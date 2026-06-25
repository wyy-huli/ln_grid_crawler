from datetime import datetime, timedelta
from apscheduler.triggers.date import DateTrigger

import scheduler
from scheduler.job_config import MAX_RETRIES, execute_with_timeout

# 存储各接口当前重试次数 {api_code: count}
_retry_counts = {}

def schedule_retry(api_code, func, *args):
    if not scheduler.running:
        return False
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
        args=[func, *args],
        kwargs={"api_code": api_code},
        trigger=DateTrigger(run_date=next_time),
        id=job_id,
        replace_existing=True,
        max_instances=1,
    )
    print(f"[重试] {api_code} 第{count+1}次重试已安排在 {next_time.strftime('%H:%M:%S')}")
    return True

def reset_retry_count(api_code):
    _retry_counts.pop(api_code, None)