# ui/app.py
import PySimpleGUI as sg
import sys
import os
import threading
import time

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scheduler.run_scheduler import start as start_scheduler, stop as stop_scheduler
from auth.auth_utils import is_auth_valid
from core.chart_crawler import manual_fetch
from utils.config import SIMPLE_TYPE1_APIS, DROP_GROUPS

def get_api_list():
    """获取所有可手动补抓的接口列表"""
    apis = []
    for api in SIMPLE_TYPE1_APIS:
        apis.append(f"{api['api_code']}: {api['api_name']}")
    for group in DROP_GROUPS:
        for opt in group['options']:
            apis.append(f"{opt['api_code']}: {opt['api_name']}")
    apis.append("realtime_clearing: 实时出清参考信息")
    return apis

def main():
    sg.theme('SystemDefault')
    api_list = get_api_list()
    from database.db_manager import init_db
    init_db()
    layout = [
        [sg.Text('【镁时镁刻】电力数据采集工具', font=('微软雅黑', 14))],
        [sg.Text('登录状态：未检测', key='-STATUS-', size=(30, 1))],
        [sg.Button('登录/刷新登录'), sg.Button('刷新状态'), sg.Button('启动定时抓取'),
         sg.Button('停止抓取'), sg.Button('退出')],
        [sg.HorizontalSeparator()],
        [sg.Text('手动补抓接口数据', font=('微软雅黑', 12))],
        [sg.Text('选择接口:'), sg.Combo(api_list, key='-API-', size=(50, 1))],
        [sg.Text('日期 (YYYY-MM-DD, 留空为今天):'), sg.Input(key='-DATE-', size=(12, 1))],
        [sg.Button('执行补抓'), sg.Text('', key='-MANUAL_RESULT-', size=(40, 1))],
        [sg.HorizontalSeparator()],
        [sg.Button('批量抓取用电数据'), sg.Button('用电数据查询（手动）')],
        [sg.Button('批量抓取合同数据')],
        [sg.HorizontalSeparator()],
        [sg.Text('运行日志', font=('微软雅黑', 12))],
        [sg.Output(size=(80, 15), key='-OUTPUT-')],
        [sg.Button('清空日志')],
    ]

    window = sg.Window('【镁时镁刻】电力数据采集工具', layout, finalize=True)

    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, '退出'):
            stop_scheduler()
            break

        if event == '登录/刷新登录':
            from auth.login import manual_login
            def do_login():
                try:
                    manual_login()
                    window.write_event_value('-LOGIN-DONE-', '登录完成')
                except Exception as e:
                    window.write_event_value('-LOGIN-DONE-', f'登录异常: {e}')
            threading.Thread(target=do_login, daemon=True).start()
            print('登录窗口已启动，请在浏览器中完成登录，完成后请点击"刷新状态"。')

        if event == '刷新状态':
            try:
                valid = is_auth_valid()
                window['-STATUS-'].update('登录状态：有效' if valid else '登录状态：已失效')
                print(f'登录状态已刷新：{"有效" if valid else "已失效"}')
            except Exception as e:
                window['-STATUS-'].update('登录状态：检测失败')
                print(f'状态检测异常: {e}')

        if event == '启动定时抓取':
            if not is_auth_valid():
                sg.popup_error('登录状态已失效，请先重新登录！')
            else:
                start_scheduler()
                print('定时调度已启动，实时接口将立即执行一次。')

        if event == '停止抓取':
            stop_scheduler()
            print('定时调度已停止')

        if event == '执行补抓':
            api_choice = values['-API-']
            date_str = values['-DATE-'].strip() or None
            if not api_choice:
                sg.popup_error('请选择一个接口')
                continue
            api_code = api_choice.split(':')[0].strip()
            print(f"手动补抓: {api_code} 日期: {date_str or '默认'}")
            # 使用线程避免界面卡顿
            def do_fetch():
                try:
                    success = manual_fetch(api_code, date_str)
                    if success:
                        window.write_event_value('-MANUAL-DONE-', f'{api_code} 补抓成功')
                    else:
                        window.write_event_value('-MANUAL-DONE-', f'{api_code} 补抓失败')
                except Exception as e:
                    window.write_event_value('-MANUAL-DONE-', f'补抓异常: {e}')
            threading.Thread(target=do_fetch, daemon=True).start()

        if event == '-MANUAL-DONE-':
            window['-MANUAL_RESULT-'].update(values[event])

        if event == '批量抓取用电数据':
            from ui.dialogs import auto_type3_dialog
            auto_type3_dialog()

        if event == '用电数据查询（手动）':
            from ui.dialogs import type3_query_dialog
            type3_query_dialog()

        if event == '批量抓取合同数据':
            from ui.dialogs import contract_crawler_dialog
            contract_crawler_dialog()

        if event == '清空日志':
            window['-OUTPUT-'].update('')

    window.close()

if __name__ == '__main__':
    main()