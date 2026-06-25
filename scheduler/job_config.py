# 任务配置与调度
# scheduler/job_config.py
import datetime
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from core.chart_crawler import (
    run_simple_type1,
    run_dropdown_group,
    run_type2,
)
from core.browser_manager import browser_pool
from database.db_manager import log_failure
from utils.config import SIMPLE_TYPE1_APIS, DROP_GROUPS

# ========== 全局调度器实例 ==========
scheduler = BackgroundScheduler()

# ========== 重试状态管理 ==========
_retry_counts = {}          # {api_code: 当前重试次数}
MAX_RETRIES = 4
RETRY_INTERVAL_MIN = 15

def reset_retry(api_code):
    _retry_counts.pop(api_code, None)

def schedule_retry(api_code, func, *args):
    """安排一次重试，返回是否成功安排"""
    count = _retry_counts.get(api_code, 0)
    if count >= MAX_RETRIES:
        print(f"[重试] {api_code} 已达最大重试次数，放弃")
        _retry_counts.pop(api_code, None)
        return False
    _retry_counts[api_code] = count + 1
    next_time = datetime.datetime.now() + datetime.timedelta(minutes=RETRY_INTERVAL_MIN)
    job_id = f"{api_code}_retry_{count}"
    scheduler.add_job(
        execute_with_timeout,
        args=[func, *args, api_code],
        trigger=DateTrigger(run_date=next_time),
        id=job_id,
        replace_existing=True,
        max_instances=1,
    )
    print(f"[重试] {api_code} 第{count+1}次重试已安排在 {next_time.strftime('%H:%M:%S')}")
    return True

# ========== 超时执行器 ==========
def execute_with_timeout(func, *args, api_code, timeout_sec=120):
    """
    在独立线程中运行 func，超时则视为失败。
    api_code 用于日志和重试。
    """
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
        print(f"[超时] {api_code} 任务执行超过 {timeout_sec} 秒，强制结束")
        log_failure(api_code, f"timeout_{timeout_sec}s")
        schedule_retry(api_code, func, *args)
        return False

    if exception:
        print(f"[异常] {api_code}: {exception}")
        log_failure(api_code, str(exception))
        schedule_retry(api_code, func, *args)
        return False

    if not result:
        # 业务失败（如 no_data），也安排重试
        print(f"[失败] {api_code} 未获取到数据，将安排重试")
        schedule_retry(api_code, func, *args)
        return False

    reset_retry(api_code)
    return True

# ========== 任务包装函数 ==========
def job_type1(api_cfg):
    """普通类型1接口任务"""
    api_code = api_cfg["api_code"]
    print(f"[调度] 开始执行: {api_cfg['api_name']} ({api_code})")
    execute_with_timeout(run_simple_type1, api_cfg, api_code=api_code, timeout_sec=120)

def job_dropdown(group_cfg):
    """下拉组任务（整体执行）"""
    group_id = group_cfg["group_name"]
    print(f"[调度] 开始执行组: {group_id}")
    execute_with_timeout(run_dropdown_group, group_cfg, api_code=group_id, timeout_sec=150)

def job_type2():
    api_code = "realtime_clearing"
    print(f"[调度] 执行实时接口: {api_code}")
    try:
        success = run_type2()
        if not success:
            print(f"[实时] {api_code} 未获取到数据")
    except Exception as e:
        print(f"[实时] {api_code} 异常: {e}")
        log_failure(api_code, str(e))

def job_restart_browser():
    """每日凌晨2点重启浏览器"""
    print("[维护] 正在重启浏览器...")
    browser_pool.restart_browser()
    print("[维护] 浏览器已重启")

def job_auth_check():
    """登录状态检测（每天 08:55, 17:55）"""
    from auth.auth_utils import is_auth_valid
    if not is_auth_valid():
        print("[AUTH] 登录状态已失效，请重新登录！")
    else:
        print("[AUTH] 登录状态正常")

# ========== 注册所有定时任务 ==========
def register_all_jobs():
    """将配置中的接口任务添加到调度器"""
    # 普通类型1接口
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

    # 下拉任务组
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

    # 类型2 实时接口（每15分钟）
    scheduler.add_job(
        job_type2,
        trigger=CronTrigger(minute="*/15", second=0),
        id="realtime_clearing_job",
        replace_existing=True,
        max_instances=1,
    )

    # 每日维护：凌晨2点重启浏览器
    scheduler.add_job(
        job_restart_browser,
        trigger=CronTrigger(hour=2, minute=0),
        id="restart_browser",
        replace_existing=True,
    )

    # Auth 检测：08:55 和 17:55
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