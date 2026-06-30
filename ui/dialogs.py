# ui/dialogs.py
import threading
import time

import PySimpleGUI as sg
import datetime
import json
import requests

from core.type3_auto import auto_fetch_type3
from utils.config import TYPE3_MEMBER_URL, TYPE3_CONS_URL, TYPE3_QUERY_URL, AUTH_FILE
from auth.auth_utils import is_auth_valid
from database.db_manager import save_type3_query



def show_error(title, message):
    sg.popup_error(message, title=title)

def show_info(title, message):
    sg.popup(message, title=title)

def confirm_action(title, message):
    return sg.popup_yes_no(message, title=title) == "Yes"

def manual_fetch_dialog():
    # 备用，已在主界面实现
    pass



def _build_type3_headers(target_url, member_date=None):
    """从 auth.json 动态构建类型3所需的请求头"""
    with open(AUTH_FILE, 'r') as f:
        storage = json.load(f)

    cookies = {c['name']: c['value'] for c in storage.get('cookies', [])}

    # 提取 localStorage 中的 userId 作为 x-uid
    x_uid = '2217636810955'  # 默认值
    for origin in storage.get('origins', []):
        for item in origin.get('localStorage', []):
            if item['name'] == 'userId':
                x_uid = item['value']
                break

    # 动态构建 CurrentRoute（包含时间戳）
    timestamp_ms = str(int(time.time() * 1000))
    if 'getMemberName' in target_url:
        route = f"/pxf-settlement-outnetpub-gs/lnInformationDelivery/lnInfoIpYxXhFsPq?date={timestamp_ms}"
    elif 'queryIpYxXhFsPqChange' in target_url:
        route = f"/pxf-settlement-outnetpub-gs/lnInformationDelivery/lnInfoIpYxXhFsPq?date={timestamp_ms}"
    else:
        route = f"/pxf-settlement-outnetpub-gs/lnInformationDelivery/lnInfoIpYxXhFsPq?date={timestamp_ms}"

    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        "X-Ticket": cookies.get("X-Ticket", ""),
        "ClientTag": cookies.get("ClientTag", "OUTNET_BROWSE"),
        "CurrentRoute": route,
        "Referer": "https://pmos.ln.sgcc.com.cn/pxf-settlement-outnetpub-gs/",
        "X-Token": cookies.get("X-Token", "undefined"),
        "x-uid": x_uid,
    }
    return headers, cookies

def type3_query_dialog():
    if not is_auth_valid():
        show_error('登录失效', '请先重新登录')
        return

    try:
        headers, cookies = _build_type3_headers(TYPE3_MEMBER_URL)
    except Exception as e:
        show_error('读取认证信息失败', str(e))
        return

    # 1. 获取市场主体列表
    try:
        resp = requests.post(TYPE3_MEMBER_URL, cookies=cookies, headers=headers, json={}, timeout=15)
        if resp.status_code != 200:
            show_error('获取主体失败', f'HTTP {resp.status_code}')
            return
        data = resp.json()
        if data.get('status') != 0:
            msg = data.get('message', '未知错误')
            if '没有该接口访问权限' in msg:
                show_error('权限不足', '当前UKey没有该接口访问权限，请更换UKey或联系管理员')
                return
            show_error('获取主体失败', msg)
            return
        members = data.get('data', [])
        if not members:
            show_info('提示', '未获取到市场主体列表')
            return
        member_names = [m['powerMembersName'] for m in members]
        member_ids = [m['powerMembersId'] for m in members]
    except Exception as e:
        show_error('获取主体异常', str(e))
        return

    # 2. 构建对话框
    layout = [
        [sg.Text('选择市场主体:'), sg.Combo(member_names, key='-MEMBER-', size=(40, 1), enable_events=True)],
        [sg.Text('选择用电编号:'), sg.Combo([], key='-CONS-', size=(40, 1))],
        [sg.Text('查询日期:'), sg.Input(datetime.date.today().isoformat(), key='-DATE-', size=(12, 1))],
        [sg.Button('查询'), sg.Button('取消')],
        [sg.Text('', key='-RESULT-', size=(50, 1))]
    ]
    window = sg.Window('用电数据查询', layout, modal=True)

    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, '取消'):
            break

        if event == '-MEMBER-' and values['-MEMBER-']:
            idx = member_names.index(values['-MEMBER-'])
            mid = member_ids[idx]
            try:
                cons_payload = {
                    "data": {"consNo": "", "mid": [mid], "infoDate": values['-DATE-']},
                    "pageInfo": {"pageNum": 1, "pageSize": 1000, "total": 0}
                }
                resp = requests.post(TYPE3_CONS_URL, cookies=cookies, headers=headers, json=cons_payload, timeout=15)
                if resp.status_code == 200:
                    cons_data = resp.json()
                    if cons_data.get('status') == 0:
                        cons_list = cons_data.get('data', {}).get('list', [])
                        window['-CONS-'].update(values=cons_list)
                    else:
                        show_error('获取用电编号失败', cons_data.get('message', ''))
                else:
                    show_error('获取用电编号失败', f'HTTP {resp.status_code}')
            except Exception as e:
                show_error('异常', str(e))

        if event == '查询':
            member = values['-MEMBER-']
            cons_no = values['-CONS-']
            info_date = values['-DATE-']
            if not member or not cons_no:
                show_error('参数缺失', '请选择市场主体和用电编号')
                continue
            idx = member_names.index(member)
            mid = member_ids[idx]
            try:
                query_payload = {
                    "data": {
                        "consNo": [cons_no],
                        "mid": [mid],
                        "infoDate": info_date
                    },
                    "pageInfo": {"total": 96, "list": [], "pageNum": 1, "pageSize": 96}
                }
                resp = requests.post(TYPE3_QUERY_URL, cookies=cookies, headers=headers, json=query_payload, timeout=30)
                if resp.status_code == 200:
                    result = resp.json()
                    if result.get('status') == 0:
                        save_type3_query(info_date, cons_no, mid, json.dumps(result))
                        window['-RESULT-'].update('查询成功，数据已保存')
                    else:
                        show_error('查询失败', result.get('message', '未知错误'))
                else:
                    show_error('查询失败', f'HTTP {resp.status_code}')
            except Exception as e:
                show_error('查询异常', str(e))

    window.close()
def auto_type3_dialog():
    """自动批量抓取用电数据，用户输入日期范围（后台线程执行，不卡UI）"""
    layout = [
        [sg.Text('自动抓取所有用电数据', font=('微软雅黑', 12))],
        [sg.Text('开始日期:'), sg.Input(datetime.date.today().isoformat(), key='-START-', size=(12,1)),
         sg.CalendarButton('选择', target='-START-', format='%Y-%m-%d')],
        [sg.Text('结束日期:'), sg.Input(datetime.date.today().isoformat(), key='-END-', size=(12,1)),
         sg.CalendarButton('选择', target='-END-', format='%Y-%m-%d')],
        [sg.Button('开始抓取'), sg.Button('取消')],
        [sg.Output(size=(70, 15), key='-OUTPUT-')],
    ]
    window = sg.Window('批量用电数据抓取', layout, modal=True, finalize=True)

    fetching = False  # 防止重复点击

    def run_fetch(start, end):
        nonlocal fetching
        print(f"开始抓取：{start} 至 {end}")
        success, fails = auto_fetch_type3(start, end)
        print(f"抓取完成，成功 {success} 条，失败 {len(fails)} 条")
        if fails:
            print("失败列表:")
            for f in fails:
                print(f"  {f}")
        fetching = False

    while True:
        event, values = window.read(timeout=100)
        if event in (sg.WIN_CLOSED, '取消'):
            if fetching:
                sg.popup('正在抓取中，无法取消，请等待完成')
                continue
            break
        if event == '开始抓取':
            if fetching:
                sg.popup('正在抓取中，请稍后...')
                continue
            start = values['-START-']
            end = values['-END-']
            if not start or not end:
                sg.popup_error('请选择开始和结束日期')
                continue
            if start > end:
                sg.popup_error('开始日期不能晚于结束日期')
                continue
            window['-OUTPUT-'].update('')
            fetching = True
            threading.Thread(target=run_fetch, args=(start, end), daemon=True).start()
    window.close()

def contract_crawler_dialog():
    """合同分时曲线数据批量抓取对话框"""
    if not is_auth_valid():
        show_error('登录失效', '请先重新登录')
        return

    current_month = datetime.date.today().strftime('%Y-%m')

    layout = [
        [sg.Text('合同分时曲线数据抓取', font=('微软雅黑', 12))],
        [sg.Text('选择月份:'), sg.Input(current_month, key='-MONTH-', size=(10, 1))],
        [sg.Button('开始抓取'), sg.Button('取消')],
        [sg.Output(size=(70, 15), key='-OUTPUT-')],
    ]
    window = sg.Window('批量合同数据抓取', layout, modal=True, finalize=True)

    fetching = False

    def run_fetch(month_str):
        nonlocal fetching
        print(f"开始抓取 {month_str} 月份合同数据...")
        from core.contract_crawler import fetch_month_contract_data
        success, fails = fetch_month_contract_data(month_str, log_callback=print)
        print(f"抓取完成，成功 {success} 条，失败 {len(fails)} 条")
        if fails:
            print("失败列表:")
            for f in fails:
                print(f"  {f}")
        fetching = False

    while True:
        event, values = window.read(timeout=100)
        if event in (sg.WIN_CLOSED, '取消'):
            if fetching:
                sg.popup('正在抓取中，无法取消，请等待完成')
                continue
            break
        if event == '开始抓取':
            if fetching:
                sg.popup('正在抓取中，请稍后...')
                continue
            month_str = values['-MONTH-']
            if not month_str:
                sg.popup_error('请选择月份')
                continue
            if len(month_str) != 7 or month_str[4] != '-':
                sg.popup_error('请输入正确的月份格式 (YYYY-MM)')
                continue
            try:
                datetime.datetime.strptime(month_str, '%Y-%m')
            except ValueError:
                sg.popup_error('请输入正确的月份格式 (YYYY-MM)')
                continue
            window['-OUTPUT-'].update('')
            fetching = True
            threading.Thread(target=run_fetch, args=(month_str,), daemon=True).start()
    window.close()


if __name__ == '__main__':
    type3_query_dialog()