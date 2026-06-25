# scheduler/run_scheduler.py
import datetime
import time
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from core.chart_crawler import run_simple_type1, run_dropdown_group, run_type2
from core.post_crawler import run_type4
from database.db_manager import log_failure
from utils.config import SIMPLE_TYPE1_APIS, DROP_GROUPS

# 全局调度器实例
scheduler = BackgroundScheduler()

# ========== 重试管理 ==========
_retry_counts = {}
MAX_RETRIES = 4
RETRY_INTERVAL_MIN = 15


def reset_retry(api_code):
    _retry_counts.pop(api_code, None)


def schedule_retry(api_code, func, *args):
    # 如果调度器未运行或已暂停，立即放弃重试
    if not scheduler.running or scheduler.state == 2:  # 2 = PAUSED
        return False
    count = _retry_counts.get(api_code, 0)
    if count >= MAX_RETRIES:
        print(f"[重试] {api_code} 已达最大重试次数，放弃")
        _retry_counts.pop(api_code, None)
        return False
    _retry_counts[api_code] = count + 1
    next_time = datetime.datetime.now() + datetime.timedelta(minutes=RETRY_INTERVAL_MIN)
    job_id = f"{api_code}_retry_{count}"
    try:
        scheduler.add_job(
            execute_with_timeout,
            args=[func, *args],
            kwargs={"api_code": api_code},
            trigger=DateTrigger(run_date=next_time),
            id=job_id,
            replace_existing=True,
            max_instances=1,
        )
        print(f"[重试] {api_code} 第{count+1}次重试已安排在 {next_time.strftime('%H:%M:%S')}")
        return True
    except (RuntimeError, Exception) as e:
        print(f"[重试] 添加任务失败: {e}")
        return False

def execute_with_timeout(func, *args, api_code, timeout_sec=120):
    """带超时的任务执行（线程池）"""
    result = False
    exception = None
    finished = threading.Event()

    def worker():
        nonlocal result, exception
        try:
            result = func(*args)
        except Exception as e:
            exception = e
        finally:
            finished.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    if not finished.wait(timeout=timeout_sec):
        print(f"[超时] {api_code} 任务超时（{timeout_sec}秒）")
        log_failure(api_code, f"timeout_{timeout_sec}s")
        schedule_retry(api_code, func, *args)
        return False

    if exception:
        print(f"[异常] {api_code}: {exception}")
        log_failure(api_code, str(exception))
        schedule_retry(api_code, func, *args)
        return False

    if not result:
        print(f"[失败] {api_code} 未获取到数据，将安排重试")
        schedule_retry(api_code, func, *args)
        return False

    reset_retry(api_code)
    return True


# ========== 任务包装函数（包含日志输出） ==========
def job_type1(api_cfg):
    api_code = api_cfg["api_code"]
    api_name = api_cfg.get("api_name", api_code)
    print(f"[调度] 开始抓取: {api_name}")
    success = execute_with_timeout(run_simple_type1, api_cfg, api_code=api_code, timeout_sec=120)
    if success:
        print(f"[成功] {api_name} 数据已入库")


def job_dropdown(group_cfg):
    group_name = group_cfg["group_name"]
    print(f"[调度] 开始抓取组: {group_name}")
    success = execute_with_timeout(run_dropdown_group, group_cfg, api_code=group_name, timeout_sec=150)
    if success:
        print(f"[成功] {group_name} 数据已入库")


def job_type2():
    api_code = "realtime_clearing"
    print(f"[调度] 执行实时接口: 实时出清参考信息")
    try:
        success = run_type2()
        if success:
            print(f"[成功] 实时出清参考信息 已更新")
        else:
            print(f"[实时] 实时出清参考信息 本次无数据")
    except Exception as e:
        print(f"[实时] 异常: {e}")
        log_failure(api_code, str(e))


def job_type4():
    print(f"[调度] 执行类型4: 机组状态")
    try:
        success = run_type4()
        if success:
            print(f"[成功] 类型4 数据已入库")
        else:
            print(f"[类型4] 本次无数据或失败")
    except Exception as e:
        print(f"[类型4] 异常: {e}")
        log_failure("type4_unit_status", str(e))


def job_auth_check():
    from auth.auth_utils import is_auth_valid
    if not is_auth_valid():
        print("[AUTH] 登录状态已失效！请重新登录。")


# 注册所有定时任务
def register_jobs():
    for api in SIMPLE_TYPE1_APIS:
        h, m = map(int, api["fetch_time"].split(":"))
        scheduler.add_job(
            job_type1,
            args=[api],
            trigger=CronTrigger(hour=h, minute=m),
            id=api["api_code"],
            replace_existing=True,
            max_instances=1,
        )

    for group in DROP_GROUPS:
        h, m = map(int, group["fetch_time"].split(":"))
        scheduler.add_job(
            job_dropdown,
            args=[group],
            trigger=CronTrigger(hour=h, minute=m),
            id=group["group_name"],
            replace_existing=True,
            max_instances=1,
        )

    scheduler.add_job(
        job_type2,
        trigger=CronTrigger(minute="*/15", second=30),
        id="realtime_clearing_job",
        replace_existing=True,
        max_instances=1,
    )

    scheduler.add_job(
        job_type4,
        trigger=CronTrigger(hour=11, minute=0),
        id="type4_unit_status",
        replace_existing=True,
        max_instances=1,
    )

    # 每天 08:55 和 17:55 检测登录状态
    scheduler.add_job(
        job_auth_check,
        trigger=CronTrigger(hour=8, minute=55),
        id="auth_check_morning",
        replace_existing=True,
    )
    scheduler.add_job(
        job_auth_check,
        trigger=CronTrigger(hour=17, minute=55),
        id="auth_check_afternoon",
        replace_existing=True,
    )


def start():
    """启动调度器（线程安全，可重复调用）"""
    if not scheduler.running:
        register_jobs()
        scheduler.start()
        print("定时调度已启动，实时接口将立即执行一次。")
        # 立即执行一次类型2，确保实时数据尽快开始
        threading.Thread(target=job_type2, daemon=True).start()
    else:
        print("调度器已在运行")

def stop():
    if scheduler.running:
        # 先暂停调度，不再触发新作业
        scheduler.pause()
        # 等待当前正在执行的任务完成（最多等待5秒）
        import time
        time.sleep(2)  # 给正在运行的任务一点时间完成
        scheduler.shutdown(wait=False)  # 不等待，直接关闭
        print("调度器已停止")
    else:
        print("调度器未在运行")


if __name__ == "__main__":
    start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop()