# core/chart_crawler.py
import datetime
import os
from playwright.sync_api import sync_playwright
from auth.auth_utils import is_auth_valid
from database.db_manager import (
    save_type1_batch,
    upsert_type2_data,
    log_failure,
)
from utils.config import (
    SIMPLE_TYPE1_APIS,
    DROP_GROUPS,
    TYPE2_APIS,
    REALTIME_REPORT_URL,
    AUTH_FILE,
)


def run_simple_type1(api_cfg, manual_date=None):
    """
    执行单个普通类型1接口的抓取（无下拉切换）
    manual_date: 'YYYY-MM-DD' 可选，用于手动补抓指定日期；默认按配置偏移量计算
    返回 True 表示成功入库，False 表示失败
    """
    api_name = api_cfg.get("api_name", api_cfg["api_code"])
    if not is_auth_valid():
        log_failure(api_cfg["api_code"], "auth_expired")
        return False

    if manual_date:
        target_date = manual_date
    else:
        target_date = (
            datetime.date.today() + datetime.timedelta(days=api_cfg["date_offset"])
        ).isoformat()

    captured = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = browser.new_context(
                storage_state=AUTH_FILE if os.path.exists(AUTH_FILE) else None,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/148.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(api_cfg["report_url"], timeout=30000)
            page.wait_for_selector("input.fr-trigger-texteditor", timeout=15000)
            page.locator("input.fr-trigger-texteditor").fill(target_date)
            page.keyboard.press("Tab")
            page.wait_for_timeout(500)

            # 查询按钮：蓝色背景 + 文本“查 询”，取最后一个
            search_btn = page.locator(
                'div[style*="background: rgb(24, 144, 255)"]:has(span:text("查 询"))'
            ).last

            with page.expect_response(
                lambda resp: "writer_out_html" in resp.url and resp.status == 200,
                timeout=120000,
            ) as resp_info:
                search_btn.click()

            response = resp_info.value
            j = response.json()
            if j["chartAttr"]["title"]["text"] == api_cfg["title_text"]:
                data = j["chartAttr"]["series"][0]["data"]
                # 时间点统一截取前5位（HH:MM）
                for d in data:
                    d["x"] = d["x"][:5]
                captured.extend(data)
            else:
                print(
                    f"[{api_name}] 标题不匹配，期待 {api_cfg['title_text']}，"
                    f"实际 {j['chartAttr']['title']['text']}"
                )
        except Exception as e:
            print(f"[{api_name}] 抓取异常: {e}")
            log_failure(api_cfg["api_code"], str(e))
            return False
        finally:
            context.close()
            browser.close()

    if captured:
        save_type1_batch(api_cfg["api_code"], api_name, target_date, captured)
        return True
    else:
        log_failure(api_cfg["api_code"], "no_data")
        return False


def run_dropdown_group(group_cfg, manual_date=None):
    group_name = group_cfg["group_name"]
    if not is_auth_valid():
        for opt in group_cfg["options"]:
            log_failure(opt["api_code"], "auth_expired")
        return False

    if manual_date:
        target_date = manual_date
    else:
        target_date = (
            datetime.date.today() + datetime.timedelta(days=group_cfg["date_offset"])
        ).isoformat()

    captured = {opt["api_code"]: [] for opt in group_cfg["options"]}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = browser.new_context(
                storage_state=AUTH_FILE if os.path.exists(AUTH_FILE) else None,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/148.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(group_cfg["report_url"], timeout=30000)

            page.wait_for_selector("input.fr-trigger-texteditor", timeout=15000)
            date_input = page.locator("input.fr-trigger-texteditor").first
            date_input.fill(target_date)
            page.wait_for_timeout(300)
            page.mouse.click(0, 0)
            page.wait_for_timeout(500)

            for opt in group_cfg["options"]:
                api_name = opt.get("api_name", opt["api_code"])
                try:
                    # 1. 尝试切换下拉选项
                    option_switched = False
                    try:
                        # 使用文本直接点击下拉触发器（部分页面可能用 label 作为触发器）
                        page.locator("text=风电").first.click()  # 或其他通用方式
                    except:
                        pass
                    # 通用方法：寻找所有可能的触发器
                    if not option_switched:
                        # 先点击可能的触发器（带箭头的 div 或 input）
                        dropdown_candidates = page.locator(".fr-trigger-btn-up, .fr-trigger-text, input[type='text']")
                        for i in range(dropdown_candidates.count()):
                            trigger = dropdown_candidates.nth(i)
                            if trigger.is_visible():
                                trigger.click()
                                page.wait_for_timeout(300)
                                # 尝试在弹出层中点击选项文本
                                option = page.get_by_text(opt["option_text"], exact=True)
                                if option.is_visible():
                                    option.click()
                                    page.wait_for_timeout(300)
                                    option_switched = True
                                    break
                        # 如果仍未找到，尝试直接输入文本（某些下拉支持搜索）
                        if not option_switched:
                            date_input.fill(opt["option_text"])
                            page.keyboard.press("Enter")
                            page.wait_for_timeout(300)
                            option_switched = True
                    # 2. 点击查询按钮
                    search_btn = page.locator(
                        'div[style*="background: rgb(24, 144, 255)"]:has(span:text("查 询"))'
                    ).last
                    with page.expect_response(
                        lambda resp: "writer_out_html" in resp.url and resp.status == 200,
                        timeout=120000,
                    ) as resp_info:
                        search_btn.click()

                    response = resp_info.value
                    j = response.json()
                    # 安全提取数据
                    chart_attr = j.get("chartAttr", {})
                    series = chart_attr.get("series", [])

                    if series and len(series) > 0 and "data" in series[0]:
                        data = series[0]["data"]
                        for d in data:
                            d["x"] = d["x"][:5]
                        captured[opt["api_code"]].extend(data)
                        print(f"[{group_name}] {api_name} 捕获 {len(data)} 条")
                    else:
                        print(f"[{group_name}] {api_name} 响应中无数据")
                        log_failure(opt["api_code"], "empty_response")
                except Exception as e:
                    print(f"[{group_name}] {api_name} 失败: {e}")
                    log_failure(opt["api_code"], str(e))
        except Exception as e:
            print(f"[{group_name}] 整体异常: {e}")
            for opt in group_cfg["options"]:
                log_failure(opt["api_code"], f"group_error: {e}")
            return False
        finally:
            context.close()
            browser.close()

    any_success = False
    for opt in group_cfg["options"]:
        if captured[opt["api_code"]]:
            save_type1_batch(
                opt["api_code"],
                opt.get("api_name", opt["api_code"]),
                target_date,
                captured[opt["api_code"]],
            )
            any_success = True
        else:
            log_failure(opt["api_code"], "no_data")
    return any_success


def run_type2():
    """实时出清参考信息（每15分钟调用），返回 True/False"""
    print("[实时] run_type2 开始执行...")
    today = datetime.date.today().isoformat()
    if not is_auth_valid():
        log_failure("realtime_clearing", "auth_expired")
        return False

    captured = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome",
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = browser.new_context(
                storage_state=AUTH_FILE if os.path.exists(AUTH_FILE) else None,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/148.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(REALTIME_REPORT_URL, timeout=30000)
            page.wait_for_selector("input.fr-trigger-texteditor", timeout=15000)
            page.locator("input.fr-trigger-texteditor").fill(today)
            page.keyboard.press("Tab")
            page.wait_for_timeout(500)

            search_btn = page.locator("div[widgetname='FORMSUBMIT0']")
            with page.expect_response(
                lambda resp: "writer_out_html" in resp.url and resp.status == 200,
                timeout=120000,
            ) as resp_info:
                search_btn.click()

            response = resp_info.value
            j = response.json()
            if j["chartAttr"]["title"]["text"] == "实时出清参考信息":
                data = j["chartAttr"]["series"][0]["data"]
                for d in data:
                    d["x"] = d["x"][:5]
                captured.extend(data)
        except Exception as e:
            print(f"[实时] 抓取异常: {e}")
            log_failure("realtime_clearing", str(e))
            return False
        finally:
            context.close()
            browser.close()

    if captured:
        upsert_type2_data("realtime_clearing", "实时出清参考信息", today, captured)
        return True
    else:
        log_failure("realtime_clearing", "no_data")
        return False


def manual_fetch(api_code, date_str=None):
    """
    手动补抓接口，可通过 GUI 调用。
    api_code: 接口标识
    date_str: 可选，格式 'YYYY-MM-DD'，不传则按配置偏移量（默认当天）
    """
    # 查找普通类型1
    for api in SIMPLE_TYPE1_APIS:
        if api["api_code"] == api_code:
            return run_simple_type1(api, manual_date=date_str)
    # 查找下拉组（补抓整个组）
    for group in DROP_GROUPS:
        for opt in group["options"]:
            if opt["api_code"] == api_code:
                return run_dropdown_group(group, manual_date=date_str)
    # 类型2
    if api_code == "realtime_clearing":
        return run_type2()
    print(f"未找到接口: {api_code}")
    return False


# 测试入口（可选，直接运行本文件测试）
if __name__ == "__main__":
    print(">>> 测试手动补抓...")
    # 修改为你想测试的 api_code
    manual_fetch("sys_load_w", None)