import sys
import os
import multiprocessing

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

# PyInstaller windowed 模式（console=False）下 sys.stdout/stderr 是 None
# 子进程继承后调用 flush()/write() 会报 AttributeError
# 这里把 None 重定向到 devnull，保证 print 和子进程不会崩溃
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='ignore')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8', errors='ignore')


def _safe_cleanup():
    """程序退出时的安全清理：停止调度器 + 清理本程序 Chrome 残留。

    任何步骤都吞异常，确保清理过程本身不会抛异常覆盖原始异常。
    幂等：多次调用安全（stop_scheduler 内部有 scheduler.running 守卫）。
    """
    # 1. 停止调度器
    try:
        from scheduler.run_scheduler import stop as stop_scheduler
        stop_scheduler()
    except Exception as e:
        try:
            print(f"[清理] stop_scheduler 异常（吞掉）: {e}", file=sys.stderr)
        except Exception:
            pass

    # 2. 清理本程序启动的 Chrome 残留（Q0-2 精准清理，不影响用户 Chrome）
    try:
        from core.browser_guard import _kill_all_chrome_processes
        _kill_all_chrome_processes()
    except Exception as e:
        try:
            print(f"[清理] 清理 Chrome 异常（吞掉）: {e}", file=sys.stderr)
        except Exception:
            pass


def _main():
    try:
        from ui.app import main
        main()
    except Exception as e:
        # 先用 print 兜底（logger 可能也异常，如磁盘满）
        import traceback
        try:
            print(f"[致命异常] 程序即将退出: {e}", file=sys.stderr)
            traceback.print_exc()
        except Exception:
            pass

        # 尝试用 logger 记录（可能失败）
        try:
            from utils.logger import logger
            logger.error(f"[致命异常] 程序退出: {e}\n{traceback.format_exc()}")
        except Exception:
            pass

        # 尝试弹窗提示（window 可能未创建，PySimpleGUI 可能不可用）
        try:
            import PySimpleGUI as sg
            sg.popup_error(f'程序发生异常即将退出:\n{e}')
        except Exception:
            # windowed 打包模式下 sg 可能也无法弹窗，fallback 到 print
            try:
                print(f"[致命异常] 程序即将退出: {e}", file=sys.stderr)
            except Exception:
                pass
    finally:
        _safe_cleanup()


if __name__ == '__main__':
    # PyInstaller 打包后 multiprocessing spawn 模式必须调用 freeze_support
    # 必须在 if __name__ == '__main__': 内调用，否则 spawn 子进程会重复执行
    multiprocessing.freeze_support()
    _main()
