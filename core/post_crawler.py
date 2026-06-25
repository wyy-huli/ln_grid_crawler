# core/post_crawler.py
import json
import os
import requests
from playwright.sync_api import sync_playwright
from auth.auth_utils import is_auth_valid
from database.db_manager import save_type4_data, log_failure
from utils.config import AUTH_FILE, TYPE4_URL, TYPE4_BODY_TEMPLATE
from utils.logger import logger


def _get_cookies_from_auth():
    """从 auth.json 提取 cookies 字典"""
    try:
        with open(AUTH_FILE, 'r') as f:
            storage = json.load(f)
        cookies = {c['name']: c['value'] for c in storage.get('cookies', [])}
        return cookies
    except Exception as e:
        logger.error(f"读取auth.json失败: {e}")
        return {}

def _playwright_fetch_type4():
    captured_data = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            storage_state=AUTH_FILE if os.path.exists(AUTH_FILE) else None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        try:
            # 1. 打开首页
            page.goto("https://pmos.ln.sgcc.com.cn", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            # 2. 点击“信息披露”
            page.click("text=信息披露")
            page.wait_for_timeout(1500)

            # 3. 点击“综合查询”，监听新窗口
            with context.expect_page() as new_page_info:
                page.click("text=综合查询")
            new_page = new_page_info.value
            new_page.wait_for_load_state("networkidle")
            page = new_page
            page.bring_to_front()
            page.wait_for_timeout(3000)

            # 4. 点击“电网运行”
            page.click("text=电网运行")
            page.wait_for_timeout(3000)

            # 5. 精确点击“机组状态”（避免点到“机组状态（新）”）
            target = page.locator("text=机组状态").filter(has_not_text="（新）").first
            target.evaluate("node => node.click()")
            page.wait_for_timeout(5000)

            # ========== 填写 T-1 日期 ==========
            from datetime import date, timedelta
            t_minus_one = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
            date_input = page.locator("input.el-input__inner[placeholder='请选择日期']")
            date_input.wait_for(state="visible", timeout=10000)
            date_input.fill(t_minus_one)
            page.keyboard.press("Tab")   # 移开焦点，确保日期生效
            page.wait_for_timeout(500)
            # ===================================

            # 6. 点击查询按钮
            query_btn = page.get_by_role("button", name="查 询")
            if not query_btn.is_visible():
                query_btn = page.locator("button:has(span:text('查 询'))").first
            query_btn.scroll_into_view_if_needed()
            query_btn.evaluate("node => node.click()")

            # 7. 等待目标响应
            with page.expect_response(
                lambda resp: resp.url == TYPE4_URL and resp.status == 200,
                timeout=30000,
            ) as resp_info:
                pass

            response = resp_info.value
            data = response.json()
            if data.get("status") == 0:
                obj_list = data.get("data", {}).get("objectList", {}).get("list", [])
                captured_data = obj_list
                print(f"[类型4] 成功捕获 {len(captured_data)} 条记录")

        except Exception as e:
            logger.error(f"[类型4] 异常: {e}")
            page.screenshot(path="type4_error.png")
        finally:
            context.close()
            browser.close()

    if captured_data:
        save_type4_data(captured_data)
        return True
    return False

def run_type4():
    """类型4入口，无数据时不重试"""
    logger.info("开始执行类型4抓取（机组状态）")
    if not is_auth_valid():
        log_failure("type4_unit_status", "auth_expired")
        return False

    success = _playwright_fetch_type4()
    if not success:
        # 无数据或失败，仅记录日志，不触发重试
        log_failure("type4_unit_status", "no_data_or_failed")
        logger.warning("类型4抓取结束：无数据或失败")
    return success
if __name__ == '__main__':
    run_type4()