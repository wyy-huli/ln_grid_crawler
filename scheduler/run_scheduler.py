# scheduler/run_scheduler.py
import datetime
import time
import threading
import traceback
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

from database.db_manager import log_failure
from utils.config import SIMPLE_TYPE1_APIS, DROP_GROUPS
from core.browser_guard import run_in_subprocess, check_and_kill_chrome_if_needed
from utils.logger import logger

# 全局调度器实例
scheduler = BackgroundScheduler(daemon=True)

# 浏览器任务串行化锁：同一时间只能有一个 Playwright 任务执行，
# 避免 Type2 与 Type1/Dropdown 同时启动多个 Chrome 导致资源耗尽
_browser_task_lock = threading.Lock()

# 浏览器锁等待超时（秒）：抢不到锁就跳过本次任务，避免任务堆积
_BROWSER_LOCK_WAIT_SEC = 5

# 不走重试的接口：
# - realtime_clearing: 高频任务（每15分钟），下次 cron 会自动触发
# - type4_unit_status: 原设计为"无数据时不重试"
_NO_RETRY_APIS = {"realtime_clearing", "type4_unit_status"}

# ========== 重试管理 ==========
_retry_counts = {}
MAX_RETRIES = 4
RETRY_INTERVAL_MIN = 15


def reset_retry(api_code):
    _retry_counts.pop(api_code, None)


def schedule_retry(api_code, func_path, timeout_sec=120, *args):
    # 高频任务跳过重试，避免浏览器堆积
    if api_code in _NO_RETRY_APIS:
        logger.warning(f"[重试] {api_code} 配置为不重试，跳过")
        return False
    # 如果调度器未运行或已暂停，立即放弃重试
    if not scheduler.running or scheduler.state == 2:  # 2 = PAUSED
        return False
    count = _retry_counts.get(api_code, 0)
    if count >= MAX_RETRIES:
        logger.info(f"[重试] {api_code} 已达最大重试次数，放弃")
        _retry_counts.pop(api_code, None)
        return False
    _retry_counts[api_code] = count + 1
    next_time = datetime.datetime.now() + datetime.timedelta(minutes=RETRY_INTERVAL_MIN)
    job_id = f"{api_code}_retry_{count}"
    try:
        scheduler.add_job(
            _run_browser_task,
            args=[func_path, api_code, timeout_sec, args],
            trigger=DateTrigger(run_date=next_time),
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )
        logger.info(f"[重试] {api_code} 第{count+1}次重试已安排在 {next_time.strftime('%H:%M:%S')}")
        return True
    except Exception as e:
        logger.error(f"[重试] 添加任务失败: {e}")
        return False


def _run_browser_task(func_path, api_code, timeout_sec, args=None):
    """所有浏览器（Playwright）任务的统一入口。

    先抢全局浏览器锁，确保同一时间只有一个 Playwright 任务运行，
    避免多个 Chrome 实例并发导致资源耗尽/卡死。
    抢不到锁则跳过本次任务（高频任务下一周期会再触发）。
    任务通过进程级隔离执行（browser_guard.run_in_subprocess），
    超时后强制 kill 进程和所有 Chrome 子进程，彻底防止浏览器泄漏。
    """
    # 执行前先检查 Chrome 进程数，防止系统资源耗尽
    try:
        check_and_kill_chrome_if_needed()
    except Exception:
        pass

    acquired = _browser_task_lock.acquire(timeout=_BROWSER_LOCK_WAIT_SEC)
    if not acquired:
        logger.warning(f"[跳过] {api_code} 等待浏览器锁超时（{_BROWSER_LOCK_WAIT_SEC}s），本次跳过")
        try:
            log_failure(api_code, "browser_lock_timeout")
        except Exception:
            pass
        return False
    try:
        # 使用进程级隔离执行，超时强制 kill 进程
        result = run_in_subprocess(
            func_path,
            timeout_sec=timeout_sec,
            args=args or (),
            kill_chrome_on_timeout=True,
        )
        return result
    except Exception as e:
        logger.error(f"[异常] _run_browser_task {api_code}: {e}")
        traceback.print_exc()
        return False
    finally:
        try:
            _browser_task_lock.release()
        except Exception:
            pass
        # 执行后再检查一次 Chrome 进程
        try:
            check_and_kill_chrome_if_needed()
        except Exception:
            pass


# ========== 任务包装函数（全部 try-except 兜底） ==========
def job_type1(api_cfg):
    api_code = api_cfg["api_code"]
    api_name = api_cfg.get("api_name", api_code)
    logger.info(f"[调度] 开始抓取: {api_name}")
    try:
        success = _run_browser_task(
            "core.chart_crawler.run_simple_type1",
            api_code=api_code,
            timeout_sec=120,
            args=(api_cfg,),
        )
        if success:
            logger.info(f"[成功] {api_name} 数据已入库")
            reset_retry(api_code)
        else:
            logger.error(f"[失败] {api_name} 抓取失败或无数据")
            try:
                schedule_retry(api_code, "core.chart_crawler.run_simple_type1", 120, api_cfg)
            except Exception as e:
                logger.error(f"[失败] schedule_retry 异常（吞掉）: {e}")
    except Exception as e:
        logger.error(f"[JOB异常] job_type1 {api_code}: {e}")
        traceback.print_exc()


def job_dropdown(group_cfg):
    group_name = group_cfg["group_name"]
    logger.info(f"[调度] 开始抓取组: {group_name}")
    try:
        success = _run_browser_task(
            "core.chart_crawler.run_dropdown_group",
            api_code=group_name,
            timeout_sec=150,
            args=(group_cfg,),
        )
        if success:
            logger.info(f"[成功] {group_name} 数据已入库")
            reset_retry(group_name)
        else:
            logger.error(f"[失败] {group_name} 抓取失败或无数据")
            try:
                schedule_retry(group_name, "core.chart_crawler.run_dropdown_group", 150, group_cfg)
            except Exception as e:
                logger.error(f"[失败] schedule_retry 异常（吞掉）: {e}")
    except Exception as e:
        logger.error(f"[JOB异常] job_dropdown {group_name}: {e}")
        traceback.print_exc()


def job_type2():
    api_code = "realtime_clearing"
    logger.info(f"[调度] 执行实时接口: 实时出清参考信息")
    try:
        success = _run_browser_task(
            "core.chart_crawler.run_type2",
            api_code=api_code,
            timeout_sec=120,
            args=(),
        )
        if success:
            logger.info(f"[成功] 实时出清参考信息 已更新")
        else:
            logger.error(f"[失败] 实时出清参考信息 抓取失败")
    except Exception as e:
        logger.error(f"[JOB异常] job_type2 {api_code}: {e}")
        traceback.print_exc()


def job_type4():
    logger.info(f"[调度] 执行类型4: 机组状态")
    try:
        # type4 也用 Playwright，走浏览器锁串行化
        success = _run_browser_task(
            "core.post_crawler.run_type4",
            api_code="type4_unit_status",
            timeout_sec=180,
            args=(),
        )
        if success:
            logger.info(f"[成功] 类型4 数据已入库")
        else:
            logger.error(f"[类型4] 本次无数据或失败")
    except Exception as e:
        logger.error(f"[类型4] 异常: {e}")
        traceback.print_exc()
        try:
            log_failure("type4_unit_status", str(e))
        except Exception as le:
            logger.error(f"[类型4] log_failure 异常（吞掉）: {le}")


def job_auth_check():
    try:
        from auth.auth_utils import is_auth_valid
        if not is_auth_valid():
            logger.info("[AUTH] 登录状态已失效！请重新登录。")
    except Exception as e:
        logger.error(f"[AUTH] 检测异常: {e}")


# ========== 调度器事件监听（兜底捕获任何未 catch 的异常） ==========
def _on_job_error(event):
    try:
        logger.error(f"[调度异常] job_id={event.job_id} exception={event.exception}")
    except Exception:
        pass


def _on_job_missed(event):
    try:
        logger.info(f"[错过] job_id={event.job_id} 错过触发时间")
    except Exception:
        pass


# 注册所有定时任务
def register_jobs():
    # 统一容错配置：错过 60 秒内允许补跑，积压触发自动合并
    common_kwargs = dict(
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    for api in SIMPLE_TYPE1_APIS:
        h, m = map(int, api["fetch_time"].split(":"))
        scheduler.add_job(
            job_type1,
            args=[api],
            trigger=CronTrigger(hour=h, minute=m),
            id=api["api_code"],
            **common_kwargs,
        )

    for group in DROP_GROUPS:
        h, m = map(int, group["fetch_time"].split(":"))
        scheduler.add_job(
            job_dropdown,
            args=[group],
            trigger=CronTrigger(hour=h, minute=m),
            id=group["group_name"],
            **common_kwargs,
        )

    scheduler.add_job(
        job_type2,
        trigger=CronTrigger(minute="*/15", second=40),
        id="realtime_clearing_job",
        **common_kwargs,
    )

    scheduler.add_job(
        job_type4,
        trigger=CronTrigger(hour=11, minute=0),
        id="type4_unit_status",
        **common_kwargs,
    )

    # 每天 08:55 和 17:55 检测登录状态
    scheduler.add_job(
        job_auth_check,
        trigger=CronTrigger(hour=8, minute=55),
        id="auth_check_morning",
        **common_kwargs,
    )
    scheduler.add_job(
        job_auth_check,
        trigger=CronTrigger(hour=17, minute=55),
        id="auth_check_afternoon",
        **common_kwargs,
    )


def start():
    """启动调度器（线程安全，可重复调用）"""
    if not scheduler.running:
        # 注册事件监听，捕获任何漏网的异常
        scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
        scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)
        register_jobs()
        scheduler.start()
        logger.info("定时调度已启动，实时接口将立即执行一次。")
        # 立即执行一次类型2：通过 DateTrigger 让调度器自己触发，
        # 这样受 max_instances=1 约束，避免与 cron 触发重叠
        try:
            scheduler.add_job(
                job_type2,
                trigger=DateTrigger(run_date=datetime.datetime.now() + datetime.timedelta(seconds=1)),
                id="realtime_clearing_startup",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=10,
            )
        except Exception as e:
            logger.error(f"[启动] 立即触发 type2 失败: {e}")
    else:
        logger.info("调度器已在运行")

def stop():
    if scheduler.running:
        try:
            scheduler.pause()
        except Exception as e:
            logger.error(f"[停止] 暂停调度器异常: {e}")
        try:
            for job in scheduler.get_jobs():
                try:
                    scheduler.remove_job(job.id)
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[停止] 移除任务异常: {e}")
        time.sleep(2)
        try:
            scheduler.shutdown(wait=False)
        except Exception as e:
            logger.error(f"[停止] 关闭调度器异常: {e}")
        try:
            from core.browser_guard import _kill_all_chrome_processes
            _kill_all_chrome_processes()
        except Exception:
            pass
        logger.info("调度器已停止")
    else:
        logger.info("调度器未在运行")


if __name__ == "__main__":
    start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop()
