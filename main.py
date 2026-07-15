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


def _main():
    from ui.app import main
    main()


if __name__ == '__main__':
    # PyInstaller 打包后 multiprocessing spawn 模式必须调用 freeze_support
    # 必须在 if __name__ == '__main__': 内调用，否则 spawn 子进程会重复执行
    multiprocessing.freeze_support()
    _main()
