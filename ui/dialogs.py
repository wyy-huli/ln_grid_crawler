# ui/dialogs.py
import threading
import time
import queue

import PySimpleGUI as sg
import datetime
import json
import requests

from core.type3_auto import auto_fetch_type3
from utils.config import TYPE3_MEMBER_URL, TYPE3_CONS_URL, TYPE3_QUERY_URL, AUTH_FILE
from auth.auth_utils import is_auth_valid
from auth.tenant_context import get_current_dept_id
from database.db_manager import save_type3_query
from utils.logger import logger, drain_ui_log



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
    window = sg.Window('用电数据查询', layout, modal=True,
                       size=(620, 280), resizable=True)

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
                        raw_list = cons_data.get('data', {}).get('list', [])
                        cons_list = []
                        for item in raw_list:
                            if isinstance(item, dict):
                                cons_list.append(str(item.get('consNo', item.get('value', str(item)))))
                            else:
                                cons_list.append(str(item))
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
                        save_type3_query(info_date, cons_no, mid, json.dumps(result), member, dept_id=get_current_dept_id())
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
        [sg.Button('开始抓取'), sg.Button('取消抓取'), sg.Button('关闭')],
        [sg.Output(size=(70, 15), key='-OUTPUT-', expand_x=True, expand_y=True)],
    ]
    window = sg.Window('批量用电数据抓取', layout, modal=True, finalize=True,
                       size=(800, 550), resizable=True)

    fetching = False  # 防止重复点击
    stop_event = threading.Event()
    log_q = queue.Queue()  # 子线程日志队列，主线程 drain 后更新 sg.Output

    def log_callback(msg):
        """子线程调用：把日志放入队列，不直接写 sg.Output"""
        log_q.put(msg)

    def drain_log():
        """主线程调用：批量取出队列日志，更新 sg.Output"""
        lines = []
        for _ in range(200):
            try:
                lines.append(log_q.get_nowait())
            except queue.Empty:
                break
        if lines:
            window['-OUTPUT-'].update(value='\n'.join(lines) + '\n', append=True)

    def run_fetch(start, end):
        nonlocal fetching
        log_callback(f"开始抓取：{start} 至 {end}")
        success, fails = auto_fetch_type3(start, end,
                                          log_callback=log_callback,
                                          stop_event=stop_event)
        if stop_event.is_set():
            log_callback(f"抓取已取消，成功 {success} 条，失败 {len(fails)} 条")
        else:
            log_callback(f"抓取完成，成功 {success} 条，失败 {len(fails)} 条")
        if fails:
            log_callback("失败列表:")
            for f in fails:
                log_callback(f"  {f}")
        fetching = False
        stop_event.clear()

    while True:
        event, values = window.read(timeout=100)
        drain_log()

        if event in (sg.WIN_CLOSED, '关闭'):
            if fetching:
                stop_event.set()
                # 不强制 break，等子线程检测到 stop_event 后退出
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
            stop_event.clear()
            fetching = True
            threading.Thread(target=run_fetch, args=(start, end), daemon=True).start()
        if event == '取消抓取':
            if fetching:
                stop_event.set()
                logger.info('用户已请求取消抓取，等待当前请求完成后退出...')
            else:
                sg.popup('当前未在抓取')
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
        [sg.Button('开始抓取'), sg.Button('取消抓取'), sg.Button('关闭')],
        [sg.Output(size=(70, 15), key='-OUTPUT-', expand_x=True, expand_y=True)],
    ]
    window = sg.Window('批量合同数据抓取', layout, modal=True, finalize=True,
                       size=(800, 550), resizable=True)

    fetching = False
    stop_event = threading.Event()
    log_q = queue.Queue()

    def log_callback(msg):
        log_q.put(msg)

    def drain_log():
        lines = []
        for _ in range(200):
            try:
                lines.append(log_q.get_nowait())
            except queue.Empty:
                break
        if lines:
            window['-OUTPUT-'].update(value='\n'.join(lines) + '\n', append=True)

    def run_fetch(month_str):
        nonlocal fetching
        log_callback(f"开始抓取 {month_str} 月份合同数据...")
        from core.contract_crawler import fetch_month_contract_data
        success, fails = fetch_month_contract_data(month_str,
                                                    log_callback=log_callback,
                                                    stop_event=stop_event)
        if stop_event.is_set():
            log_callback(f"抓取已取消，成功 {success} 条，失败 {len(fails)} 条")
        else:
            log_callback(f"抓取完成，成功 {success} 条，失败 {len(fails)} 条")
        if fails:
            log_callback("失败列表:")
            for f in fails:
                if len(f) == 3:
                    log_callback(f"  合同: {f[0]} (ID:{f[1]}) - {f[2]}")
                else:
                    log_callback(f"  {f}")
        fetching = False
        stop_event.clear()

    while True:
        event, values = window.read(timeout=100)
        drain_log()

        if event in (sg.WIN_CLOSED, '关闭'):
            if fetching:
                stop_event.set()
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
            stop_event.clear()
            fetching = True
            threading.Thread(target=run_fetch, args=(month_str,), daemon=True).start()
        if event == '取消抓取':
            if fetching:
                stop_event.set()
                logger.info('用户已请求取消抓取，等待当前请求完成后退出...')
            else:
                sg.popup('当前未在抓取')
    window.close()


def manual_contract_dialog():
    """手动选择单个合同进行抓取"""
    if not is_auth_valid():
        show_error('登录失效', '请先重新登录')
        return

    current_month = datetime.date.today().strftime('%Y-%m')

    layout = [
        [sg.Text('手动抓取合同数据', font=('微软雅黑', 12))],
        [sg.Text('选择月份:'), sg.Input(current_month, key='-MONTH-', size=(10, 1))],
        [sg.Button('获取合同列表'), sg.Button('抓取选中合同'), sg.Button('取消')],
        [sg.Text('合同列表:')],
        [sg.Listbox([], key='-CONTRACT-LIST-', size=(70, 10), enable_events=True, expand_x=True, expand_y=True)],
        [sg.Text('选中合同:'), sg.Text('', key='-SELECTED-', size=(50, 1))],
        [sg.Output(size=(70, 10), key='-OUTPUT-', expand_x=True, expand_y=True)],
    ]
    window = sg.Window('手动合同数据抓取', layout, modal=True, finalize=True,
                       size=(850, 650), resizable=True)

    fetching = False
    contract_list_data = []
    # 【严重1修复】子对话框使用本地日志队列，不共享全局 UI_LOG_QUEUE，
    # 避免：
    # 1) 关闭对话框后，主窗口 read 循环 drain 残留日志导致串台
    # 2) 多个子对话框同时打开（或按序打开）时日志互相干扰
    local_log_q = queue.Queue()

    def local_log(msg):
        """子线程专用：把日志放入本地队列，不污染全局 UI_LOG_QUEUE"""
        local_log_q.put(msg)

    def drain_local_log():
        """主线程调用：批量取出本地队列日志，安全更新当前对话框的 sg.Output"""
        lines = []
        for _ in range(200):
            try:
                lines.append(local_log_q.get_nowait())
            except queue.Empty:
                break
        return '\n'.join(lines)

    def fetch_contract_list_data(month_str):
        try:
            from core.contract_crawler import fetch_contract_list
            local_log(f"正在获取 {month_str} 月份合同列表...")
            contracts = fetch_contract_list(month_str, log_callback=local_log)
            result_list = []
            for c in contracts:
                result_list.append({
                    'contractId': c.get('contractId', ''),
                    'contractName': c.get('contractName', '')
                })
            window.write_event_value('-CONTRACT-LIST-READY-', result_list)
        except Exception as e:
            local_log(f"获取合同列表失败: {e}")
            window.write_event_value('-CONTRACT-LIST-ERROR-', str(e))

    def run_single_fetch(contract_id, contract_name, month_str):
        nonlocal fetching
        from core.contract_crawler import fetch_single_contract_data
        success, error = fetch_single_contract_data(contract_id, contract_name, month_str, log_callback=local_log)
        if error:
            local_log(f"抓取失败: {error}")
        else:
            local_log(f"抓取成功，共 {success} 天数据")
        window.write_event_value('-FETCH-DONE-', None)

    while True:
        event, values = window.read(timeout=100)
        # 只从本地日志队列取日志，不再 drain 全局 UI_LOG_QUEUE，避免串台
        log_text = drain_local_log()
        if log_text:
            window['-OUTPUT-'].update(value=log_text + '\n', append=True)
        if event in (sg.WIN_CLOSED, '取消'):
            if fetching:
                sg.popup('正在抓取中，无法取消，请等待完成')
                continue
            break

        if event == '获取合同列表':
            if fetching:
                sg.popup('正在操作中，请稍后...')
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
            threading.Thread(target=fetch_contract_list_data, args=(month_str,), daemon=True).start()

        if event == '-CONTRACT-LIST-READY-':
            contract_list_data = values[event]
            contract_names = [f"{c['contractName']} (ID:{c['contractId']})" for c in contract_list_data]
            window['-CONTRACT-LIST-'].update(values=contract_names)
            local_log(f"共获取到 {len(contract_names)} 个合同")
            fetching = False

        if event == '-CONTRACT-LIST-ERROR-':
            show_error('获取合同列表失败', values[event])
            fetching = False

        if event == '-FETCH-DONE-':
            fetching = False

        if event == '-CONTRACT-LIST-' and values['-CONTRACT-LIST-']:
            selected = values['-CONTRACT-LIST-'][0]
            window['-SELECTED-'].update(selected)

        if event == '抓取选中合同':
            if fetching:
                sg.popup('正在抓取中，请稍后...')
                continue
            selected_items = values['-CONTRACT-LIST-']
            if not selected_items:
                sg.popup_error('请先选择一个合同')
                continue
            month_str = values['-MONTH-']
            if not month_str:
                sg.popup_error('请选择月份')
                continue

            selected_text = selected_items[0]
            idx = selected_text.rfind(' (ID:')
            if idx > 0:
                contract_name = selected_text[:idx]
                contract_id = selected_text[idx + 5:-1]
            else:
                sg.popup_error('无法解析合同信息')
                continue

            window['-OUTPUT-'].update('')
            fetching = True
            threading.Thread(target=run_single_fetch, args=(contract_id, contract_name, month_str), daemon=True).start()

    window.close()


def backfill_dept_dialog():
    """租户数据回填对话框"""
    import PySimpleGUI as sg
    from auth.tenant_context import get_current_dept_id, get_current_dept_name
    from database.db_manager import list_null_metering_records, list_null_contract_records, backfill_metering_dept, backfill_contract_dept
    from utils.logger import logger

    current_dept_id = get_current_dept_id()
    current_dept_name = get_current_dept_name()

    if not current_dept_id or current_dept_id == 'UNKNOWN':
        sg.popup_error('未检测到当前租户，请先登录！')
        return

    sg.theme('SystemDefault')

    type_choice = '用电数据'
    metering_records = []
    contract_records = []
    metering_list_str = []
    contract_list_str = []

    def _load_records(rec_type):
        nonlocal metering_records, contract_records, metering_list_str, contract_list_str
        try:
            if rec_type == '用电数据':
                metering_records = list_null_metering_records()
                metering_list_str = [
                    f'[ID:{r["id"]}] {r["query_date"]} mid={r["mid"]} {r["cons_no"]} {r["mname"]}'
                    for r in metering_records
                ]
            else:
                contract_records = list_null_contract_records()
                contract_list_str = [
                    f'[ID:{r["contract_id"]}] {r["contract_name"]} 购:{r["buyer"]} 售:{r["seller"]}'
                    for r in contract_records
                ]
        except Exception as e:
            sg.popup_error(f'加载失败: {e}')

    _load_records(type_choice)

    layout = [
        [sg.Text(f'当前租户: {current_dept_name} (deptId={current_dept_id})', text_color='darkblue')],
        [sg.Text('回填类型:'), sg.Combo(['用电数据', '合同数据'], default_value=type_choice, key='-TYPE-', enable_events=True, size=(15, 1))],
        [sg.Text('', key='-COUNT-', size=(30, 1))],
        [sg.Listbox(values=metering_list_str, key='-RECORDS-', size=(70, 15), select_mode=sg.LISTBOX_SELECT_MODE_MULTIPLE, enable_events=True, expand_x=True, expand_y=True)],
        [sg.Text('', key='-SEL-', size=(50, 1), text_color='blue')],
        [sg.Button('加载待回填记录'), sg.Button('执行回填选中记录'), sg.Button('关闭')],
        [sg.Text('', key='-RESULT-', size=(50, 1), text_color='green')],
    ]

    window = sg.Window('租户数据回填', layout, finalize=True,
                       size=(900, 650), resizable=True)

    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, '关闭'):
            break

        if event == '-TYPE-':
            type_choice = values['-TYPE-']
            _load_records(type_choice)
            if type_choice == '用电数据':
                window['-RECORDS-'].update(metering_list_str)
            else:
                window['-RECORDS-'].update(contract_list_str)
            count = len(metering_records) if type_choice == '用电数据' else len(contract_records)
            window['-COUNT-'].update(f'待回填记录数: {count}')

        if event == '加载待回填记录':
            type_choice = values['-TYPE-']
            _load_records(type_choice)
            if type_choice == '用电数据':
                window['-RECORDS-'].update(metering_list_str)
                window['-COUNT-'].update(f'待回填记录数: {len(metering_records)}')
            else:
                window['-RECORDS-'].update(contract_list_str)
                window['-COUNT-'].update(f'待回填记录数: {len(contract_records)}')

        if event == '-RECORDS-':
            selected = values['-RECORDS-']
            window['-SEL-'].update(f'已选中 {len(selected)} 条')

        if event == '执行回填选中记录':
            selected = values['-RECORDS-']
            if not selected:
                sg.popup_error('请先选择要回填的记录')
                continue

            type_choice = values['-TYPE-']
            if type_choice == '用电数据':
                record_ids = []
                for sel in selected:
                    # 精确提取 [ID:xxx] 中的数字
                    import re
                    match = re.search(r'\[ID:(\d+)\]', sel)
                    if match:
                        rid = int(match.group(1))
                        # 验证该 ID 在 metering_records 中存在
                        if any(r['id'] == rid for r in metering_records):
                            record_ids.append(rid)
                if record_ids:
                    try:
                        updated = backfill_metering_dept(record_ids, current_dept_id)
                        # 重新加载剩余记录
                        _load_records(type_choice)
                        window['-RECORDS-'].update(values=metering_list_str)
                        window['-COUNT-'].update(f'剩余待回填记录数: {len(metering_records)}')
                        window['-RESULT-'].update(f'回填成功: {updated} 条')
                        logger.info(f'回填用电数据 dept_id: {updated} 条')
                    except Exception as e:
                        sg.popup_error(f'回填失败: {e}')
            else:
                contract_ids = []
                for sel in selected:
                    # 精确提取 [ID:xxx] 后的 contract_id
                    import re
                    match = re.search(r'\[ID:([^\]]+)\]', sel)
                    if match:
                        cid = match.group(1)
                        if any(r['contract_id'] == cid for r in contract_records):
                            contract_ids.append(cid)
                if contract_ids:
                    try:
                        updated = backfill_contract_dept(contract_ids, current_dept_id)
                        # 重新加载剩余记录
                        _load_records(type_choice)
                        window['-RECORDS-'].update(values=contract_list_str)
                        window['-COUNT-'].update(f'剩余待回填记录数: {len(contract_records)}')
                        window['-RESULT-'].update(f'回填成功: {updated} 条')
                        logger.info(f'回填合同数据 dept_id: {updated} 条')
                    except Exception as e:
                        sg.popup_error(f'回填失败: {e}')

    window.close()


if __name__ == '__main__':
    type3_query_dialog()