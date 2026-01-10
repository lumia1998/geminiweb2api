"""诊断图片上传问题的脚本
这个脚本会模拟 client.py 的上传逻辑，并打印详细的调试信息
"""
import httpx
import json
import base64
import random
import re
from pathlib import Path

# === 配置 ===
# 请替换为你的 cookies.json 路径
COOKIE_FILE = "data/cookies.json"
# 请替换为你的图片路径
IMAGE_PATH = "奶龙.jpg"
# 如果有代理，请填写
PROXY_URL = "" 

def diagnose_upload():
    print("=== 开始诊断图片上传 ===")
    
    # 1. 读取配置
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # 获取第一个账号
            key = list(data["cookies"].keys())[0]
            cookie_data = data["cookies"][key]
            parsed = cookie_data["parsed"]
            push_id = cookie_data["push_id"]
            snlm0e = cookie_data["snlm0e"]
            
            secure_1psid = parsed.get("__Secure-1PSID")
            secure_1psidts = parsed.get("__Secure-1PSIDTS")
            
            print(f"✅ 读取配置成功")
            print(f"   Push ID: {push_id}")
            print(f"   1PSID: {secure_1psid[:10]}...")
            
    except Exception as e:
        print(f"❌ 读取配置失败: {e}")
        return

    # 2. 读取图片
    try:
        if not Path(IMAGE_PATH).exists():
             print(f"❌ 图片不存在: {IMAGE_PATH}")
             return
        with open(IMAGE_PATH, "rb") as f:
            image_data = f.read()
        print(f"✅ 读取图片成功，大小: {len(image_data)} bytes")
    except Exception as e:
        print(f"❌ 读取图片失败: {e}")
        return

    # 3. 初始化 Session
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Origin": "https://gemini.google.com",
        "Referer": "https://gemini.google.com/",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    
    proxies = None
    if PROXY_URL:
        proxies = PROXY_URL
        print(f"   使用代理: {PROXY_URL}")

    print(f"   准备初始化 httpx.Client, proxy={proxies}")
    try:
        session = httpx.Client(
            headers=headers,
            proxy=proxies,
            timeout=60.0,
            follow_redirects=True
        )
    except Exception as e:
        print(f"❌ httpx 初始化失败: {e}")
        # 尝试不带 proxy
        session = httpx.Client(
            headers=headers,
            timeout=60.0,
            follow_redirects=True
        )
        print("⚠️  降级尝试不带代理初始化成功")
    
    # 设置 Cookie
    session.cookies.set("__Secure-1PSID", secure_1psid, domain=".google.com")
    if secure_1psidts:
        session.cookies.set("__Secure-1PSIDTS", secure_1psidts, domain=".google.com")

    # 4. 开始上传流程
    upload_url = "https://push.clients6.google.com/upload/"
    filename = f"image_{random.randint(100000, 999999)}.png"
    
    browser_headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
        "origin": "https://gemini.google.com",
        "referer": "https://gemini.google.com/",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "x-browser-channel": "stable",
        "x-browser-copyright": "Copyright 2025 Google LLC. All Rights reserved.",
        "x-browser-validation": "Aj9fzfu+SaGLBY9Oqr3S7RokOtM=",
        "x-browser-year": "2025",
        "x-client-data": "CIa2yQEIpbbJAQipncoBCNvaygEIk6HLAQiFoM0BCJaMzwEIkZHPAQiSpM8BGOyFzwEYsobPAQ==",
    }

    # Step A: Init
    print("\n[Step A] 初始化上传...")
    init_headers = {
        **browser_headers,
        "content-type": "application/x-www-form-urlencoded;charset=utf-8",
        "push-id": push_id,
        "x-goog-upload-command": "start",
        "x-goog-upload-header-content-length": str(len(image_data)),
        "x-goog-upload-protocol": "resumable",
        "x-tenant-id": "bard-storage",
    }
    
    try:
        init_resp = session.post(upload_url, data={"File name": filename}, headers=init_headers)
        print(f"   状态码: {init_resp.status_code}")
        print(f"   响应头: {dict(init_resp.headers)}")
        print(f"   响应体: {init_resp.text[:200]}")
        
        if init_resp.status_code != 200:
            print("❌ 初始化失败")
            return
            
        upload_id = init_resp.headers.get("x-guploader-uploadid")
        if not upload_id:
            print("❌ 未获取到 upload_id")
            return
        print(f"✅ 获取到 Upload ID: {upload_id[:20]}...")
        
    except Exception as e:
        print(f"❌ 初始化请求异常: {e}")
        return

    # Step B: Upload Data
    print("\n[Step B] 上传图片数据...")
    final_upload_url = f"{upload_url}?upload_id={upload_id}&upload_protocol=resumable"
    
    upload_headers = {
        **browser_headers,
        "content-type": "application/x-www-form-urlencoded;charset=utf-8",
        "push-id": push_id,
        "x-goog-upload-command": "upload, finalize",
        "x-goog-upload-offset": "0",
        "x-tenant-id": "bard-storage",
        "x-client-pctx": "CgcSBWjK7pYx",
    }
    
    try:
        upload_resp = session.post(
            final_upload_url,
            headers=upload_headers,
            content=image_data
        )
        print(f"   状态码: {upload_resp.status_code}")
        print(f"   响应头: {dict(upload_resp.headers)}")
        print(f"   响应体: {upload_resp.text}")
        
        if upload_resp.status_code == 200:
            print("✅ 上传成功！")
        else:
            print(f"❌ 上传失败 (Status: {upload_resp.status_code})")
            
    except Exception as e:
        print(f"❌ 上传请求异常: {e}")

if __name__ == "__main__":
    diagnose_upload()
