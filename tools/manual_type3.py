# 临时手动执行类型3
import sys, json, datetime, requests
from auth.auth_utils import is_auth_valid
from database.db_manager import save_type3_query
from utils.config import TYPE3_MEMBER_URL, TYPE3_CONS_URL, TYPE3_QUERY_URL, AUTH_FILE

def fetch_cons_by_member(mid, date, cookies):
    resp = requests.post(TYPE3_CONS_URL, json={
        "data": {"consNo": "", "mid": [mid], "infoDate": date},
        "pageInfo": {"pageNum": 1, "pageSize": 1000, "total": 0}
    }, cookies=cookies)
    if resp.status_code == 200 and resp.json().get('status') == 0:
        return resp.json()['data']['list']
    return []

if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("用法: python manual_type3.py <mid> <cons_no> <date>")
        sys.exit(1)
    mid, cons_no, date = sys.argv[1], sys.argv[2], sys.argv[3]
    with open(AUTH_FILE) as f:
        cookies = {c['name']: c['value'] for c in json.load(f)['cookies']}
    resp = requests.post(TYPE3_QUERY_URL, json={
        "data": {"consNo": [cons_no], "mid": [mid], "infoDate": date},
        "pageInfo": {"total": 96, "list": [], "pageNum": 1, "pageSize": 96}
    }, cookies=cookies)
    if resp.status_code == 200 and resp.json()['status'] == 0:
        save_type3_query(date, cons_no, mid, json.dumps(resp.json()))
        print("保存成功")
    else:
        print("查询失败:", resp.text)