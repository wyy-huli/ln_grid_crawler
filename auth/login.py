# auth/login.py
import os, time, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from playwright.sync_api import sync_playwright
from utils.config import AUTH_FILE

def manual_login():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://pmos.ln.sgcc.com.cn")
        print("请在弹出的浏览器中完成登录，程序将自动检测并保存状态。")
        timeout = 300
        start = time.time()
        while time.time() - start < timeout:
            cookies = context.cookies()
            if any(c['name'] == 'Admin-Token' and c['value'] for c in cookies):
                context.storage_state(path=AUTH_FILE)
                print(f"登录状态已保存到 {AUTH_FILE}")
                break
            time.sleep(3)
        else:
            print("登录超时，请重试。")
        context.close()
        browser.close()

if __name__ == "__main__":
    manual_login()