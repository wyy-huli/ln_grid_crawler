# core/chart_crawler.py
import time
import datetime
import os
import traceback
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
    BROWSER_HEADLESS,
)
from utils.logger import logger
from core.browser_guard import launch_browser, apply_stealth_patches


def run_simple_type1(api_cfg, manual_date=None):
    """
    执行单个普通类型1接口的抓取（无下拉切换）
    manual_date: 'YYYY-MM-DD' 可选，用于手动补抓指定日期；默认按配置偏移量计算
    返回 True 表示成功入库，False 表示失败
    """
    api_name = api_cfg.get("api_name", api_cfg["api_code"])
    api_code = api_cfg["api_code"]
    target_date = manual_date or (
        datetime.date.today() + datetime.timedelta(days=api_cfg["date_offset"])
    ).isoformat()

    logger.info(f"[{api_name}] 开始抓取: 接口={api_code}, 日期={target_date}")

    if not is_auth_valid():
        logger.error(f"[{api_name}] 登录状态已失效")
        log_failure(api_code, "auth_expired")
        return False

    captured = []
    context = None
    with sync_playwright() as p:
        browser = launch_browser(p)
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
            apply_stealth_patches(page)
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
            title = j.get("chartAttr", {}).get("title", {}).get("text", "")
            if title == api_cfg["title_text"]:
                series = j.get("chartAttr", {}).get("series", [])
                if not series:
                    logger.warning(f"[{api_name}] 接口返回 series 为空")
                    log_failure(api_code, "empty_series")
                    return False
                data = series[0].get("data", [])
                if not data:
                    logger.warning(f"[{api_name}] 接口返回 data 为空")
                    log_failure(api_code, "empty_data")
                    return False
                # 时间点统一截取前5位（HH:MM）
                for d in data:
                    d["x"] = d["x"][:5]
                captured.extend(data)
            else:
                logger.warning(
                    f"[{api_name}] 标题不匹配，期待 {api_cfg['title_text']}，"
                    f"实际 {title}"
                )
        except Exception as e:
            logger.error(f"[{api_name}] 抓取异常: {e}")
            try:
                log_failure(api_cfg["api_code"], str(e))
            except Exception as le:
                logger.error(f"[{api_name}] log_failure 异常（吞掉）: {le}")
            return False
        finally:
            # close 失败不能抛出，否则会掩盖原始异常并影响 worker 线程状态
            try:
                if context is not None:
                    context.close()
            except Exception as ce:
                logger.error(f"[{api_name}] context.close 异常（吞掉）: {ce}")
            try:
                browser.close()
            except Exception as be:
                logger.error(f"[{api_name}] browser.close 异常（吞掉）: {be}")

    if captured:
        try:
            save_type1_batch(api_code, api_name, target_date, captured)
            logger.info(f"[{api_name}] 抓取成功: 捕获 {len(captured)} 条数据")
        except Exception as e:
            logger.error(f"[{api_name}] save_type1_batch 异常: {e}")
            try:
                log_failure(api_code, str(e))
            except Exception:
                pass
            return False
        return True
    else:
        logger.warning(f"[{api_name}] 抓取失败: 无数据返回")
        log_failure(api_code, "no_data")
        return False


def run_dropdown_group(group_cfg, manual_date=None):
    group_name = group_cfg["group_name"]
    target_date = manual_date or (
        datetime.date.today() + datetime.timedelta(days=group_cfg["date_offset"])
    ).isoformat()

    logger.info(f"[{group_name}] 开始抓取下拉组: 日期={target_date}")

    if not is_auth_valid():
        logger.error(f"[{group_name}] 登录状态已失效")
        for opt in group_cfg["options"]:
            log_failure(opt["api_code"], "auth_expired")
        return False

    captured = {opt["api_code"]: [] for opt in group_cfg["options"]}
    context = None
    
    OPTION_TIMEOUT = 90
    page_timeout = OPTION_TIMEOUT * 1000

    with sync_playwright() as p:
        browser = launch_browser(p)
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
            apply_stealth_patches(page)
            page.set_default_timeout(page_timeout)

            page.goto(group_cfg["report_url"], timeout=30000)

            page.wait_for_selector("input.fr-trigger-texteditor", timeout=15000)
            date_input = page.locator("input.fr-trigger-texteditor").first
            date_input.fill(target_date)
            page.wait_for_timeout(300)
            page.mouse.click(0, 0)
            page.wait_for_timeout(500)

            for opt in group_cfg["options"]:
                api_name = opt.get("api_name", opt["api_code"])
                option_start = time.time()
                try:
                    option_switched = False
                    try:
                        page.locator("text=风电").first.click()
                    except:
                        pass
                    
                    if not option_switched:
                        dropdown_candidates = page.locator(".fr-trigger-btn-up, .fr-trigger-text, input[type='text']")
                        for i in range(dropdown_candidates.count()):
                            trigger = dropdown_candidates.nth(i)
                            if trigger.is_visible():
                                trigger.click()
                                page.wait_for_timeout(300)
                                option = page.get_by_text(opt["option_text"], exact=True)
                                if option.is_visible():
                                    option.click()
                                    page.wait_for_timeout(300)
                                    option_switched = True
                                    break
                        if not option_switched:
                            date_input.fill(opt["option_text"])
                            page.keyboard.press("Enter")
                            page.wait_for_timeout(300)
                            option_switched = True

                    search_btn = page.locator(
                        'div[style*="background: rgb(24, 144, 255)"]:has(span:text("查 询"))'
                    ).last
                    
                    with page.expect_response(
                        lambda resp: "writer_out_html" in resp.url and resp.status == 200,
                        timeout=OPTION_TIMEOUT * 1000,
                    ) as resp_info:
                        search_btn.click()

                    response = resp_info.value
                    j = response.json()
                    chart_attr = j.get("chartAttr", {})
                    series = chart_attr.get("series", [])

                    if series and len(series) > 0 and "data" in series[0]:
                        data = series[0]["data"]
                        for d in data:
                            d["x"] = d["x"][:5]
                        captured[opt["api_code"]].extend(data)
                        logger.info(f"[{group_name}] {api_name} 捕获 {len(data)} 条，耗时 {time.time()-option_start:.1f}s")
                    else:
                        logger.info(f"[{group_name}] {api_name} 响应中无数据")
                        log_failure(opt["api_code"], "empty_response")
                except Exception as e:
                    elapsed = time.time() - option_start
                    logger.error(f"[{group_name}] {api_name} 失败: {e}，耗时 {elapsed:.1f}s")
                    if elapsed >= OPTION_TIMEOUT:
                        logger.warning(f"[{group_name}] {api_name} 疑似超时，跳过该选项继续")
                    log_failure(opt["api_code"], str(e))
        except Exception as e:
            logger.error(f"[{group_name}] 整体异常: {e}")
            for opt in group_cfg["options"]:
                try:
                    log_failure(opt["api_code"], f"group_error: {e}")
                except Exception:
                    pass
            return False
        finally:
            try:
                if context is not None:
                    context.close()
            except Exception as ce:
                logger.error(f"[{group_name}] context.close 异常（吞掉）: {ce}")
            try:
                browser.close()
            except Exception as be:
                logger.error(f"[{group_name}] browser.close 异常（吞掉）: {be}")

    # ========== 第二层修复：分选项即时入库 ==========
    # 原逻辑：所有选项抓取完成后，统一遍历 captured 入库
    # 问题：下拉组有2个选项（如风电+光伏），风电成功后抓光伏超时，
    #       Python进程被杀导致风电数据虽已入库但 result_queue 没来得及put，
    #       主进程判失败并触发重试；重试时需重抓2个选项，效率低且有再次超时风险。
    # 修复：
    #   1. 每个选项抓取成功后立即 save_type1_batch（不等其余选项）
    #      → 风电抓完立即入库，即使后续光伏超时，风电数据已保存不会丢失
    #   2. 成功计数在循环内即时统计，any_success 逻辑不变
    #   3. 返回值：只有所有选项成功才返回 True，部分成功返回 False
    #      → 部分成功时仍触发重试，补抓失败选项（save_type1_batch 是覆盖更新语义，重试安全）
    any_success = False
    success_count = 0
    fail_count = 0
    total_count = len(group_cfg["options"])
    for opt in group_cfg["options"]:
        api_name = opt.get("api_name", opt["api_code"])
        if captured[opt["api_code"]]:
            # 第二层修复：每抓完一个选项立即入库，不等全部完成
            try:
                save_type1_batch(
                    opt["api_code"],
                    api_name,
                    target_date,
                    captured[opt["api_code"]],
                )
                any_success = True
                success_count += 1
                logger.info(
                    f"[{group_name}] {api_name} 已即时入库 {len(captured[opt['api_code']])} 条 "
                    f"(进度 {success_count}/{total_count})"
                )
            except Exception as e:
                logger.error(f"[{group_name}] {api_name} 即时入库失败: {e}")
                try:
                    log_failure(opt["api_code"], str(e))
                except Exception:
                    pass
                fail_count += 1
        else:
            logger.warning(f"[{group_name}] {api_name} 无数据")
            log_failure(opt["api_code"], "no_data")
            fail_count += 1

    # 所有选项都成功才返回 True，否则返回 False 触发重试补抓失败项
    all_success = (success_count == total_count and total_count > 0)
    if all_success:
        logger.info(f"[{group_name}] 抓取完成: 成功 {success_count} 个接口（全部完成）")
    elif any_success:
        logger.info(
            f"[{group_name}] 部分完成: 成功 {success_count} 个, 失败 {fail_count} 个 "
            f"（成功项已即时入库，将触发重试补抓失败项）"
        )
    else:
        logger.error(f"[{group_name}] 抓取失败: 所有 {total_count} 个接口均无数据")
    return all_success


def run_type2(manual_date=None):
    """实时出清参考信息（每15分钟调用），返回 True/False
    manual_date: 'YYYY-MM-DD' 可选，用于手动补抓指定日期；默认按当天
    """
    target_date = manual_date or datetime.date.today().isoformat()
    logger.info(f"[实时出清] 开始抓取: 日期={target_date}")

    if not is_auth_valid():
        logger.error("[实时出清] 登录状态已失效")
        log_failure("realtime_clearing", "auth_expired")
        return False

    captured = []
    context = None
    with sync_playwright() as p:
        browser = launch_browser(p)
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
            apply_stealth_patches(page)
            page.goto(REALTIME_REPORT_URL, timeout=30000)
            page.wait_for_selector("input.fr-trigger-texteditor", timeout=15000)
            page.locator("input.fr-trigger-texteditor").fill(target_date)
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
            title = j.get("chartAttr", {}).get("title", {}).get("text", "")
            if title == "实时出清参考信息":
                series = j.get("chartAttr", {}).get("series", [])
                if not series:
                    logger.warning(f"[实时出清] 接口返回 series 为空，可能暂无数据")
                    log_failure("realtime_clearing", "empty_series")
                    return False
                data = series[0].get("data", [])
                if not data:
                    logger.warning(f"[实时出清] 接口返回 data 为空，可能暂无数据")
                    log_failure("realtime_clearing", "empty_data")
                    return False
                for d in data:
                    d["x"] = d["x"][:5]
                captured.extend(data)
            else:
                logger.warning(f"[实时出清] 标题不匹配，期待 实时出清参考信息，实际 {title}")
                log_failure("realtime_clearing", f"title_mismatch:{title}")
        except Exception as e:
            logger.error(f"[实时出清] 抓取异常: {e}")
            try:
                log_failure("realtime_clearing", str(e))
            except Exception as le:
                logger.error(f"[实时出清] log_failure 异常（吞掉）: {le}")
            return False
        finally:
            try:
                if context is not None:
                    context.close()
            except Exception as ce:
                logger.error(f"[实时出清] context.close 异常（吞掉）: {ce}")
            try:
                browser.close()
            except Exception as be:
                logger.error(f"[实时出清] browser.close 异常（吞掉）: {be}")

    if captured:
        try:
            upsert_type2_data("realtime_clearing", "实时出清参考信息", target_date, captured)
            logger.info(f"[实时出清] 抓取成功: 捕获 {len(captured)} 条数据")
        except Exception as e:
            logger.error(f"[实时出清] 存储异常: {e}")
            try:
                log_failure("realtime_clearing", str(e))
            except Exception:
                pass
            return False
        return True
    else:
        logger.warning(f"[实时出清] 抓取失败: 无数据返回")
        log_failure("realtime_clearing", "no_data")
        return False


def manual_fetch(api_code, date_str=None):
    """
    手动补抓接口，可通过 GUI 调用。
    api_code: 接口标识
    date_str: 可选，格式 'YYYY-MM-DD'，不传则按配置偏移量（默认当天）
    """
    logger.info(f"[手动补抓] 开始执行: 接口={api_code}, 日期={date_str or '默认'}")

    # 查找普通类型1
    for api in SIMPLE_TYPE1_APIS:
        if api["api_code"] == api_code:
            result = run_simple_type1(api, manual_date=date_str)
            if result:
                logger.info(f"[手动补抓] 成功: 接口={api_code}")
            else:
                logger.error(f"[手动补抓] 失败: 接口={api_code}")
            return result
    # 查找下拉组（补抓整个组）
    for group in DROP_GROUPS:
        for opt in group["options"]:
            if opt["api_code"] == api_code:
                result = run_dropdown_group(group, manual_date=date_str)
                if result:
                    logger.info(f"[手动补抓] 成功: 接口={api_code} (下拉组={group['group_name']})")
                else:
                    logger.error(f"[手动补抓] 失败: 接口={api_code} (下拉组={group['group_name']})")
                return result
    # 类型2
    if api_code == "realtime_clearing":
        result = run_type2(manual_date=date_str)
        if result:
            logger.info(f"[手动补抓] 成功: 接口={api_code}")
        else:
            logger.error(f"[手动补抓] 失败: 接口={api_code}")
        return result
    # 类型4（机组状态）：run_type4 内部固定取 T-1 日期，不接收 date_str 参数
    if api_code == "type4_unit_status":
        from core.post_crawler import run_type4
        logger.info("[手动补抓] 机组状态接口固定抓取 T-1 日期，忽略传入的日期参数")
        result = run_type4()
        if result:
            logger.info(f"[手动补抓] 成功: 接口={api_code}")
        else:
            logger.error(f"[手动补抓] 失败: 接口={api_code}")
        return result
    logger.error(f"[手动补抓] 未找到接口: {api_code}")
    return False


# 测试入口（可选，直接运行本文件测试）
if __name__ == "__main__":
    logger.info(">>> 测试手动补抓...")
    # 修改为你想测试的 api_code
    manual_fetch("sys_load_w", None)