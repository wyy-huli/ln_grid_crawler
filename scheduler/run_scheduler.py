# scheduler/run_scheduler.py
import datetime
import time
import threading
import traceback
import json
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

from database.db_manager import log_failure, RetryStateRepository, cleanup_failure_logs
from utils.config import SIMPLE_TYPE1_APIS, DROP_GROUPS
from core.browser_guard import run_in_subprocess, check_and_kill_chrome_if_needed
from utils.logger import logger

# Q1-5 修复：统一时区为 Asia/Shanghai，避免部署到非上海时区环境时任务触发时间偏移
TZ_SHANGHAI = 'Asia/Shanghai'

# 全局调度器实例（指定默认时区，所有未显式指定时区的 trigger 也会用此时区）
scheduler = BackgroundScheduler(daemon=True, timezone=TZ_SHANGHAI)

# 标记调度器是否已被 shutdown（shutdown 后的调度器实例无法重启，必须重新创建）
_scheduler_shutdown = False

# 浏览器任务串行化锁：同一时间只能有一个 Playwright 任务执行，
# 避免 Type2 与 Type1/Dropdown 同时启动多个 Chrome 导致资源耗尽
_browser_task_lock = threading.Lock()

# 浏览器锁等待超时（秒）：抢不到锁就跳过本次任务，避免任务堆积
# Q1-2 修复：5s → 60s，给前序任务充分收尾时间，避免密集时段静默丢任务
_BROWSER_LOCK_WAIT_SEC = 60

# 不走重试的接口：
# - realtime_clearing: 高频任务（每15分钟），下次 cron 会自动触发
# - type4_unit_status: 原设计为"无数据时不重试"
_NO_RETRY_APIS = {"realtime_clearing", "type4_unit_status"}

# 编程类错误不重试（重试必然失败，浪费浏览器锁）
_PROGRAMMING_ERRORS = {"NameError", "AttributeError", "ImportError", "SyntaxError", "TypeError"}

# ========== 登录门控（Q1-1 修复：登录失效时不抓取，避免浪费资源） ==========
# 登录状态缓存（避免每次任务都发 HTTP 请求检查）
# None=未知, True=有效, False=无效
_auth_valid_cache = None
_auth_check_time = 0.0
_AUTH_VALID_TTL = 60     # 有效状态缓存 60s（短期内不会失效）
_AUTH_INVALID_TTL = 300  # 无效状态缓存 300s（5分钟内不重复检查，避免高频任务频繁请求）


def _set_auth_valid(valid):
    """更新登录状态缓存（供 job_auth_check 和外部登录流程调用）。"""
    global _auth_valid_cache, _auth_check_time
    _auth_valid_cache = bool(valid)
    _auth_check_time = time.time()


def _invalidate_auth_cache():
    """清除登录状态缓存（用户重新登录后调用，强制下次重新检查）。"""
    global _auth_valid_cache, _auth_check_time
    _auth_valid_cache = None
    _auth_check_time = 0.0


def _is_auth_valid_for_fetch():
    """任务执行前的登录门控检查（带缓存）。

    缓存策略：
    - 有效状态缓存 60s（短期内不会失效）
    - 无效状态缓存 300s（5分钟内不重复检查）
    - 未知/过期：调用 is_auth_valid() 实时检查

    Returns:
        bool: True=登录有效可抓取, False=登录失效应跳过
    """
    global _auth_valid_cache, _auth_check_time
    now = time.time()
    # 缓存有效期内直接返回
    if _auth_valid_cache is True and (now - _auth_check_time) < _AUTH_VALID_TTL:
        return True
    if _auth_valid_cache is False and (now - _auth_check_time) < _AUTH_INVALID_TTL:
        return False
    # 缓存过期或未知，重新检查
    try:
        from auth.auth_utils import is_auth_valid
        valid = is_auth_valid()
        _auth_valid_cache = valid
        _auth_check_time = now
        return valid
    except Exception as e:
        # 检查异常不阻断任务（保守策略：让任务尝试执行，避免误杀）
        logger.warning(f"[AUTH] 登录检查异常（放行）: {e}")
        return True


def _check_auth_or_skip(api_code, display, high_freq=False):
    """登录门控：失效时返回 True（应跳过），有效时返回 False（继续执行）。

    Args:
        api_code: 接口标识（用于 failure_log）
        display: 显示名称（用于日志）
        high_freq: 是否高频任务（True 时不记录 failure_log，避免日志爆炸）

    Returns:
        bool: True=应跳过, False=可继续
    """
    if _is_auth_valid_for_fetch():
        return False  # 登录有效，不跳过
    logger.warning(f"[跳过] {display} 登录已失效，跳过本次抓取")
    if not high_freq:
        try:
            log_failure(api_code, "auth_invalid")
        except Exception:
            pass
    return True  # 应跳过


# ========== 重试管理（Q0-3 修复：状态持久化到 retry_state 表，重启不丢失） ==========
MAX_RETRIES = 4
RETRY_INTERVAL_MIN = 15  # 基础间隔（分钟），实际按指数退避计算


def _calc_retry_interval_min(retry_count):
    """Q2-2 修复：计算指数退避重试间隔（分钟）。

    退避策略：基础间隔 * 2^(retry_count-1)，即 15/30/60/120 分钟
    加 ±10% 随机抖动，避免多个 api 同时重试造成峰值。

    Args:
        retry_count: 第几次重试（1~4）

    Returns:
        float: 重试间隔（分钟）
    """
    import random
    base = RETRY_INTERVAL_MIN * (2 ** (retry_count - 1))
    # ±10% 抖动
    jitter = base * 0.1 * (random.random() * 2 - 1)
    return base + jitter


def reset_retry(api_code):
    """任务成功后清除重试状态（Q0-3 修复：从内存字典改为数据库删除）。"""
    RetryStateRepository.delete(api_code)


def schedule_retry(api_code, func_path, timeout_sec=120, *args):
    # 高频任务跳过重试，避免浏览器堆积
    if api_code in _NO_RETRY_APIS:
        logger.warning(f"[重试] {api_code} 配置为不重试，跳过")
        return False
    # 编程错误不重试（重试必然失败，浪费浏览器资源）
    # Q0-3 修复：异常类型从 retry_state 表读取（持久化，重启后仍生效）
    last_exc = RetryStateRepository.get_last_exc_type(api_code)
    if last_exc in _PROGRAMMING_ERRORS:
        logger.warning(f"[重试] {api_code} 检测到编程错误 ({last_exc})，跳过重试")
        RetryStateRepository.delete(api_code)
        return False
    # 如果调度器未运行或已暂停，立即放弃重试
    if not scheduler.running or scheduler.state == 2:  # 2 = PAUSED
        return False
    # Q0-3 修复：重试计数从 retry_state 表读取（持久化，重启不归零）
    count = RetryStateRepository.get_retry_count(api_code)
    if count >= MAX_RETRIES:
        logger.info(f"[重试] {api_code} 已达最大重试次数，放弃")
        RetryStateRepository.delete(api_code)
        return False
    next_count = count + 1
    # Q2-2 修复：指数退避 + 抖动，避免固定间隔持续打爆服务器
    interval_min = _calc_retry_interval_min(next_count)
    next_time = datetime.datetime.now() + datetime.timedelta(minutes=interval_min)
    job_id = f"retry_{api_code}_{next_count}"
    try:
        # Q0-1 修复：重试任务调用 _execute_with_retry（含重试链路）而非 _run_browser_task（无重试链路）
        # 否则重试任务失败后无人继续触发 schedule_retry，4 次重试实际只生效 1 次
        scheduler.add_job(
            _execute_with_retry,
            args=[func_path, api_code, timeout_sec, args, True, None],
            trigger=DateTrigger(run_date=next_time, timezone='Asia/Shanghai'),
            id=job_id,
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        # Q0-3 修复：持久化重试状态到数据库（重启后可恢复）
        try:
            args_json = json.dumps(list(args), default=str) if args else None
        except Exception:
            args_json = None
        RetryStateRepository.upsert(
            api_code=api_code,
            retry_count=next_count,
            scheduled_next_time=next_time,
            job_id=job_id,
            func_path=func_path,
            timeout_sec=timeout_sec,
            args_json=args_json,
        )
        logger.info(f"[重试] {api_code} 第{next_count}次重试已安排在 {next_time.strftime('%H:%M:%S')}（间隔{interval_min:.1f}分钟）")
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
        exc_info = {}
        result = run_in_subprocess(
            func_path,
            timeout_sec=timeout_sec,
            args=args or (),
            kill_chrome_on_timeout=True,
            exc_info=exc_info,
        )
        # Q0-3 修复：异常类型持久化到 retry_state 表（替代内存字典 _last_exc_type_map）
        # 注意：只在有异常时写入，无异常不清除（成功时由 reset_retry 统一清除整条记录）
        exc_type = exc_info.get('type')
        if exc_type:
            RetryStateRepository.upsert(api_code, last_exc_type=exc_type)
        return result
    except Exception as e:
        logger.error(f"[异常] _run_browser_task {api_code}: {e}")
        logger.error(traceback.format_exc())
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

def _execute_with_retry(func_path, api_code, timeout_sec, args=None,
                         retry_enabled=True, api_name=None):
    """统一的"执行+重试"入口（Q0-1 修复）。

    cron 触发的任务和重试触发的任务都走此入口，确保重试任务失败后也能继续触发重试，
    形成 retry_1 → retry_2 → retry_3 → retry_4 的完整重试链路。

    修复前：重试任务调用 _run_browser_task（纯执行器，无重试逻辑），失败后无人继续触发重试
    修复后：重试任务调用本函数，失败后再次调用 schedule_retry，形成完整重试链路

    Args:
        func_path: 浏览器任务函数路径，如 'core.chart_crawler.run_simple_type1'
        api_code: 接口标识（用于日志、重试计数、失败日志）
        timeout_sec: 子进程超时秒数
        args: 传给 func_path 的位置参数元组
        retry_enabled: 是否启用重试（type2/type4 不重试）
        api_name: 显示名称（可选，用于日志）
    """
    display = api_name or api_code
    # Q1-1 修复：登录门控——登录失效时跳过抓取，不触发重试（重试也会失败）
    if _check_auth_or_skip(api_code, display):
        return False
    logger.info(f"[调度] 开始抓取: {display}")
    try:
        success = _run_browser_task(
            func_path,
            api_code=api_code,
            timeout_sec=timeout_sec,
            args=args or (),
        )
        if success:
            logger.info(f"[成功] {display} 数据已入库")
            reset_retry(api_code)
            return True
        else:
            logger.error(f"[失败] {display} 抓取失败或无数据")
            if retry_enabled:
                try:
                    schedule_retry(api_code, func_path, timeout_sec, *(args or ()))
                except Exception as e:
                    logger.error(f"[失败] schedule_retry 异常（吞掉）: {e}")
            return False
    except Exception as e:
        logger.error(f"[JOB异常] {api_code}: {e}")
        logger.error(traceback.format_exc())
        return False


def job_type1(api_cfg):
    """类型1任务包装：调用统一入口（Q0-1 修复后改为薄包装）"""
    _execute_with_retry(
        func_path="core.chart_crawler.run_simple_type1",
        api_code=api_cfg["api_code"],
        timeout_sec=120,
        args=(api_cfg,),
        api_name=api_cfg.get("api_name", api_cfg["api_code"]),
    )


def job_dropdown(group_cfg):
    """下拉组任务包装：调用统一入口（Q0-1 修复后改为薄包装）

    第三层修复：timeout_sec 按选项数动态计算。
    原逻辑：下拉组统一 150s，2选项下拉组（如新能源总出力=风电+光伏）最坏 90×2+入库+close≈200s，
            远超 150s，频繁触发超时判失败。
    新逻辑：90s × 选项数 + 60s余量。
            2选项=240s，1选项=150s（保持兼容），6选项=600s（未来扩展）。
    """
    option_count = len(group_cfg.get("options", []))
    timeout_sec = 90 * option_count + 60
    _execute_with_retry(
        func_path="core.chart_crawler.run_dropdown_group",
        api_code=group_cfg["group_name"],
        timeout_sec=timeout_sec,
        args=(group_cfg,),
        api_name=group_cfg["group_name"],
    )


def job_type2():
    api_code = "realtime_clearing"
    # Q1-1 修复：登录门控（高频任务静默跳过，不记 failure_log）
    if _check_auth_or_skip(api_code, "实时出清参考信息", high_freq=True):
        return
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
        logger.error(traceback.format_exc())


def job_type4():
    # Q1-1 修复：登录门控
    if _check_auth_or_skip("type4_unit_status", "机组状态"):
        return
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
        logger.error(traceback.format_exc())
        try:
            log_failure("type4_unit_status", str(e))
        except Exception as le:
            logger.error(f"[类型4] log_failure 异常（吞掉）: {le}")


def job_auth_check():
    try:
        from auth.auth_utils import is_auth_valid
        valid = is_auth_valid()
        # Q1-1 修复：同步更新缓存，让任务门控立即可用
        _set_auth_valid(valid)
        if not valid:
            logger.info("[AUTH] 登录状态已失效！请重新登录。")
        else:
            logger.info("[AUTH] 登录状态有效")
    except Exception as e:
        logger.error(f"[AUTH] 检测异常: {e}")


def job_cleanup_failure_logs():
    """Q1-7 修复：定期清理过期失败日志，防止 fetch_failure_log 表无限膨胀。"""
    logger.info("[清理] 开始清理过期失败日志")
    try:
        deleted = cleanup_failure_logs(days_to_keep=30)
        logger.info(f"[清理] 失败日志清理完成，删除 {deleted} 条")
    except Exception as e:
        logger.error(f"[清理] 失败日志清理异常: {e}")


# ========== 调度器事件监听（兜底捕获任何未 catch 的异常） ==========
def _on_job_error(event):
    try:
        logger.error(f"[调度异常] job_id={event.job_id} exception={event.exception}")
    except Exception:
        pass


def _on_job_missed(event):
    """Q1-2 修复：misfire 不再静默丢弃，写 failure_log 让运维可追溯"""
    try:
        job_id = event.job_id
        # 从 job_id 反查 api_code
        api_code = job_id
        if job_id.startswith("retry_"):
            # retry_{api_code}_{retry_no}
            parts = job_id.split("_")
            api_code = "_".join(parts[1:-1]) if len(parts) > 2 else job_id
        elif job_id.endswith("_job"):
            api_code = job_id[:-4]
        elif job_id.endswith("_startup"):
            # 启动时立即触发的任务（如 realtime_clearing_startup）
            api_code = job_id[:-8]  # 去掉 "_startup" 后缀（8字符）

        logger.warning(f"[错过] job_id={job_id} api_code={api_code} 错过触发时间，已记录失败日志")
        try:
            log_failure(api_code, "scheduler_misfire")
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[错过] 处理 misfire 事件异常: {e}")


# 注册所有定时任务
def register_jobs():
    # Q1-2 修复：misfire_grace_time 60s → 300s（5 分钟容错）
    # 避免密集时段（09:00-10:13）前序任务慢导致后序任务静默丢弃
    common_kwargs = dict(
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=300,
    )
    for api in SIMPLE_TYPE1_APIS:
        h, m = map(int, api["fetch_time"].split(":"))
        scheduler.add_job(
            job_type1,
            args=[api],
            trigger=CronTrigger(hour=h, minute=m, timezone=TZ_SHANGHAI),
            id=api["api_code"],
            **common_kwargs,
        )

    for group in DROP_GROUPS:
        h, m = map(int, group["fetch_time"].split(":"))
        scheduler.add_job(
            job_dropdown,
            args=[group],
            trigger=CronTrigger(hour=h, minute=m, timezone=TZ_SHANGHAI),
            id=group["group_name"],
            **common_kwargs,
        )

    scheduler.add_job(
        job_type2,
        trigger=CronTrigger(minute="*/15", second=40, timezone=TZ_SHANGHAI),
        id="realtime_clearing_job",
        **common_kwargs,
    )

    scheduler.add_job(
        job_type4,
        trigger=CronTrigger(hour=11, minute=0, timezone=TZ_SHANGHAI),
        id="type4_unit_status",
        **common_kwargs,
    )

    # 每天 08:55 和 17:55 检测登录状态
    scheduler.add_job(
        job_auth_check,
        trigger=CronTrigger(hour=8, minute=55, timezone=TZ_SHANGHAI),
        id="auth_check_morning",
        **common_kwargs,
    )
    scheduler.add_job(
        job_auth_check,
        trigger=CronTrigger(hour=17, minute=55, timezone=TZ_SHANGHAI),
        id="auth_check_afternoon",
        **common_kwargs,
    )

    # Q1-7 修复：每天 03:00 清理过期失败日志（保留 30 天），低峰时段执行
    scheduler.add_job(
        job_cleanup_failure_logs,
        trigger=CronTrigger(hour=3, minute=0, timezone=TZ_SHANGHAI),
        id="cleanup_failure_logs",
        **common_kwargs,
    )


def _restore_pending_retries():
    """恢复重启前未完成的重试任务（Q0-3 修复核心）。

    从 retry_state 表读取所有 scheduled_next_time 不为空的记录，
    重新注册到调度器。已过期的任务立即触发（misfire 容错）。

    注意事项：
    - 必须在 scheduler.start() 之后调用（调度器已就绪）
    - 恢复后清除 scheduled_next_time（避免重复恢复）
    - 恢复的任务仍走 _execute_with_retry，失败后能继续触发重试链路
    """
    try:
        pending = RetryStateRepository.list_pending()
        if not pending:
            return
        logger.info(f"[恢复] 发现 {len(pending)} 个未完成的重试任务，开始恢复")
        now = datetime.datetime.now()
        restored = 0
        for state in pending:
            try:
                # 判断重试时间是否已过期
                if state.scheduled_next_time and state.scheduled_next_time > now:
                    # 未来时间：按原计划重新安排
                    run_date = state.scheduled_next_time
                else:
                    # 已过期：10 秒后立即触发（misfire 容错）
                    run_date = now + datetime.timedelta(seconds=10)

                # 重建参数
                try:
                    args_list = json.loads(state.args_json) if state.args_json else []
                except Exception:
                    args_list = []
                func_path = state.func_path
                if not func_path:
                    logger.warning(f"[恢复] {state.api_code} 缺少 func_path，跳过")
                    continue
                timeout_sec = state.timeout_sec or 120
                api_code = state.api_code
                job_id = state.job_id or f"retry_{api_code}_{state.retry_count}"

                # 重新安排任务（走 _execute_with_retry，保持重试链路）
                scheduler.add_job(
                    _execute_with_retry,
                    args=[func_path, api_code, timeout_sec, tuple(args_list), True, None],
                    trigger=DateTrigger(run_date=run_date, timezone=TZ_SHANGHAI),
                    id=job_id,
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=300,
                )
                # 清除 scheduled_next_time（避免下次启动重复恢复）
                # 注意：不删除整条记录，保留 retry_count/last_exc_type 供重试判定
                RetryStateRepository.clear_scheduled(api_code)
                restored += 1
                logger.info(f"[恢复] {api_code} 第{state.retry_count}次重试 → {run_date.strftime('%H:%M:%S')}")
            except Exception as e:
                logger.error(f"[恢复] {state.api_code} 恢复失败: {e}")
        logger.info(f"[恢复] 共恢复 {restored}/{len(pending)} 个重试任务")
    except Exception as e:
        logger.error(f"[恢复] 恢复重试任务异常: {e}")


def start():
    """启动调度器（线程安全，可重复调用）"""
    global scheduler, _scheduler_shutdown
    if not scheduler.running:
        # APScheduler 的 shutdown() 会永久关闭内部 ThreadPoolExecutor，
        # shutdown 后的调度器实例无法重启（报 "cannot schedule new futures after shutdown"）。
        # 修复：检测调度器是否曾被 shutdown，如果是则重新创建实例。
        if _scheduler_shutdown:
            scheduler = BackgroundScheduler(daemon=True, timezone=TZ_SHANGHAI)
            _scheduler_shutdown = False
            logger.info("[启动] 检测到调度器曾被关闭，已重新创建调度器实例")

        # 清理上次闪退可能残留的 Chrome 进程（Q0-2 精准清理，不影响用户 Chrome）
        try:
            from core.browser_guard import _kill_all_chrome_processes
            _kill_all_chrome_processes()
        except Exception as e:
            logger.warning(f"[启动] 清理上次残留 Chrome 异常（忽略）: {e}")

        # 注册事件监听，捕获任何漏网的异常
        scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)
        scheduler.add_listener(_on_job_missed, EVENT_JOB_MISSED)
        register_jobs()
        scheduler.start()
        logger.info("定时调度已启动，实时接口将立即执行一次。")
        # Q0-3 修复：恢复重启前未完成的重试任务
        _restore_pending_retries()
        # 立即执行一次类型2：通过 DateTrigger 让调度器自己触发，
        # 这样受 max_instances=1 约束，避免与 cron 触发重叠
        try:
            scheduler.add_job(
                job_type2,
                trigger=DateTrigger(run_date=datetime.datetime.now() + datetime.timedelta(seconds=1),
                                    timezone=TZ_SHANGHAI),
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
    """停止调度器（Q1-4 修复：优雅退出 + 精准清理）。

    修复前问题：
    1. 手动遍历 remove_job 多余（shutdown 自动清理）
    2. time.sleep(2) 硬编码等待无意义
    3. _kill_all_chrome_processes 阶段一已改造为精准清理（不误杀用户 Chrome）

    修复后流程：
    1. pause() 暂停调度器，不再触发新任务
    2. shutdown(wait=False) 关闭调度器（正在执行的线程会自然完成，由 browser_guard 超时机制保障不卡死）
    3. _kill_all_chrome_processes 精准清理本程序残留 Chrome
    4. 清除 AuthGate 缓存（下次启动重新检查登录状态）

    注意：重试任务状态已由 Q0-3 持久化到 retry_state 表，stop() 不再需要特殊处理重试任务，
    下次 start() 时 _restore_pending_retries 会自动恢复未完成的重试任务。
    """
    global _scheduler_shutdown
    if not scheduler.running:
        logger.info("调度器未在运行")
        return

    logger.info("[停止] 开始停止调度器...")

    # 1. 暂停调度器（不再触发新任务）
    try:
        scheduler.pause()
        logger.info("[停止] 调度器已暂停，不再触发新任务")
    except Exception as e:
        logger.error(f"[停止] 暂停调度器异常: {e}")

    # 2. 关闭调度器（不等待正在执行的线程，线程会自然完成）
    #    注：正在执行的 job 在子进程中运行，由 browser_guard 的超时机制保障不会卡死
    try:
        scheduler.shutdown(wait=False)
        logger.info("[停止] 调度器已关闭")
    except Exception as e:
        logger.error(f"[停止] 关闭调度器异常: {e}")
    # 无论 shutdown 成功或异常，都标记为已关闭，确保下次 start 能重建实例
    _scheduler_shutdown = True

    # 3. 精准清理本程序残留 Chrome（阶段一已改造为不误杀用户 Chrome）
    try:
        from core.browser_guard import _kill_all_chrome_processes
        _kill_all_chrome_processes()
    except Exception as e:
        logger.warning(f"[停止] 清理 Chrome 异常: {e}")

    # 4. 清除 AuthGate 缓存（下次启动重新检查登录状态）
    try:
        _invalidate_auth_cache()
    except Exception:
        pass

    logger.info("[停止] 调度器已完全停止")


# ========== 重试/任务查询 ==========

def get_retry_jobs():
    """查询当前所有待执行的重试任务列表。

    Returns:
        list[dict]: 每个元素包含：
            - job_id: 任务ID
            - api_code: 接口标识
            - retry_no: 第几次重试 (1~4)
            - next_run_time: 下次执行时间 (datetime，为 None 表示暂停)
            - func_path: 执行的函数路径
            - status: pending / 描述 pending/paused/unknown
    """
    result = []
    try:
        jobs = scheduler.get_jobs()
    except Exception as e:
        logger.warning(f"[重试查询] 获取调度器任务列表失败: {e}")
        return result
    for job in jobs:
        if not job.id or not job.id.startswith("retry_"):
            continue
        parts = job.id.split("_")
        # retry_{api_code}_{retry_no}
        # api_code 本身可能含下划线，按"最后一段为次数 + 中间全部是 api_code
        try:
            retry_no = int(parts[-1]) if parts[-1].isdigit() else -1
            api_code = "_".join(parts[1:-1]) if len(parts) > 2 else job.id
        except Exception:
            api_code = job.id
            retry_no = -1

        next_run = job.next_run_time
        status = "paused" if next_run is None else "pending"

        try:
            ref = job.func_ref
            func_path = f"{ref[0]}.{ref[1]}"
        except Exception:
            func_path = str(job.func)

        result.append({
            "job_id": job.id,
            "api_code": api_code,
            "retry_no": retry_no,
            "next_run_time": next_run,
            "func_path": func_path,
            "status": status,
        })
    # 按下次执行时间排序，None（暂停）放最后
    result.sort(key=lambda x: (x["next_run_time"] is None, x["next_run_time"] or datetime.datetime.max))
    return result


def list_retry_jobs(log=True):
    """打印并重试任务日志中输出，同时返回数量。

    Args:
        log: 是否输出到 logger

    Returns:
        tuple: (总重试数, 列表)
    """
    jobs = get_retry_jobs()
    total = len(jobs)

    if not log:
        return total, jobs

    if total == 0:
        logger.info("[重试查询] 当前没有待执行的重试任务")
        return total, jobs

    logger.info(f"[重试查询] 当前共有 {total} 个待执行的重试任务:")
    for j in jobs:
        nxt = j["next_run_time"].strftime("%Y-%m-%d %H:%M:%S") if j["next_run_time"] else "-"
        logger.info(f"  - {j['api_code']}  第{j['retry_no']}次  下次={nxt}  状态={j['status']}")
    return total, jobs


def get_all_jobs_summary():
    """获取所有已注册任务简略（含定时任务 + 立即触发 + 重试）的概要，用于 UI 状态展示。

    Returns:
        dict: {cron: [...], retry: [...], startup: [...]
    """
    try:
        jobs = scheduler.get_jobs()
    except Exception:
        return {"cron": [], "retry": [], "startup": []}
    cron_jobs = []
    retry_jobs = []
    startup_jobs = []
    for job in jobs:
        info = {
            "id": job.id,
            "next_run": job.next_run_time.strftime("%H:%M:%S") if job.next_run_time else "-",
        }
        if job.id.startswith("retry_"):
            retry_jobs.append(info)
        elif "startup" in job.id.lower():
            startup_jobs.append(info)
        else:
            cron_jobs.append(info)
    return {"cron": cron_jobs, "retry": retry_jobs, "startup": startup_jobs}


if __name__ == "__main__":
    start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop()
