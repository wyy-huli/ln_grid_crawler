# 日志
# utils/logger.py
import logging
import os
import sys
import queue
from datetime import date
from logging.handlers import RotatingFileHandler

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8', errors='ignore')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8', errors='ignore')

from utils.config import DATA_DIR

LOG_DIR = os.path.join(DATA_DIR, 'logs')
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, 'crawler.log')
DAILY_LOG_FILE = os.path.join(LOG_DIR, 'daily_log.txt')

# 主进程日志队列：所有 logger 输出同时入队，由主线程 drain 后更新 sg.Output
# 解决子线程/调度器工作线程跨线程写 sg.Output 导致 Tkinter 死锁问题
# 【严重2修复】设置 maxsize=10000：主窗口最小化/卡死期间，日志不会无限膨胀导致 OOM，
# 超过容量时自动丢弃最旧日志（只影响 UI 展示，不影响 file_handler 的磁盘日志）
UI_LOG_QUEUE = queue.Queue(maxsize=10000)

# 丢弃计数器（日志队列满时），用于诊断
_ui_log_dropped = 0


class UiQueueHandler(logging.Handler):
    """将日志消息格式化后放入 UI_LOG_QUEUE，供主线程 drain 并安全更新 sg.Output。

    队列满策略：丢弃最旧条目（队头），保留最新，避免 put_nowait 抛 Full 后全部丢弃。
    （file_handler/daily_handler 仍然写入磁盘，UI 展示是"近似最近"视图）
    """
    def __init__(self):
        super().__init__()
        self.setFormatter(formatter)

    def emit(self, record):
        try:
            msg = self.format(record)
            try:
                UI_LOG_QUEUE.put_nowait(msg)
            except queue.Full:
                # 队列满：丢弃 1 条最旧，腾出空间放入最新（简单环形缓冲思路）
                global _ui_log_dropped
                try:
                    UI_LOG_QUEUE.get_nowait()
                    _ui_log_dropped += 1
                except Exception:
                    pass
                try:
                    UI_LOG_QUEUE.put_nowait(msg)
                except Exception:
                    pass
                # 每 100 条溢出打印一次告警，避免告警日志本身再次溢出
                if _ui_log_dropped % 100 == 0:
                    try:
                        warn_msg = self.format(logging.LogRecord(
                            name='UiQueueHandler',
                            level=logging.WARNING,
                            pathname=__file__,
                            lineno=0,
                            msg=f'[UI日志队列] 累计已溢出丢弃 {_ui_log_dropped} 条',
                            args=(),
                            exc_info=None,
                        ))
                        # 不调用 put_nowait，避免再次抛 Full 触发告警风暴
                        try:
                            UI_LOG_QUEUE.get_nowait()
                        except Exception:
                            pass
                        try:
                            UI_LOG_QUEUE.put_nowait(warn_msg)
                        except Exception:
                            pass
                    except Exception:
                        pass
        except Exception:
            pass


def drain_ui_log(max_batch=200):
    """主线程调用：批量取出队列中的日志消息，返回合并后的字符串。

    Args:
        max_batch: 单次 drain 最大消息数，防止极端堆积导致 UI 阻塞

    Returns:
        str: 合并后的日志文本（以换行分隔），无消息返回空字符串
    """
    lines = []
    for _ in range(max_batch):
        try:
            msg = UI_LOG_QUEUE.get_nowait()
            lines.append(msg)
        except queue.Empty:
            break
    return '\n'.join(lines)


class DailyFileHandler(logging.FileHandler):
    """按天覆盖的日志处理器。

    固定文件名（daily_log.txt），每天第一次写入时自动清空文件，
    当天后续写入追加模式，实现"每天一个文件，覆盖前一天日志"。
    """

    def __init__(self, filename, encoding='utf-8'):
        super().__init__(filename, mode='a', encoding=encoding)
        # 初始化为 None，确保第一次 emit 会检查文件日期
        self._current_date = None
        self.encoding = encoding

    def _should_rollover(self):
        """判断是否需要清空文件（跨天或首次启动）"""
        today = date.today()
        if self._current_date is None:
            # 首次启动，检查文件是否存在且非今天创建
            if os.path.exists(self.baseFilename):
                mtime = date.fromtimestamp(os.path.getmtime(self.baseFilename))
                return mtime != today
            return False
        return today != self._current_date

    def _do_rollover(self):
        """执行清空文件并更新日期"""
        self._current_date = date.today()
        self.close()
        with open(self.baseFilename, 'w', encoding=self.encoding):
            pass
        self.mode = 'a'
        self.stream = self._open()

    def emit(self, record):
        if self._should_rollover():
            self._do_rollover()
        super().emit(record)

# 日志格式
formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# 根 logger
logger = logging.getLogger('grid_crawler')
logger.setLevel(logging.DEBUG)

# UI 队列 handler（INFO 级别）：替代 console_handler 指向 sg.Output 的方式
# 由主线程 drain 后更新 sg.Output，避免跨线程操作 Tkinter
ui_queue_handler = UiQueueHandler()
ui_queue_handler.setLevel(logging.INFO)
ui_queue_handler.setFormatter(formatter)

# 文件 handler（DEBUG 级别，保留最近 5MB，最多 3 个备份）
file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)

# 每日覆盖文件 handler（INFO 级别，固定文件名，每天自动清空前一天）
daily_handler = DailyFileHandler(DAILY_LOG_FILE, encoding='utf-8')
daily_handler.setLevel(logging.INFO)
daily_handler.setFormatter(formatter)

# 添加 handler（不再添加 console_handler，避免 stream 指向 sg.OutputStream 产生跨线程写）
logger.addHandler(ui_queue_handler)
logger.addHandler(file_handler)
logger.addHandler(daily_handler)

# 简化的快捷函数
def info(msg):
    logger.info(msg)

def debug(msg):
    logger.debug(msg)

def warning(msg):
    logger.warning(msg)

def error(msg):
    logger.error(msg)

def exception(msg):
    logger.exception(msg)