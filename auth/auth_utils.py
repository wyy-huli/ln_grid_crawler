# auth/auth_utils.py
import json
import os

import requests
from utils.config import AUTH_FILE

def is_auth_valid():
    try:
        if not os.path.exists(AUTH_FILE):
            return False
        with open(AUTH_FILE, 'r') as f:
            storage = json.load(f)
        cookies = {c['name']: c['value'] for c in storage.get('cookies', [])}

        # 使用一个需要登录才能访问的报表页面
        test_url = (
            "https://pmos.ln.sgcc.com.cn/px-basesystem-reportform/decision/view/form?"
            "viewlet=%25E4%25BF%25A1%25E6%2581%25AF%25E5%258F%2591%25E5%25B8%2583%25E7%258E%25B0%25E8%25B4%25A7%252Flnsnxh_w_dqxtfhyc.frm"
        )
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        }
        resp = requests.get(test_url, cookies=cookies, headers=headers, allow_redirects=False, timeout=10)

        # 登录有效：返回200（直接显示报表）或302但未跳转到登录页
        if resp.status_code == 200:
            return True
        elif resp.status_code == 302:
            location = resp.headers.get('Location', '')
            if 'login' not in location.lower():
                return True
        return False
    except Exception as e:
        print(f"[Auth] 检测异常: {e}")
        return False