# auth/login.py
import os, time, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright
from utils.config import AUTH_FILE
from utils.logger import logger
from core.browser_guard import launch_browser, apply_stealth_patches

def manual_login():
    """手动登录，打开浏览器让用户完成登录操作。

    注意：登录场景必须用非 headless 模式（headless=False），让用户能看到浏览器完成交互。
    launch_browser 的 headless 参数显式传 False，不受全局 BROWSER_HEADLESS 配置影响。
    """
    browser = None
    context = None
    try:
        with sync_playwright() as p:
            browser = launch_browser(p, headless=False)
            context = browser.new_context()
            page = context.new_page()
            apply_stealth_patches(page)
            page.goto("https://pmos.ln.sgcc.com.cn", timeout=60000)
            logger.info("请在弹出的浏览器中完成登录，程序将自动检测并保存状态。")
            timeout = 300
            start = time.time()
            while time.time() - start < timeout:
                cookies = context.cookies()
                if any(c['name'] == 'Admin-Token' and c['value'] for c in cookies):
                    context.storage_state(path=AUTH_FILE)
                    logger.info(f"登录状态已保存到 {AUTH_FILE}")
                    break
                time.sleep(3)
            else:
                logger.info("登录超时，请重试。")
    except Exception as e:
        logger.error(f"[登录] 异常: {e}")
        raise
    finally:
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass

if __name__ == "__main__":
    manual_login()