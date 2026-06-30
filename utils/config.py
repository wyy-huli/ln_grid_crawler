# 所有API配置、URL
import datetime
import os
import sys

# ==================== 路径适配（支持打包和开发环境） ====================
def _get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _get_user_data_dir():
    if getattr(sys, 'frozen', False):
        app_data = os.environ.get('APPDATA', os.path.expanduser('~'))
        data_dir = os.path.join(app_data, 'LnGridCrawler')
        os.makedirs(data_dir, exist_ok=True)
        return data_dir
    return _get_base_dir()

BASE_DIR = _get_base_dir()
USER_DATA_DIR = _get_user_data_dir()

AUTH_FILE = os.path.join(USER_DATA_DIR, 'auth.json')
BROWSER_DATA_DIR = os.path.join(USER_DATA_DIR, 'browser_data')
DATA_DIR = os.path.join(USER_DATA_DIR, 'data')
LOG_DIR = os.path.join(DATA_DIR, 'logs')
DB_FILE = os.path.join(USER_DATA_DIR, 'grid_data.db')

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(BROWSER_DATA_DIR, exist_ok=True)

DATABASE_URL = f"sqlite:///{DB_FILE}"

# 普通类型1接口（无下拉）
SIMPLE_TYPE1_APIS = [    {
        "title_text": "短期系统负荷预测_周",
        "api_code": "sys_load_w",
        "api_name": "系统负荷预测(周)",
        "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_w_dqxtfhyc.frm&ref_t=design&ref_c=7368af68-545a-48dc-ae48-720f3e8e1fff",
        "date_offset": 1,
        "fetch_time": "09:00"
    },
    {"title_text": "系统负荷预测", "api_code": "sys_load_d", "api_name": "系统负荷预测(日)","report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_xtfhyc.frm&ref_t=design&ref_c=2ace123a-94db-4163-aee1-8fd5043df37c","date_offset": 1, "fetch_time": "09:03"},
    {"title_text": "联络线预计划", "api_code": "interline_pred", "api_name": "省间联络线输电曲线预测-日前系统间联络线输电曲线预测","report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_llxyjh.frm", "date_offset": 1, "fetch_time": "09:05"},
    {"title_text": "联络线终计划预测", "api_code": "interline_final", "api_name": "省间联络线输电曲线预测-日前联络线终计划", "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_llxzjhyc.frm&ref_t=design&ref_c=6646dd9c-46a8-4cf4-abfd-a19f681eca30","date_offset": 1, "fetch_time": "18:05"},
    # {"title_text": "发电总出力预测", "api_code": "gen_pred", "api_name": "发电总出力预测", "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_fdzclyc.frm&ref_t=design&ref_c=1af4aa32-7c52-4327-b0b4-b421f2a1386d","date_offset": -1, "fetch_time": "09:13"},
    {"title_text": "分散式风电预测", "api_code": "disp_wind_pred", "api_name": "分散式风电预测","report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_fssfdyc.frm&ref_t=design&ref_c=41e73001-51fe-4b68-9169-d7b26cf09d22", "date_offset": 1, "fetch_time": "09:23"},
    {"title_text": "分布式光伏预测", "api_code": "dist_pv_pred", "api_name": "分布式光伏预测", "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_fbsgfyc.frm&ref_t=design&ref_c=41e73001-51fe-4b68-9169-d7b26cf09d22","date_offset": 1, "fetch_time": "09:25"},
    {"title_text": "水电计划_周", "api_code": "hydro_w_pred", "api_name": "水电(含抽蓄)总出力预测(周)", "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_w_sdjh.frm&ref_t=design&ref_c=7368af68-545a-48dc-ae48-720f3e8e1fff","date_offset": 1, "fetch_time": "09:27"},
    {"title_text": "水电总出力预测", "api_code": "hydro_d_pred", "api_name": "水电(含抽蓄)总出力预测（日）","report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_sdjh.frm", "date_offset": 1, "fetch_time": "20:29"},
    {"title_text": "核电出力总加", "api_code": "nuclear_pred", "api_name": "核电出力总加预测", "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_hdclzj.frm","date_offset": 1, "fetch_time": "09:33"},
    {"title_text": "地方燃煤出力总加", "api_code": "coal_pred", "api_name": "地方燃煤出力总加预测", "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_dfrmclzj.frm","date_offset": 1, "fetch_time": "09:37"},
    {"title_text": "除水电外非市场发电总加预测", "api_code": "nonmarket_pred", "api_name": "除水电外非市场发电总加预测", "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_csdwfscfdzjyc.frm&ref_t=design&ref_c=a037aa20-6e39-4fef-9540-6bf316d851af","date_offset": 1, "fetch_time": "09:40"},
    {"title_text": "火电富余电力", "api_code": "thermal_surplus", "api_name": "火电富余电力","report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_sj_hdfydl.frm&ref_t=design&ref_c=bf8cbda9-2c58-4206-9750-5488861e8bfe", "date_offset": 1, "fetch_time": "09:43"},
    # T-1
    {"title_text": "全网发电实时总出力", "api_code": "gen_actual", "api_name": "发电总出力", "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_qwfdsszcl.frm","date_offset": -1, "fetch_time": "09:48"},
    {"title_text": "非市场化机组实际出力", "api_code": "nonmarket_actual", "api_name": "非市场机组总出力","report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_fschjzsjcl.frm", "date_offset": -1, "fetch_time": "09:52"},
    {"title_text": "水电总实时出力", "api_code": "hydro_actual", "api_name": "水电(含抽蓄)总出力","report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_sdzsscl.frm", "date_offset": -1, "fetch_time": "09:55"},
    {"title_text": "实时负荷", "api_code": "load_actual", "api_name": "实际负荷", "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_ssfh.frm","date_offset": -1, "fetch_time": "09:58"},
    {"title_text": "省间联络线输电情况", "api_code": "interline_actual", "api_name": "省间联络线输电情况","report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_ssllxcl.frm", "date_offset": -1, "fetch_time": "10:02"},
    {"title_text": "核电实际出力", "api_code": "nuclear_actual", "api_name": "核电实际出力总加", "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_hdsjclzj.frm&ref_t=design&ref_c=6646dd9c-46a8-4cf4-abfd-a19f681eca30","date_offset": -1, "fetch_time": "10:05"},
    {"title_text": "地方燃煤实际出力", "api_code": "coal_actual", "api_name": "地方燃煤实际出力总加","report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_dfrmsjclzj.frm&ref_t=design&ref_c=6646dd9c-46a8-4cf4-abfd-a19f681eca30", "date_offset": -2, "fetch_time": "10:08"},
]

# 下拉任务组配置
DROP_GROUPS = [
    {
        "group_name": "集中式新能源日",
        "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_nyyczh.frm&ref_t=design&ref_c=0bd8279b-aee8-42ab-90f8-7736dae09702",
        "options": [
            {"option_text": "风电", "api_code": "newenergy_w_wind", "api_name": "集中式新能源日-风电"},
            {"option_text": "光伏", "api_code": "newenergy_w_pv", "api_name": "集中式新能源日-光伏"}
        ],
        "date_offset": 1,
        "fetch_time": "09:07"
    },
    {
        "group_name": "集中式新能源周",
        "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_w_xnyzclyc.frm&ref_t=design&ref_c=7368af68-545a-48dc-ae48-720f3e8e1fff",
        "options": [
            {"option_text": "风电", "api_code": "newenergy_d_wind", "api_name": "集中式新能源周-风电"},
            {"option_text": "光伏", "api_code": "newenergy_d_pv", "api_name": "集中式新能源周-光伏"}
        ],
        "date_offset": 1,
        "fetch_time": "09:10"
    },
    {
        "group_name": "日前各时段出清电力及平均电价",
        "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_rqclzjjpjjg.frm&ref_t=design&ref_c=7b592835-ab65-4634-bb76-908eca75b905",
        "options": [
            {"option_text": "各时段平均电价", "api_code": "da_price", "api_name": "日前各时段平均电价"}
        ],
        "date_offset": -1,
        "fetch_time": "09:13"
    },
    {
        "group_name": "实时各时段出清电力及平均电价",
        "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_ssclzjjpjjg.frm&ref_t=design&ref_c=7368af68-545a-48dc-ae48-720f3e8e1fff",
        "options": [
            {"option_text": "各时段平均电价", "api_code": "rt_price", "api_name": "实时各时段平均电价"}
        ],
        "date_offset": -1,
        "fetch_time": "09:17"
    },
    {
        "group_name": "新能源总出力",
        "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_xnyzsscl.frm",
        "options": [
            {"option_text": "风电总实时出力", "api_code": "newenergy_wind_actual", "api_name": "新能源总出力-风电"},
            {"option_text": "光伏总实时出力", "api_code": "newenergy_pv_actual", "api_name": "新能源总出力-光伏"}
        ],
        "date_offset": -1,
        "fetch_time": "10:11"
    },
    {
        "group_name": "电力电量供需平衡预测",
        "report_url": "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_rqsczfby.frm&ref_t=design&ref_c=0bd8279b-aee8-42ab-90f8-7736dae09702",
        "options": [
            {"option_text": "电力电量供需平衡预测", "api_code": "newenergy_wind_actual", "api_name": "电力电量供需平衡预测(日）"},
        ],
        "date_offset": -1,
        "fetch_time": "10:13"
    }
]

# 类型2实时接口
TYPE2_APIS = [
    {"title_text": "实时出清参考信息", "api_code": "realtime_clearing", "api_name": "实时出清参考信息"}
]
REALTIME_REPORT_URL = "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_sscqckxx.frm&ref_t=design&ref_c=9dc2730b-1383-4e44-b9aa-54e6ca85fd3e"
# ==================== 报表URL ====================
# 类型1/2 公用帆软入口
REPORT_URL = "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_w_dqxtfhyc.frm&ref_t=design&ref_c=7368af68-545a-48dc-ae48-720f3e8e1fff"
# 类型2 实时报表URL (从Referer提取)
REALTIME_REPORT_URL = "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_sscqckxx.frm&ref_t=design&ref_c=9dc2730b-1383-4e44-b9aa-54e6ca85fd3e"

# ==================== POST接口（类型3/4） ====================
TYPE3_QUERY_URL = "https://pmos.ln.sgcc.com.cn/px-settlement-infpubquery-gs/LnInformationDeliveryController/queryIpYxXhFsPq"
TYPE3_MEMBER_URL = "https://pmos.ln.sgcc.com.cn/px-settlement-infpubquery-gs/LnInformationDeliveryController/getMemberName"
TYPE3_CONS_URL = "https://pmos.ln.sgcc.com.cn/px-settlement-infpubquery-gs/LnInformationDeliveryController/queryIpYxXhFsPqChange"

# 类型4：机组状态数据
TYPE4_URL = "https://pmos.ln.sgcc.com.cn/px-settlement-infpubquery/dynamicTable/dynamicTableQueryData"

# 合同分时曲线接口
CONTRACT_CURVE_BASE_URL = "https://pmos.ln.sgcc.com.cn/px-dif-contract-extranet/contractDivisionResolveCurve/getCurveBaseInfoManage"
CONTRACT_CURVE_DETAIL_URL = "https://pmos.ln.sgcc.com.cn/px-dif-contract-extranet/contractDivisionResolveCurve/getDifCurveInfoById"
TYPE4_BODY_TEMPLATE = {
    "publicKey": "010a92a0fc67918b12eb2b56b8274a2c",
    "authKey": {
        "sm4": "043a3c88bdc066fb4c4cf192f65739dc960f76d77862dc27b7cac7208d9254ec815369d4bca5c7922403284e342862f3bb3cd7cce1a15b6f18e2608eee0a57832d0b7584bc355c412defa4cd330871f73e94bcd8e14f6861bf8b3d72631546b9df491755ba1bbfbfd67f3d95fab3a0dc056091f6aa29b59b2ae11975ad62655124b17d35774bc3b7a4b452f74044d102e0dc534e6980644ad742cb3109638f308004f4e151095ccc8bdcbfda82764b0a6fb0f443ec697beed08e32b2b895cd21a4a8fda53aab6678513cb4673a5ecbc9082c443b990f70f0715d26f3e7a9de33032dcd1051702e513a5bcd8ae5db54b7e43cbb18ef4d1b6e54ee0a1b7c976c80b87a321fa1f199b51915a6bcdc47d9520d8d3d3646c18e9b95662e28c6f4ccbe84f19dcc3f6b7f4e5f83ac4040f91600eb7f858847f523fb7c9c409ec9644f2a39183a10318990e725763a7455065e9d41b165dff0e4822ef098a03ad4b49a3a1d8e1bc3f2cde860a7eb7eb83ad9",
        "sm2": "8c29ca94ee35d4d33b0a4ede76f08ca7d9b16c691af2f23583718b11dd278ab09ca2cda66e817a0325b08e07d43ec76c9178acd0552d5043d301cda107e5cb82"
    }
}