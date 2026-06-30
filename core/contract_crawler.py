import json
import time
import datetime
import requests
from auth.auth_utils import is_auth_valid
from database.db_manager import upsert_contract_basic, save_contract_daily_data, log_failure
from utils.config import CONTRACT_CURVE_BASE_URL, CONTRACT_CURVE_DETAIL_URL, AUTH_FILE


def _build_contract_headers(target_url):
    """构建合同分时曲线接口所需的请求头"""
    with open(AUTH_FILE, 'r') as f:
        storage = json.load(f)
    
    cookies = {c['name']: c['value'] for c in storage.get('cookies', [])}
    
    x_uid = '2217636810955'
    for origin in storage.get('origins', []):
        for item in origin.get('localStorage', []):
            if item['name'] in ('userId', 'id'):
                x_uid = item['value']
                break
    
    timestamp_ms = str(int(time.time() * 1000))
    route = f"/pxf-dif-contract-extranet/mediumAndLongtermCurve/contractDivisionResolveCurve?date={timestamp_ms}"
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        "ClientTag": cookies.get("ClientTag", "OUTNET_BROWSE"),
        "Connection": "keep-alive",
        "Content-Type": "application/json;charset=UTF-8",
        "CurrentRoute": route,
        "Origin": "https://pmos.ln.sgcc.com.cn",
        "Referer": "https://pmos.ln.sgcc.com.cn/pxf-dif-contract-extranet/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
        "X-Ticket": cookies.get("X-Ticket", ""),
        "sec-ch-ua": "\"Google Chrome\";v=\"149\", \"Chromium\";v=\"149\", \"Not)A;Brand\";v=\"24\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "x-uid": x_uid,
    }
    return headers, cookies


def fetch_contract_list(month_str, log_callback=None):
    """
    获取指定月份的合同列表
    month_str: 'YYYY-MM' 格式
    返回合同列表 [{contract_id, contract_name, seller, buyer, ...}, ...]
    """
    if log_callback:
        log_callback(f"正在获取 {month_str} 月份的合同列表...")
    
    headers, cookies = _build_contract_headers(CONTRACT_CURVE_BASE_URL)
    
    change_month = f"{month_str}-01T00:00:00.000Z"
    
    payload = {
        "data": {
            "sequenceId": None,
            "isTypicalCurve": "",
            "changeMonth": change_month,
            "contractType": "",
            "isPause": "0",
            "energyChangeStatus": "",
            "priceChangeStatus": ""
        },
        "pageInfo": {
            "pageNum": 1,
            "pageSize": 1000,
            "size": 0,
            "startRow": 1,
            "endRow": 1,
            "pages": 0,
            "prePage": 0,
            "nextPage": 0,
            "isFirstPage": True,
            "isLastPage": True,
            "hasPreviousPage": False,
            "navigatePages": 0,
            "navigatepageNums": [],
            "navigateFirstPage": 1,
            "navigateLastPage": 1,
            "total": 0
        }
    }
    
    try:
        resp = requests.post(CONTRACT_CURVE_BASE_URL, cookies=cookies, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")
        
        data = resp.json()
        if data.get('status') != 0:
            raise Exception(data.get('message', '获取合同列表失败'))
        
        contract_list = data.get('data', {}).get('list', [])
        if log_callback:
            log_callback(f"共获取到 {len(contract_list)} 个合同")
        
        return contract_list
    except Exception as e:
        if log_callback:
            log_callback(f"获取合同列表失败: {e}")
        raise e


def fetch_contract_detail(contract_id, month_str, log_callback=None):
    """
    获取单个合同的详细96点数据
    contract_id: 合同ID（对应详情接口返回的CONTRACT_ID）
    month_str: 'YYYY-MM' 格式
    返回数组，每条记录是一天的96点数据
    """
    if log_callback:
        log_callback(f"  获取合同 {contract_id} 的96点数据...")
    
    headers, cookies = _build_contract_headers(CONTRACT_CURVE_DETAIL_URL)
    
    change_month = f"{month_str}-01 00:00:00"
    
    payload = {
        "contractId": contract_id,
        "changeMonth": change_month
    }
    
    try:
        resp = requests.post(CONTRACT_CURVE_DETAIL_URL, cookies=cookies, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}")
        
        resp_text = resp.text
        if log_callback:
            preview = resp_text[:500].replace('\n', ' ')
            log_callback(f"  响应预览: {preview}...")
        
        data = json.loads(resp_text)
        if data.get('status') != 0:
            raise Exception(data.get('message', '获取合同详情失败'))
        
        # 详情数据直接是数组，每条记录是一天
        detail_data = data.get('data', [])
        
        if log_callback:
            log_callback(f"  详情记录数: {len(detail_data)}")
            if detail_data:
                first = detail_data[0]
                keys = list(first.keys())[:20]
                log_callback(f"  第一条记录字段(前20): {keys}")
                log_callback(f"  第一条记录ROLE: {first.get('ROLE')}, GEN_DATE: {first.get('GEN_DATE')}")
                log_callback(f"  POINT1: {first.get('POINT1')}, POINT60: {first.get('POINT60')}, PRICE1: {first.get('PRICE1')}")
                non_zero = sum(1 for i in range(1, 97) if first.get(f'POINT{i}') not in (None, 0, 0.0))
                log_callback(f"  第一条记录非零POINT数: {non_zero}")
                
                # 打印购方第一条记录的详细信息
                buyer_first = None
                for item in detail_data:
                    if str(item.get('ROLE', '')) == '购方':
                        buyer_first = item
                        break
                if buyer_first:
                    log_callback(f"  --- 购方第一条记录 ---")
                    log_callback(f"  GEN_DATE: {buyer_first.get('GEN_DATE')}")
                    log_callback(f"  ENERGY: {buyer_first.get('ENERGY')}, PRICE: {buyer_first.get('PRICE')}")
                    log_callback(f"  POINT1: {buyer_first.get('POINT1')}, type={type(buyer_first.get('POINT1')).__name__}")
                    log_callback(f"  POINT60: {buyer_first.get('POINT60')}")
                    log_callback(f"  POINT70: {buyer_first.get('POINT70')}")
                    log_callback(f"  POINT80: {buyer_first.get('POINT80')}")
                    buyer_non_zero = sum(1 for i in range(1, 97) if buyer_first.get(f'POINT{i}') not in (None, 0, 0.0))
                    log_callback(f"  购方第一条记录非零POINT数: {buyer_non_zero}")
                    # 打印所有非零的点
                    non_zero_points = [(i, buyer_first.get(f'POINT{i}')) for i in range(1, 97) if buyer_first.get(f'POINT{i}') not in (None, 0, 0.0)]
                    log_callback(f"  购方非零POINT列表: {non_zero_points}")
        
        return detail_data
    except Exception as e:
        if log_callback:
            log_callback(f"  获取合同 {contract_id} 详情失败: {e}")
        raise e


def parse_contract_basic(contract_item):
    """解析合同基础信息"""
    return {
        'contract_id': contract_item.get('contractId', ''),
        'contract_name': contract_item.get('contractName', ''),
        'seller': contract_item.get('saleParticipantname', ''),   # 售方名称
        'buyer': contract_item.get('vendeeParticipantname', ''),   # 购方名称
        'contract_type': contract_item.get('typeName', ''),        # 合同类型名称
        'contract_sequence': contract_item.get('sequenceName', ''), # 合同序列名称
        'contract_electricity': contract_item.get('contractEnergy'),
        'monthly_electricity': contract_item.get('contractQty'),
        'monthly_price': contract_item.get('price'),
        'curve_status': str(contract_item.get('difStatus', '')),
        'settlement_point': contract_item.get('contractPointName', ''),
    }


def parse_curve_data(detail_data, contract_id, log_callback=None):
    """
    解析合同曲线数据，只提取购方数据
    detail_data: 数组，每条记录是一天的96点数据
    返回 list of {curve_date, electricity_data: {time_point: value}, price_data: {time_point: value}}
    """
    result = []
    
    if not detail_data:
        if log_callback:
            log_callback(f"  警告: detail_data 为空")
        return result
    
    # 自动检测字段名（兼容大小写和不同命名）
    first_item = detail_data[0]
    all_keys = list(first_item.keys())
    
    # 检测ROLE字段
    role_field = None
    for key in all_keys:
        if key.upper() == 'ROLE' or 'ROLE' in key.upper():
            role_field = key
            break
    if role_field is None:
        if log_callback:
            log_callback(f"  警告: 未找到ROLE字段，所有字段: {all_keys}")
        return result
    
    # 检测日期字段
    date_field = None
    for key in all_keys:
        if key.upper() in ('GEN_DATE', 'CURVEDATE', 'CURVE_DATE', 'DATE'):
            date_field = key
            break
    if date_field is None:
        date_field = 'GEN_DATE'
    
    # 检测电量字段前缀（POINT/point/elec等）
    point_prefix = None
    for key in all_keys:
        if key.upper().startswith('POINT') and key[5:].isdigit():
            point_prefix = key[:5]
            break
    if point_prefix is None:
        # 尝试其他可能的前缀
        for key in all_keys:
            upper_key = key.upper()
            if (upper_key.startswith('ELEC') or upper_key.startswith('POWER') or upper_key.startswith('ENERGY')) and any(c.isdigit() for c in key):
                # 提取前缀
                import re
                m = re.match(r'([A-Za-z_]+)\d+', key)
                if m:
                    point_prefix = m.group(1)
                    break
    
    # 检测电价字段前缀
    price_prefix = None
    for key in all_keys:
        if key.upper().startswith('PRICE') and key[5:].isdigit():
            price_prefix = key[:5]
            break
    if price_prefix is None:
        for key in all_keys:
            upper_key = key.upper()
            if upper_key.startswith('PRICE') and any(c.isdigit() for c in key):
                import re
                m = re.match(r'([A-Za-z_]+)\d+', key)
                if m:
                    price_prefix = m.group(1)
                    break
    
    if log_callback:
        log_callback(f"  字段检测: ROLE={role_field}, DATE={date_field}, 电量前缀={point_prefix}, 电价前缀={price_prefix}")
    
    # 筛选购方记录
    buyer_items = []
    for item in detail_data:
        role_val = str(item.get(role_field, ''))
        if '购' in role_val or 'buyer' in role_val.lower() or 'BUYER' in role_val.upper():
            buyer_items.append(item)
    
    if log_callback:
        log_callback(f"  购方记录数: {len(buyer_items)} / {len(detail_data)}")
    
    for item in buyer_items:
        curve_date = str(item.get(date_field, ''))
        if not curve_date:
            continue
        
        electricity_data = {}
        price_data = {}
        
        # 解析电量数据
        if point_prefix:
            for i in range(1, 97):
                point_key = f'{point_prefix}{i}'
                value = item.get(point_key)
                if value is not None:
                    try:
                        float_val = float(value)
                    except (ValueError, TypeError):
                        float_val = 0.0
                    hour = (i - 1) // 4
                    minute = ((i - 1) % 4) * 15
                    time_point = f"{hour:02d}:{minute:02d}"
                    electricity_data[time_point] = float_val
        
        # 解析电价数据
        if price_prefix:
            for i in range(1, 97):
                price_key = f'{price_prefix}{i}'
                value = item.get(price_key)
                if value is not None:
                    try:
                        float_val = float(value)
                    except (ValueError, TypeError):
                        float_val = 0.0
                    hour = (i - 1) // 4
                    minute = ((i - 1) % 4) * 15
                    time_point = f"{hour:02d}:{minute:02d}"
                    price_data[time_point] = float_val
        
        if electricity_data or price_data:
            result.append({
                'curve_date': curve_date,
                'electricity_data': electricity_data,
                'price_data': price_data,
            })
    
    if log_callback and result:
        first_day = result[0]
        non_zero_elec = sum(1 for v in first_day['electricity_data'].values() if v and v != 0)
        non_zero_price = sum(1 for v in first_day['price_data'].values() if v and v != 0)
        log_callback(f"  第一天({first_day['curve_date']}) 非零电量点: {non_zero_elec}, 非零电价点: {non_zero_price}")
    
    return result


def fetch_month_contract_data(month_str, log_callback=None):
    """
    抓取指定月份所有合同的购方96点数据
    month_str: 'YYYY-MM' 格式
    返回 (成功数量, 失败列表)
    """
    if not is_auth_valid():
        if log_callback:
            log_callback("登录状态已失效，请重新登录后再执行")
        return 0, ["auth_expired"]
    
    success_count = 0
    failures = []
    
    try:
        contract_list = fetch_contract_list(month_str, log_callback)
    except Exception as e:
        return 0, [("合同列表", str(e))]
    
    for contract_item in contract_list:
        # 使用 contractId 作为合同ID
        contract_id = contract_item.get('contractId', '')
        if not contract_id:
            continue
        
        try:
            contract_basic = parse_contract_basic(contract_item)
            upsert_contract_basic(contract_basic)
            
            detail_data = fetch_contract_detail(contract_id, month_str, log_callback)
            daily_data_list = parse_curve_data(detail_data, contract_id, log_callback)
            
            for daily_data in daily_data_list:
                save_contract_daily_data(
                    contract_id=contract_id,
                    curve_date_str=daily_data['curve_date'],
                    electricity_data=daily_data['electricity_data'],
                    price_data=daily_data['price_data'],
                    log_callback=log_callback
                )
                success_count += 1
            
            if log_callback:
                log_callback(f"  合同 {contract_id} 处理完成，共 {len(daily_data_list)} 天")
        
        except Exception as e:
            failures.append((contract_id, str(e)))
            if log_callback:
                log_callback(f"  合同 {contract_id} 处理失败: {e}")
        
        time.sleep(0.5)
    
    return success_count, failures


if __name__ == '__main__':
    month = datetime.datetime.now().strftime('%Y-%m')
    print(f"测试抓取 {month} 月份合同数据...")
    success, fails = fetch_month_contract_data(month, log_callback=print)
    print(f"抓取完成，成功 {success} 条，失败 {len(fails)} 条")
    if fails:
        print("失败列表:")
        for f in fails:
            print(f"  {f}")