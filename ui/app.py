# ui/app.py
import PySimpleGUI as sg
import sys
import os
import threading
import time
import datetime

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from scheduler.run_scheduler import (
    start as start_scheduler,
    stop as stop_scheduler,
    list_retry_jobs,
    get_all_jobs_summary,
)
from auth.auth_utils import is_auth_valid
from auth.tenant_context import init_tenant_context, refresh_tenant_context, get_current_dept_id, get_current_dept_name
from core.browser_guard import run_in_subprocess
from utils.config import SIMPLE_TYPE1_APIS, DROP_GROUPS
from utils.logger import logger, drain_ui_log


def _show_retry_dialog():
    """弹窗展示当前所有重试任务"""
    total, jobs = list_retry_jobs(log=False)
    summary = get_all_jobs_summary()

    lines = []
    lines.append("=" * 60)
    lines.append("调度器任务总览")
    lines.append("=" * 60)
    lines.append(f"  定时任务数: {len(summary['cron'])}")
    lines.append(f"  待重试任务数: {len(summary['retry'])}")
    lines.append(f"  一次性启动任务数: {len(summary['startup'])}")
    lines.append("")

    if summary['cron']:
        lines.append("【定时任务】")
        for j in summary['cron']:
            lines.append(f"  {j['id']:30s}   下次运行: {j['next_run']}")
        lines.append("")

    if jobs:
        lines.append(f"【待重试任务】共 {total} 个")
        lines.append("-" * 60)
        for j in jobs:
            nxt = j["next_run_time"].strftime("%Y-%m-%d %H:%M:%S") if j["next_run_time"] else "-"
            status_txt = "待执行" if j["status"] == "pending" else "暂停"
            lines.append(
                f"  接口: {j['api_code']}  第{j['retry_no']}次  "
                f"下次运行: {nxt}  状态: {status_txt}"
            )
    else:
        lines.append("【待重试任务】当前没有待执行的重试任务")

    text = "\n".join(lines)

    layout = [
        [sg.Text('调度任务与重试任务查询', font=('微软雅黑', 12))],
        [sg.Multiline(text, size=(80, 25), key='-OUT-', autoscroll=True,
                      disabled=True, font=('Consolas', 10))],
        [sg.Button('刷新'), sg.Button('导出日志'), sg.Button('关闭')],
    ]
    win = sg.Window('调度任务查询', layout, modal=True,
                    size=(900, 560), resizable=True, finalize=True)

    while True:
        event, _ = win.read()
        if event in (sg.WIN_CLOSED, '关闭'):
            break
        if event == '刷新':
            list_retry_jobs(log=True)  # 把刷新信息同步打到主窗口日志
            total2, jobs2 = list_retry_jobs(log=False)
            summary2 = get_all_jobs_summary()
            buf = []
            buf.append("=" * 60)
            buf.append("调度器任务总览（已刷新）")
            buf.append("=" * 60)
            buf.append(f"  定时任务数: {len(summary2['cron'])}")
            buf.append(f"  待重试任务数: {len(summary2['retry'])}")
            buf.append(f"  一次性启动任务数: {len(summary2['startup'])}")
            buf.append("")
            if summary2['cron']:
                buf.append("【定时任务】")
                for j in summary2['cron']:
                    buf.append(f"  {j['id']:30s}   下次运行: {j['next_run']}")
                buf.append("")
            if jobs2:
                buf.append(f"【待重试任务】共 {total2} 个")
                buf.append("-" * 60)
                for j in jobs2:
                    nxt = j["next_run_time"].strftime("%Y-%m-%d %H:%M:%S") if j["next_run_time"] else "-"
                    status_txt = "待执行" if j["status"] == "pending" else "暂停"
                    buf.append(
                        f"  接口: {j['api_code']}  第{j['retry_no']}次  "
                        f"下次运行: {nxt}  状态: {status_txt}"
                    )
            else:
                buf.append("【待重试任务】当前没有待执行的重试任务")
            win['-OUT-'].update("\n".join(buf))
        if event == '导出日志':
            try:
                path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    f"retry_jobs_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
                )
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(win['-OUT-'].get())
                sg.popup(f"已导出到:\n{path}", title='导出成功')
            except Exception as e:
                sg.popup_error(f"导出失败: {e}", title='导出失败')

    win.close()

def get_api_list():
    """获取所有可手动补抓的接口列表"""
    apis = []
    for api in SIMPLE_TYPE1_APIS:
        apis.append(f"{api['api_code']}: {api['api_name']}")
    for group in DROP_GROUPS:
        for opt in group['options']:
            apis.append(f"{opt['api_code']}: {opt['api_name']}")
    apis.append("realtime_clearing: 实时出清参考信息")
    apis.append("type4_unit_status: 机组状态")
    return apis

def main():
    sg.theme('SystemDefault')
    api_list = get_api_list()
    from database.db_manager import init_db
    init_db()
    init_tenant_context()

    # 租户信息显示
    tenant_info = f'当前租户: {get_current_dept_name()} (deptId={get_current_dept_id()})'
    layout = [
        [sg.Text('【镁时镁刻】电力数据采集工具', font=('微软雅黑', 14))],
        [sg.Text('登录状态：未检测', key='-STATUS-', size=(30, 1))],
        [sg.Text(tenant_info, key='-TENANT-', size=(50, 1), text_color='darkblue')],
        [sg.Text('调度状态：未启动', key='-SCHED-STATUS-', size=(60, 1), text_color='darkgreen')],
        [sg.Button('登录/刷新登录'), sg.Button('刷新状态'), sg.Button('查看重试任务'),
         sg.Button('启动定时抓取'), sg.Button('停止抓取'), sg.Button('退出')],
        [sg.HorizontalSeparator()],
        [sg.Text('手动补抓接口数据', font=('微软雅黑', 12))],
        [sg.Text('选择接口:'), sg.Combo(api_list, key='-API-', size=(50, 1))],
        [sg.Text('开始日期:'), sg.Input(key='-FETCH-START-', size=(12, 1)),
         sg.CalendarButton('选择', target='-FETCH-START-', format='%Y-%m-%d'),
         sg.Text('   结束日期:'), sg.Input(key='-FETCH-END-', size=(12, 1)),
         sg.CalendarButton('选择', target='-FETCH-END-', format='%Y-%m-%d')],
        [sg.Text('(单日期时开始=结束；留空则按今天执行1天)', font=('微软雅黑', 9), text_color='gray')],
        [sg.Button('执行补抓'), sg.Button('取消补抓', disabled=True),
         sg.Text('', key='-MANUAL_RESULT-', size=(60, 1))],
        [sg.HorizontalSeparator()],
        [sg.Button('批量抓取用电数据'), sg.Button('用电数据查询（手动）')],
        [sg.Button('批量抓取合同数据'), sg.Button('手动抓取合同数据')],
        [sg.Button('租户数据回填')],
        [sg.HorizontalSeparator()],
        [sg.Text('运行日志', font=('微软雅黑', 12))],
        [sg.Output(size=(80, 15), key='-OUTPUT-', expand_x=True, expand_y=True)],
        [sg.Button('清空日志')],
    ]

    window = sg.Window('【镁时镁刻】电力数据采集工具', layout,
                       size=(1100, 650),
                       resizable=True,
                       finalize=True)

    # 日志队列中转模型：主线程定期 drain UI_LOG_QUEUE 并更新 sg.Output
    # 替代原先把 logger StreamHandler 指向 sys.stdout（sg.OutputStream）的方式
    # 避免子线程/调度器工作线程跨线程写 sg.Output 导致 Tkinter 死锁
    #
    # 【严重3修复】定期截断 sg.Output 内容：长时间运行下 sg.Output 会累计数万行，
    # 每次 Tkinter 更新都需要处理巨量文本，导致 UI 冻结。
    # 策略：超过 5000 行保留最后 1000 行。
    _OUTPUT_MAX_LINES = 5000
    _OUTPUT_KEEP_LINES = 1000
    _output_line_count = 0  # 粗略计数，降低频繁访问 Tk widget 的开销
    _TRUNCATE_CHECK_EVERY = 50  # 每 50 次 drain 检查一次

    truncate_check_counter = 0

    # 手动接口补抓状态（支持日期段批量）
    batch_fetching = False  # 是否正在批量补抓
    batch_stop_event = threading.Event()  # 补抓取消信号
    # 日期段批量最大天数（防止误选大范围长时间占用浏览器）
    _BATCH_MAX_DAYS = 31
    # realtime_clearing / type4_unit_status 不支持日期段（高频接口，调度器自动触发）
    # type4 另外不接收日期参数，run_type4() 内部固定取 T-1
    _NO_BATCH_APIS = {"realtime_clearing", "type4_unit_status"}

    while True:
        # 第一层：window.read 异常（Tk 内部损坏）必须退出主循环
        try:
            event, values = window.read(timeout=100)
        except Exception as e:
            logger.error(f"[UI] window.read 异常（Tk可能损坏，退出主循环）: {e}")
            break

        # 第二层：事件处理异常 → 记录日志，continue 继续循环（不退出主循环）
        try:
            # 主线程 drain 日志队列，安全更新 sg.Output
            log_text = drain_ui_log()
            if log_text:
                window['-OUTPUT-'].update(value=log_text + '\n', append=True)
                new_lines = log_text.count('\n') + 1
                _output_line_count += new_lines
                truncate_check_counter += 1
                # 粗略超过阈值 且 达到检查周期时，精确读一次 widget 内容并截断
                if _output_line_count > _OUTPUT_MAX_LINES and truncate_check_counter >= _TRUNCATE_CHECK_EVERY:
                    truncate_check_counter = 0
                    try:
                        current = window['-OUTPUT-'].get()
                        if current:
                            lines = current.split('\n')
                            if len(lines) > _OUTPUT_MAX_LINES:
                                kept = lines[-_OUTPUT_KEEP_LINES:]
                                window['-OUTPUT-'].update(value='\n'.join(kept))
                                _output_line_count = len(kept)
                    except Exception:
                        pass

            if event in (sg.WIN_CLOSED, '退出'):
                break  # stop_scheduler() 在循环退出后统一调用

            if event == '登录/刷新登录':
                from auth.login import manual_login
                def do_login():
                    try:
                        manual_login()
                        refresh_tenant_context()
                        window.write_event_value('-LOGIN-DONE-', '登录完成')
                    except Exception as e:
                        window.write_event_value('-LOGIN-DONE-', f'登录异常: {e}')
                threading.Thread(target=do_login, daemon=True).start()
                logger.info('登录窗口已启动，请在浏览器中完成登录，完成后请点击"刷新状态"。')

            if event == '刷新状态':
                try:
                    valid = is_auth_valid()
                    # Q1-1 修复：同步更新 AuthGate 缓存
                    try:
                        from scheduler.run_scheduler import _set_auth_valid
                        _set_auth_valid(valid)
                    except Exception:
                        pass
                    window['-STATUS-'].update('登录状态：有效' if valid else '登录状态：已失效')
                    # 同时刷新租户信息
                    init_tenant_context()
                    window['-TENANT-'].update(f'当前租户: {get_current_dept_name()} (deptId={get_current_dept_id()})')
                    # 调度器状态 + 重试数
                    try:
                        from scheduler.run_scheduler import scheduler
                        running = scheduler.running
                        total_retry, _ = list_retry_jobs(log=False)
                        summary = get_all_jobs_summary()
                        sched_status = '运行中' if running else '未启动'
                        cron_cnt = len(summary['cron'])
                        sched_text = (
                            f"调度状态：{sched_status}  |  "
                            f"定时任务:{cron_cnt}个  |  "
                            f"待重试:{total_retry}个"
                        )
                        window['-SCHED-STATUS-'].update(sched_text)
                        list_retry_jobs(log=True)  # 日志里也打一份，方便追溯
                    except Exception as se:
                        window['-SCHED-STATUS-'].update(f"调度状态：查询失败 {se}")
                    logger.info(f'登录状态已刷新：{"有效" if valid else "已失效"}')
                except Exception as e:
                    window['-STATUS-'].update('登录状态：检测失败')
                    logger.error(f'状态检测异常: {e}')

            if event == '查看重试任务':
                _show_retry_dialog()
                # 关闭弹窗后顺便刷新主界面的调度状态条
                try:
                    from scheduler.run_scheduler import scheduler
                    running = scheduler.running
                    total_retry, _ = list_retry_jobs(log=False)
                    summary = get_all_jobs_summary()
                    sched_status = '运行中' if running else '未启动'
                    sched_text = (
                        f"调度状态：{sched_status}  |  "
                        f"定时任务:{len(summary['cron'])}个  |  "
                        f"待重试:{total_retry}个"
                    )
                    window['-SCHED-STATUS-'].update(sched_text)
                except Exception:
                    pass

            if event == '启动定时抓取':
                if not is_auth_valid():
                    sg.popup_error('登录状态已失效，请先重新登录！')
                else:
                    start_scheduler()
                    logger.info('定时调度已启动，实时接口将立即执行一次。')
                    try:
                        total_retry, _ = list_retry_jobs(log=False)
                        summary = get_all_jobs_summary()
                        window['-SCHED-STATUS-'].update(
                            f"调度状态：运行中  |  "
                            f"定时任务:{len(summary['cron'])}个  |  "
                            f"待重试:{total_retry}个"
                        )
                    except Exception:
                        pass

            if event == '停止抓取':
                stop_scheduler()
                logger.info('定时调度已停止')
                try:
                    total_retry, _ = list_retry_jobs(log=False)
                    summary = get_all_jobs_summary()
                    window['-SCHED-STATUS-'].update(
                        f"调度状态：已停止  |  "
                        f"定时任务:{len(summary['cron'])}个  |  "
                        f"待重试:{total_retry}个"
                    )
                except Exception:
                    pass

            if event == '执行补抓':
                if batch_fetching:
                    sg.popup('正在批量补抓中，请等待或取消后再试...')
                    continue
                api_choice = values['-API-']
                if not api_choice:
                    sg.popup_error('请选择一个接口')
                    continue
                api_code = api_choice.split(':')[0].strip()

                # 解析日期范围
                start_raw = (values.get('-FETCH-START-') or '').strip()
                end_raw = (values.get('-FETCH-END-') or '').strip()

                # 留空 → 今天执行1天
                if not start_raw and not end_raw:
                    start_date = datetime.date.today()
                    end_date = datetime.date.today()
                elif not start_raw or not end_raw:
                    sg.popup_error('请同时填写开始日期和结束日期，或两者留空（按今天执行1天）')
                    continue
                else:
                    try:
                        start_date = datetime.datetime.strptime(start_raw, '%Y-%m-%d').date()
                    except ValueError:
                        sg.popup_error('开始日期格式错误，请使用 YYYY-MM-DD')
                        continue
                    try:
                        end_date = datetime.datetime.strptime(end_raw, '%Y-%m-%d').date()
                    except ValueError:
                        sg.popup_error('结束日期格式错误，请使用 YYYY-MM-DD')
                        continue

                if start_date > end_date:
                    sg.popup_error('开始日期不能晚于结束日期')
                    continue

                # 天数限制
                total_days = (end_date - start_date).days + 1
                if total_days > _BATCH_MAX_DAYS:
                    sg.popup_error(f'日期段最大允许 {_BATCH_MAX_DAYS} 天（当前 {total_days} 天），请缩小范围')
                    continue

                # realtime_clearing / type4_unit_status 不支持多天
                if total_days > 1 and api_code in _NO_BATCH_APIS:
                    sg.popup_error(f'{api_code} 是高频接口，不支持日期段批量补抓，请调整为单天')
                    continue

                # type4 机组状态接口固定抓取 T-1 日期，提示用户日期参数无效
                if api_code == "type4_unit_status":
                    sg.popup_quick_message('机组状态接口固定抓取 T-1 日期，日期参数将被忽略', auto_close_duration=3)

                logger.info(f"手动补抓: 接口={api_code}, 日期范围={start_date} ~ {end_date}, 共{total_days}天")
                if total_days > 1:
                    logger.info("[提示] 如定时抓取正在运行，建议先点击'停止抓取'，避免与批量补抓竞争浏览器资源")

                # 初始化状态
                batch_stop_event.clear()
                batch_fetching = True
                window['执行补抓'].update(disabled=True)
                window['取消补抓'].update(disabled=False)
                window['-MANUAL_RESULT-'].update(f'执行中...  0/{total_days}')

                def do_batch_fetch(api_code, start_date, end_date, total_days):
                    nonlocal batch_fetching
                    success_count = 0
                    failure_count = 0
                    failure_dates = []
                    cancelled = False
                    delta_days = (end_date - start_date).days

                    for i in range(delta_days + 1):
                        # 取消检查点：当前子进程完成后检测
                        if batch_stop_event.is_set():
                            cancelled = True
                            logger.info(f"[批量补抓] 收到取消信号，在 {i}/{total_days} 天处停止")
                            break

                        current_date = start_date + datetime.timedelta(days=i)
                        current_date_str = current_date.isoformat()
                        logger.info(f"[批量补抓] 第{i+1}/{total_days}天 {current_date_str} 开始")

                        try:
                            ok = run_in_subprocess(
                                'core.chart_crawler.manual_fetch',
                                timeout_sec=300,
                                args=(api_code, current_date_str),
                                kill_chrome_on_timeout=True,
                            )
                            if ok:
                                success_count += 1
                                logger.info(f"[批量补抓] 第{i+1}/{total_days}天 {current_date_str} 成功")
                            else:
                                failure_count += 1
                                failure_dates.append(current_date_str)
                                logger.error(f"[批量补抓] 第{i+1}/{total_days}天 {current_date_str} 失败")
                        except Exception as e:
                            failure_count += 1
                            failure_dates.append(current_date_str)
                            logger.error(f"[批量补抓] 第{i+1}/{total_days}天 {current_date_str} 异常: {e}")

                        # 进度更新（每1天通过事件回传，避免主线程不知道进度）
                        window.write_event_value('-BATCH-PROGRESS-', (i + 1, total_days, success_count, failure_count))

                    # 发送最终结果
                    summary = {
                        'api_code': api_code,
                        'start': str(start_date),
                        'end': str(end_date),
                        'total': total_days,
                        'success': success_count,
                        'failure': failure_count,
                        'failure_dates': failure_dates,
                        'cancelled': cancelled,
                    }
                    window.write_event_value('-MANUAL-DONE-', summary)

                threading.Thread(target=do_batch_fetch,
                                 args=(api_code, start_date, end_date, total_days),
                                 daemon=True).start()

            if event == '取消补抓':
                if not batch_fetching:
                    sg.popup('当前未在批量补抓')
                    continue
                # 设置取消信号，后台线程在当前日期完成后会自动退出
                batch_stop_event.set()
                window['-MANUAL_RESULT-'].update('取消请求已发送，等待当前日期完成...')
                logger.info('[批量补抓] 用户请求取消，等待当前日期子进程完成后退出')

            if event == '-BATCH-PROGRESS-':
                done, total, succ, fail = values[event]
                window['-MANUAL_RESULT-'].update(f'执行中...  {done}/{total}（成功{succ} / 失败{fail}）')

            if event == '-MANUAL-DONE-':
                # 恢复按钮状态
                batch_fetching = False
                batch_stop_event.clear()
                window['执行补抓'].update(disabled=False)
                window['取消补抓'].update(disabled=True)

                result = values[event]
                if isinstance(result, dict):
                    # 新的批量补抓格式：汇总字典
                    api_code = result.get('api_code', '')
                    total = result.get('total', 0)
                    success = result.get('success', 0)
                    failure = result.get('failure', 0)
                    failure_dates = result.get('failure_dates', [])
                    cancelled = result.get('cancelled', False)
                    prefix = '已取消，' if cancelled else ''
                    res_text = f'{prefix}{api_code} 补抓: 成功{success}天 / 失败{failure}天 / 共{total}天'
                    window['-MANUAL_RESULT-'].update(res_text)
                    if failure_dates:
                        # 失败日期太长，用 logger 输出不在控件显示
                        logger.warning(f'[批量补抓] 失败日期: {", ".join(failure_dates)}')
                else:
                    # 旧的单日格式（兼容，一般不会走到这里）
                    window['-MANUAL_RESULT-'].update(result)

            if event == '批量抓取用电数据':
                from ui.dialogs import auto_type3_dialog
                auto_type3_dialog()

            if event == '用电数据查询（手动）':
                from ui.dialogs import type3_query_dialog
                type3_query_dialog()

            if event == '批量抓取合同数据':
                from ui.dialogs import contract_crawler_dialog
                contract_crawler_dialog()

            if event == '手动抓取合同数据':
                from ui.dialogs import manual_contract_dialog
                manual_contract_dialog()

            if event == '租户数据回填':
                from ui.dialogs import backfill_dept_dialog
                backfill_dept_dialog()
                # 回填后刷新租户信息
                window['-TENANT-'].update(f'当前租户: {get_current_dept_name()} (deptId={get_current_dept_id()})')

            if event == '-LOGIN-DONE-':
                msg = values[event]
                if '异常' in msg or '失败' in msg:
                    window['-STATUS-'].update('登录状态：失败')
                    logger.error(f'登录失败: {msg}')
                else:
                    window['-STATUS-'].update('登录状态：有效')
                    window['-TENANT-'].update(f'当前租户: {get_current_dept_name()} (deptId={get_current_dept_id()})')
                    logger.info(f'登录完成，当前租户: {get_current_dept_name()}')
                    # Q1-1 修复：登录成功后清除 AuthGate 缓存，让任务立即恢复抓取
                    try:
                        from scheduler.run_scheduler import _invalidate_auth_cache
                        _invalidate_auth_cache()
                        logger.info('[AUTH] 已清除登录缓存，任务门控将重新检查')
                    except Exception as ae:
                        logger.warning(f'[AUTH] 清除登录缓存异常: {ae}')

            if event == '清空日志':
                window['-OUTPUT-'].update('')

        # 第二层 except：事件处理异常 → 记录日志，继续主循环（不退出）
        except Exception as e:
            logger.error(f"[UI] 事件处理异常（已捕获，继续主循环）: {e}")
            continue

    # 主循环退出后的清理（window 已关闭或异常退出）
    try:
        stop_scheduler()
    except Exception as e:
        logger.error(f"[UI] 退出时 stop_scheduler 异常: {e}")
    try:
        window.close()
    except Exception as e:
        logger.error(f"[UI] 退出时 window.close 异常: {e}")

if __name__ == '__main__':
    main()