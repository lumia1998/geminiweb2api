"""
获取 Gemini 的 push-id

push-id 是图片上传所需的必要参数，格式为 feeds/xxxxx
需要从 Gemini 页面或 API 获取
"""

import httpx
import re
import httpx
import re
import json
from pathlib import Path

# 读取配置
try:
    cookie_file = Path(__file__).parent / "data" / "cookies.json"
    if cookie_file.exists():
        with open(cookie_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 假设只有一个账号，获取第一个
            first_key = list(data["cookies"].keys())[0]
            cookie_data = data["cookies"][first_key]["parsed"]
            
            SECURE_1PSID = cookie_data.get("__Secure-1PSID", "")
            SECURE_1PSIDTS = cookie_data.get("__Secure-1PSIDTS", "")
            SECURE_1PSIDCC = cookie_data.get("__Secure-1PSIDCC", "")
            COOKIES_STR = ""  # 优先使用分离的 Token
    else:
        print("❌ 未找到配置文件 data/cookies.json")
        SECURE_1PSID = ""
        SECURE_1PSIDTS = ""
        SECURE_1PSIDCC = ""
        COOKIES_STR = ""
except Exception as e:
    print(f"❌ 读取配置失败: {e}")
    SECURE_1PSID = ""
    SECURE_1PSIDTS = ""
    SECURE_1PSIDCC = ""
    COOKIES_STR = ""


def get_push_id_from_page():
    """从 Gemini 页面获取 push-id"""
    print("正在获取 push-id...")
    
    session = httpx.Client(
        timeout=30.0,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    
    # 设置 cookies
    if COOKIES_STR:
        for item in COOKIES_STR.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                session.cookies.set(key.strip(), value.strip(), domain=".google.com")
    else:
        session.cookies.set("__Secure-1PSID", SECURE_1PSID, domain=".google.com")
        if SECURE_1PSIDTS:
            session.cookies.set("__Secure-1PSIDTS", SECURE_1PSIDTS, domain=".google.com")
        if SECURE_1PSIDCC:
            session.cookies.set("__Secure-1PSIDCC", SECURE_1PSIDCC, domain=".google.com")
    
    try:
        # 访问 Gemini 主页
        resp = session.get("https://gemini.google.com")
        
        if resp.status_code != 200:
            print(f"❌ 访问失败: {resp.status_code}")
            return None
        
        html = resp.text
        
        # 尝试多种模式匹配 push-id
        patterns = [
            r'"push[_-]?id["\s:]+["\'](feeds/[a-z0-9]+)["\']',  # "push_id": "feeds/xxx"
            r'push[_-]?id["\s:=]+["\'](feeds/[a-z0-9]+)["\']',  # push_id="feeds/xxx"
            r'feedName["\s:]+["\'](feeds/[a-z0-9]+)["\']',      # "feedName": "feeds/xxx"
            r'clientId["\s:]+["\'](feeds/[a-z0-9]+)["\']',      # "clientId": "feeds/xxx"
            r'(feeds/[a-z0-9]{14,})',                            # 直接匹配 feeds/xxx 格式
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                push_id = matches[0]
                print(f"✅ 找到 push-id: {push_id}")
                return push_id
        
        # 如果没找到，保存页面源码供分析
        with open("gemini_page_debug.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("❌ 未找到 push-id")
        print("   页面源码已保存到 gemini_page_debug.html")
        print("   请手动搜索 'feeds/' 或 'push' 关键字")
        
        return None
        
    except Exception as e:
        print(f"❌ 错误: {e}")
        return None


def get_push_id_from_api():
    """尝试从 API 获取 push-id"""
    print("\n尝试从 API 获取 push-id...")
    
    session = httpx.Client(
        timeout=30.0,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Content-Type": "application/json",
        }
    )
    
    # 设置 cookies
    if COOKIES_STR:
        for item in COOKIES_STR.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                session.cookies.set(key.strip(), value.strip(), domain=".google.com")
    else:
        session.cookies.set("__Secure-1PSID", SECURE_1PSID, domain=".google.com")
    
    # 可能的 API 端点
    endpoints = [
        "https://gemini.google.com/_/BardChatUi/data/batchexecute",
        "https://push.clients6.google.com/v1/feeds",
    ]
    
    for endpoint in endpoints:
        try:
            resp = session.get(endpoint)
            print(f"  {endpoint}: {resp.status_code}")
            if resp.status_code == 200:
                # 尝试从响应中提取 push-id
                text = resp.text
                match = re.search(r'feeds/[a-z0-9]{14,}', text)
                if match:
                    push_id = match.group(0)
                    print(f"  ✅ 找到: {push_id}")
                    return push_id
        except Exception as e:
            print(f"  ❌ {endpoint}: {e}")
    
    return None


if __name__ == "__main__":
    print("=" * 60)
    print("获取 Gemini push-id")
    print("=" * 60)
    
    # 方法1: 从页面获取
    push_id = get_push_id_from_page()
    
    # 方法2: 从 API 获取
    if not push_id:
        push_id = get_push_id_from_api()
    
    if push_id:
        print("\n" + "=" * 60)
        print(f"✅ 成功获取 push-id: {push_id}")
        print("=" * 60)
        print("\n请将此值添加到 config.py:")
        print(f'PUSH_ID = "{push_id}"')
    else:
        print("\n" + "=" * 60)
        print("❌ 未能自动获取 push-id")
        print("=" * 60)
        print("\n手动获取方法:")
        print("1. 打开 https://gemini.google.com 并登录")
        print("2. F12 打开开发者工具 -> Network 标签")
        print("3. 上传一张图片")
        print("4. 查找 upload 请求")
        print("5. 在请求头中找到 push-id 或 x-goog-upload-header-content-length")
        print("6. 复制 feeds/xxxxx 格式的值")
