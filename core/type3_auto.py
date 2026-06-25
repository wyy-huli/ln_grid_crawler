# core/type3_auto.py
import json
import time
import datetime
import requests
from auth.auth_utils import is_auth_valid
from database.db_manager import save_type3_query
from utils.config import TYPE3_MEMBER_URL, TYPE3_CONS_URL, TYPE3_QUERY_URL, AUTH_FILE

def _build_headers(cookies, target_url):
    """从 cookies 和 localStorage 构建请求头"""
    with open(AUTH_FILE, 'r') as f:
        storage = json.load(f)
    x_uid = '2217636810955'
    for origin in storage.get('origins', []):
        for item in origin.get('localStorage', []):
            if item['name'] in ('userId', 'id'):
                x_uid = item['value']
                break
    timestamp_ms = str(int(time.time() * 1000))
    route = f"/pxf-settlement-outnetpub-gs/lnInformationDelivery/lnInfoIpYxXhFsPq?date={timestamp_ms}"
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "X-Ticket": cookies.get("X-Ticket", ""),
        "ClientTag": "OUTNET_BROWSE",
        "CurrentRoute": route,
        "Referer": "https://pmos.ln.sgcc.com.cn/pxf-settlement-outnetpub-gs/",
        "X-Token": cookies.get("X-Token", "undefined"),
        "x-uid": x_uid,
    }
    return headers

def auto_fetch_type3(start_date, end_date, progress_callback=None, log_callback=None):
    """
    自动抓取所有市场主体和用电编号的数据，日期范围为 [start_date, end_date]。
    progress_callback(current, total, message): 用于更新进度。
    log_callback(message): 用于输出日志。
    返回成功抓取次数和失败列表。
    """
    if not is_auth_valid():
        if log_callback:
            log_callback("登录状态已失效，请重新登录后再执行")
        return 0, ["auth_expired"]

    # 加载 cookies
    with open(AUTH_FILE, 'r') as f:
        storage = json.load(f)
    cookies = {c['name']: c['value'] for c in storage.get('cookies', [])}

    # 1. 获取市场主体列表
    headers = _build_headers(cookies, TYPE3_MEMBER_URL)
    try:
        resp = requests.post(TYPE3_MEMBER_URL, cookies=cookies, headers=headers, json={}, timeout=30)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")
        data = resp.json()
        if data.get('status') != 0:
            raise Exception(data.get('message', '获取主体失败'))
        members = data.get('data', [])
        if not members:
            raise Exception("未获取到市场主体")
    # 获取市场主体失败
    except Exception as e:
        if log_callback:
            log_callback(f"获取市场主体失败: {e}")
        return 0, [("系统", "", "", str(e))]

    total_members = len(members)
    success_count = 0
    failures = []

    date_range = []
    d = datetime.datetime.strptime(start_date, '%Y-%m-%d').date()
    end = datetime.datetime.strptime(end_date, '%Y-%m-%d').date()
    while d <= end:
        date_range.append(d.strftime('%Y-%m-%d'))
        d += datetime.timedelta(days=1)

    for idx_m, member in enumerate(members):
        mid = member['powerMembersId']
        mname = member['powerMembersName']
        if log_callback:
            log_callback(f"处理主体 [{idx_m+1}/{total_members}]: {mname}")

        # 2. 获取用电编号
        cons_headers = _build_headers(cookies, TYPE3_CONS_URL)
        try:
            cons_payload = {
                "data": {"consNo": "", "mid": [mid], "infoDate": start_date},
                "pageInfo": {"pageNum": 1, "pageSize": 1000, "total": 0}
            }
            resp = requests.post(TYPE3_CONS_URL, cookies=cookies, headers=cons_headers, json=cons_payload, timeout=30)
            if resp.status_code != 200:
                raise Exception(f"HTTP {resp.status_code}")
            cons_data = resp.json()
            if cons_data.get('status') != 0:
                raise Exception(cons_data.get('message', '获取用电编号失败'))
            cons_list = cons_data.get('data', {}).get('list', [])
            if not cons_list:
                if log_callback:
                    log_callback(f"  -> 无用电编号")
                continue
                # 获取用电编号失败
        except Exception as e:
                failures.append((mname, start_date, "ALL", f"获取用电编号失败: {e}"))
                if log_callback:
                    log_callback(f"  -> {e}")
                continue

        # 3. 遍历日期和用电编号
        for cons_no in cons_list:
            for date_str in date_range:
                if not is_auth_valid():
                    if log_callback:
                        log_callback("登录失效，中止抓取")
                    return success_count, failures
                query_headers = _build_headers(cookies, TYPE3_QUERY_URL)
                try:
                    query_payload = {
                        "data": {
                            "consNo": [cons_no],
                            "mid": [mid],
                            "infoDate": date_str
                        },
                        "pageInfo": {"total": 96, "list": [], "pageNum": 1, "pageSize": 96}
                    }
                    resp = requests.post(TYPE3_QUERY_URL, cookies=cookies, headers=query_headers, json=query_payload, timeout=30)
                    if resp.status_code == 200:
                        result = resp.json()
                        if result.get('status') == 0:
                            save_type3_query(date_str, cons_no, mid, json.dumps(result))
                            success_count += 1
                            if log_callback:
                                log_callback(f"  {date_str} {cons_no} 保存成功")
                        else:
                            failures.append((mname, date_str, cons_no, f"查询失败: {result.get('message')}"))
                            if log_callback:
                                log_callback(f"  {date_str} {cons_no} 查询失败: {result.get('message')}")
                    else:
                        failures.append((mname, date_str, cons_no, str(e)))
                        if log_callback:
                            log_callback(f"  {date_str} {cons_no} HTTP {resp.status_code}")
                except Exception as e:
                    failures.append((mname, date_str, cons_no, str(e)))
                    if log_callback:
                        log_callback(f"  {date_str} {cons_no} 异常: {e}")
                time.sleep(0.5)  # 适当间隔

    return success_count, failures