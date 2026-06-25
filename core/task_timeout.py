import threading
import functools

class TaskTimeoutException(Exception):
    pass

def with_timeout(seconds=120):
    """装饰器：为任务设置最大执行时间（秒）"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            result = [None]
            exception = [None]
            finished = threading.Event()

            def target():
                try:
                    result[0] = func(*args, **kwargs)
                except Exception as e:
                    exception[0] = e
                finally:
                    finished.set()

            thread = threading.Thread(target=target, daemon=True)
            thread.start()
            if not finished.wait(timeout=seconds):
                raise TaskTimeoutException(f"任务 {func.__name__} 超时（{seconds}秒）")
            if exception[0]:
                raise exception[0]
            return result[0]
        return wrapper
    return decorator