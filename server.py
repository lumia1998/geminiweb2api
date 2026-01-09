"""
Gemini OpenAI 兼容 API 服务

启动: python server.py
后台: http://localhost:8000/admin
API:  http://localhost:8000/v1
"""

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from pydantic import BaseModel
from typing import List, Dict, Any, Optional, Union
import uvicorn
import time
import uuid
import json
import os
import re
import httpx
import hashlib
import secrets

# ============ 配置 ============
API_KEY = "sk-gemini"
HOST = "0.0.0.0"
PORT = 8000
CONFIG_FILE = "config_data.json"
# 后台登录账号密码
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "admin123"
# ==============================

app = FastAPI(title="Gemini OpenAI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件路由 (用于示例图片)
from fastapi.responses import FileResponse

# 生成的媒体文件缓存目录
MEDIA_CACHE_DIR = os.path.join(os.path.dirname(__file__), "media_cache")
os.makedirs(MEDIA_CACHE_DIR, exist_ok=True)

@app.get("/static/{filename}")
async def serve_static(filename: str):
    """提供静态文件（示例图片等）"""
    file_path = os.path.join(os.path.dirname(__file__), filename)
    if os.path.exists(file_path):
        return FileResponse(file_path)
    raise HTTPException(status_code=404, detail="文件不存在")

@app.get("/media/{media_id}")
async def serve_media(media_id: str):
    """提供缓存的媒体文件"""
    # 安全检查：只允许字母数字和下划线
    if not media_id.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="无效的媒体 ID")
    
    # 查找匹配的文件
    for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4"]:
        file_path = os.path.join(MEDIA_CACHE_DIR, media_id + ext)
        if os.path.exists(file_path):
            return FileResponse(file_path)
    
    raise HTTPException(status_code=404, detail="媒体文件不存在")

def cleanup_old_media(max_age_hours: int = 1):
    """清理过期的媒体缓存文件"""
    import time
    now = time.time()
    max_age_seconds = max_age_hours * 3600
    
    try:
        for filename in os.listdir(MEDIA_CACHE_DIR):
            file_path = os.path.join(MEDIA_CACHE_DIR, filename)
            if os.path.isfile(file_path):
                file_age = now - os.path.getmtime(file_path)
                if file_age > max_age_seconds:
                    os.remove(file_path)
    except Exception:
        pass

# 存储有效的 session token
_admin_sessions = set()

def generate_session_token():
    """生成随机 session token"""
    return secrets.token_hex(32)

def verify_admin_session(request: Request):
    """验证管理员 session"""
    token = request.cookies.get("admin_session")
    if not token or token not in _admin_sessions:
        return False
    return True

# 默认可用模型列表 (Gemini 3 官网三个模型: 快速/思考/Pro)
DEFAULT_MODELS = ["gemini-3.0-flash", "gemini-3.0-flash-thinking", "gemini-3.0-pro"]

# 默认模型 ID (用于请求头选择模型)
DEFAULT_MODEL_IDS = {
    "flash": "56fdd199312815e2",
    "pro": "e6fa609c3fa255c0", 
    "thinking": "e051ce1aa80aa576",
}

# 配置存储
_config = {
    "SNLM0E": "",
    "SECURE_1PSID": "",
    "SECURE_1PSIDTS": "",
    "SAPISID": "",
    "SID": "",
    "HSID": "",
    "SSID": "",
    "APISID": "",
    "PUSH_ID": "",
    "FULL_COOKIE": "",  # 存储完整cookie字符串
    "MODELS": DEFAULT_MODELS.copy(),  # 可用模型列表
    "MODEL_IDS": DEFAULT_MODEL_IDS.copy(),  # 模型 ID 映射
}

# Cookie 字段映射 (浏览器cookie名 -> 配置字段名)
COOKIE_FIELD_MAP = {
    "__Secure-1PSID": "SECURE_1PSID",
    "__Secure-1PSIDTS": "SECURE_1PSIDTS",
    "SAPISID": "SAPISID",
    "__Secure-1PAPISID": "SAPISID",  # 也映射到 SAPISID
    "SID": "SID",
    "HSID": "HSID",
    "SSID": "SSID",
    "APISID": "APISID",
}


def parse_cookie_string(cookie_str: str) -> dict:
    """解析完整cookie字符串，提取所需字段"""
    result = {}
    if not cookie_str:
        return result
    
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            eq_index = item.index("=")
            key = item[:eq_index].strip()
            value = item[eq_index + 1:].strip()
            if key in COOKIE_FIELD_MAP:
                result[COOKIE_FIELD_MAP[key]] = value
    
    return result


def fetch_tokens_from_page(cookies_str: str) -> dict:
    """从 Gemini 页面自动获取 SNLM0E、PUSH_ID 和可用模型列表"""
    result = {"snlm0e": "", "push_id": "", "models": []}
    try:
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
        for item in cookies_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                session.cookies.set(key.strip(), value.strip(), domain=".google.com")
        
        resp = session.get("https://gemini.google.com")
        if resp.status_code != 200:
            return result
        
        html = resp.text
        
        # 获取 SNLM0E (AT Token)
        snlm0e_patterns = [
            r'"SNlM0e":"([^"]+)"',
            r'SNlM0e["\s:]+["\']([^"\']+)["\']',
            r'"at":"([^"]+)"',
        ]
        for pattern in snlm0e_patterns:
            match = re.search(pattern, html)
            if match:
                result["snlm0e"] = match.group(1)
                break
        
        # 获取 PUSH_ID
        push_id_patterns = [
            r'"push[_-]?id["\s:]+["\'](feeds/[a-z0-9]+)["\']',
            r'push[_-]?id["\s:=]+["\'](feeds/[a-z0-9]+)["\']',
            r'feedName["\s:]+["\'](feeds/[a-z0-9]+)["\']',
            r'clientId["\s:]+["\'](feeds/[a-z0-9]+)["\']',
            r'(feeds/[a-z0-9]{14,})',
        ]
        for pattern in push_id_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches:
                result["push_id"] = matches[0]
                break
        
        # 获取可用模型列表 (从页面中提取 gemini 模型 ID)
        model_patterns = [
            r'"(gemini-[a-z0-9\.\-]+)"',  # 匹配 "gemini-xxx" 格式
            r"'(gemini-[a-z0-9\.\-]+)'",  # 匹配 'gemini-xxx' 格式
        ]
        models_found = set()
        for pattern in model_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            for m in matches:
                # 过滤有效的模型名称
                if any(x in m.lower() for x in ['flash', 'pro', 'ultra', 'nano']):
                    models_found.add(m)
        
        if models_found:
            result["models"] = sorted(list(models_found))
        
        # 获取模型 ID (用于 x-goog-ext-525001261-jspb 请求头)
        # 这些 ID 用于选择不同的模型版本
        model_id_pattern = r'\["([a-f0-9]{16})","gemini[^"]*(?:flash|pro|thinking)[^"]*"\]'
        model_ids = re.findall(model_id_pattern, html, re.IGNORECASE)
        if model_ids:
            result["model_ids"] = list(set(model_ids))
        
        # 备用方案：直接搜索 16 位十六进制 ID（在模型配置附近）
        if not result.get("model_ids"):
            # 搜索类似 "56fdd199312815e2" 的模式
            hex_id_pattern = r'"([a-f0-9]{16})"'
            # 在包含 gemini 或 model 的上下文中查找
            context_pattern = r'.{0,100}(?:gemini|model|flash|pro|thinking).{0,100}'
            contexts = re.findall(context_pattern, html, re.IGNORECASE)
            hex_ids = set()
            for ctx in contexts:
                ids = re.findall(hex_id_pattern, ctx)
                hex_ids.update(ids)
            if hex_ids:
                result["model_ids"] = list(hex_ids)
        
        return result
    except Exception:
        return result

_client = None


# ============ Tools 支持 ============
def build_tools_prompt(tools: List[Dict]) -> str:
    """将 tools 定义转换为提示词"""
    if not tools:
        return ""
    
    tools_schema = json.dumps([{
        "name": t["function"]["name"],
        "description": t["function"].get("description", ""),
        "parameters": t["function"].get("parameters", {})
    } for t in tools if t.get("type") == "function"], ensure_ascii=False, indent=2)
    
    prompt = f"""[系统指令] 你必须作为函数调用代理。不要自己回答问题，必须调用函数。

可用函数:
{tools_schema}

严格规则:
1. 你不能直接回答用户问题
2. 你必须选择一个函数并调用它
3. 只输出以下格式，不要有任何其他文字:
```tool_call
{{"name": "函数名", "arguments": {{"参数": "值"}}}}
```

用户请求: """
    return prompt


def parse_tool_calls(content: str) -> tuple:
    """
    解析响应中的工具调用
    返回: (tool_calls列表, 剩余文本内容)
    """
    tool_calls = []
    
    # 多种匹配模式
    patterns = [
        r'```tool_call\s*\n?(.*?)\n?```',  # ```tool_call ... ```
        r'```json\s*\n?(.*?)\n?```',        # ```json ... ``` (有时模型会用这个)
        r'```\s*\n?(\{[^`]*"name"[^`]*\})\n?```',  # ``` {...} ```
    ]
    
    matches = []
    for pattern in patterns:
        found = re.findall(pattern, content, re.DOTALL)
        matches.extend(found)
    
    # 也尝试直接匹配 JSON 对象（没有代码块包裹的情况）
    if not matches:
        json_pattern = r'\{[^{}]*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^{}]*\}[^{}]*\}'
        matches = re.findall(json_pattern, content, re.DOTALL)
    
    for i, match in enumerate(matches):
        try:
            match = match.strip()
            # 尝试解析 JSON
            call_data = json.loads(match)
            if call_data.get("name"):
                tool_calls.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {
                        "name": call_data.get("name", ""),
                        "arguments": json.dumps(call_data.get("arguments", {}), ensure_ascii=False)
                    }
                })
        except json.JSONDecodeError:
            continue
    
    # 移除工具调用部分
    remaining = content
    for pattern in patterns:
        remaining = re.sub(pattern, '', remaining, flags=re.DOTALL)
    remaining = remaining.strip()
    
    return tool_calls, remaining


def load_config():
    """
    加载配置，优先级:
    1. config_data.json (前端保存的配置)
    2. config.py (本地开发配置，仅作为备用)
    """
    global _config
    loaded_from_json = False
    
    # 优先从 JSON 文件加载
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if saved.get("SNLM0E") and saved.get("SECURE_1PSID"):
                    _config.update(saved)
                    loaded_from_json = True
        except:
            pass
    
    # 如果 JSON 没有有效配置，尝试从 config.py 加载
    if not loaded_from_json:
        try:
            import config
            for key in _config:
                if hasattr(config, key) and getattr(config, key):
                    _config[key] = getattr(config, key)
        except:
            pass


def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(_config, f, indent=2, ensure_ascii=False)


def get_client():
    global _client
    
    if not _config.get("SNLM0E") or not _config.get("SECURE_1PSID"):
        raise HTTPException(status_code=500, detail="请先在后台配置 Token 和 Cookie")
    
    # 如果 client 已存在，直接复用，保持会话上下文
    if _client is not None:
        return _client
    
    cookies = f"__Secure-1PSID={_config['SECURE_1PSID']}"
    if _config.get("SECURE_1PSIDTS"):
        cookies += f"; __Secure-1PSIDTS={_config['SECURE_1PSIDTS']}"
    if _config.get("SAPISID"):
        cookies += f"; SAPISID={_config['SAPISID']}; __Secure-1PAPISID={_config['SAPISID']}"
    if _config.get("SID"):
        cookies += f"; SID={_config['SID']}"
    if _config.get("HSID"):
        cookies += f"; HSID={_config['HSID']}"
    if _config.get("SSID"):
        cookies += f"; SSID={_config['SSID']}"
    if _config.get("APISID"):
        cookies += f"; APISID={_config['APISID']}"
    
    # 构建媒体文件的基础 URL
    media_base_url = f"http://localhost:{PORT}"
    
    from client import GeminiClient
    _client = GeminiClient(
        secure_1psid=_config["SECURE_1PSID"],
        snlm0e=_config["SNLM0E"],
        cookies_str=cookies,
        push_id=_config.get("PUSH_ID") or None,
        model_ids=_config.get("MODEL_IDS") or DEFAULT_MODEL_IDS,
        debug=False,
        media_base_url=media_base_url,
    )
    return _client


def get_login_html():
    return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>登录 - Gemini API</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; 
            display: flex; align-items: center; justify-content: center; padding: 20px; }
        .login-card { background: white; border-radius: 16px; padding: 40px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); width: 100%; max-width: 400px; }
        h1 { color: #333; margin-bottom: 10px; font-size: 28px; text-align: center; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; text-align: center; }
        .form-group { margin-bottom: 20px; }
        label { display: block; font-size: 13px; font-weight: 500; color: #555; margin-bottom: 8px; }
        input { width: 100%; padding: 14px 16px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 15px; transition: border-color 0.2s; }
        input:focus { outline: none; border-color: #667eea; }
        .btn { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 14px 30px;
            border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; width: 100%; margin-top: 10px; transition: transform 0.2s, box-shadow 0.2s; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 5px 20px rgba(102,126,234,0.4); }
        .btn:disabled { opacity: 0.7; cursor: not-allowed; transform: none; }
        .error { background: #f8d7da; color: #721c24; padding: 12px; border-radius: 8px; margin-bottom: 20px; font-size: 14px; display: none; }
        .logo { text-align: center; margin-bottom: 20px; font-size: 48px; }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="logo">🤖</div>
        <h1>Gemini API</h1>
        <p class="subtitle">请登录以访问后台管理</p>
        
        <div id="error" class="error"></div>
        
        <form id="loginForm">
            <div class="form-group">
                <label>用户名</label>
                <input type="text" name="username" id="username" placeholder="请输入用户名" required autofocus>
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" name="password" id="password" placeholder="请输入密码" required>
            </div>
            <button type="submit" class="btn" id="submitBtn">登 录</button>
        </form>
    </div>
    
    <script>
        document.getElementById('loginForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const errorEl = document.getElementById('error');
            const submitBtn = document.getElementById('submitBtn');
            
            errorEl.style.display = 'none';
            submitBtn.disabled = true;
            submitBtn.textContent = '登录中...';
            
            try {
                const resp = await fetch('/admin/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        username: document.getElementById('username').value,
                        password: document.getElementById('password').value
                    })
                });
                const result = await resp.json();
                
                if (result.success) {
                    window.location.href = '/admin';
                } else {
                    errorEl.textContent = result.message || '登录失败';
                    errorEl.style.display = 'block';
                }
            } catch (err) {
                errorEl.textContent = '网络错误: ' + err.message;
                errorEl.style.display = 'block';
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = '登 录';
            }
        });
    </script>
</body>
</html>'''


def get_admin_html():
    return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gemini API 配置</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 20px; }
        .container { max-width: 800px; margin: 0 auto; }
        .card { background: white; border-radius: 16px; padding: 30px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); }
        h1 { color: #333; margin-bottom: 10px; font-size: 28px; }
        .subtitle { color: #666; margin-bottom: 30px; font-size: 14px; }
        .section { margin-bottom: 25px; }
        .section-title { font-size: 16px; font-weight: 600; color: #333; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 2px solid #eee; }
        .required { color: #e74c3c; }
        .optional { color: #95a5a6; font-size: 12px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; font-size: 13px; font-weight: 500; color: #555; margin-bottom: 5px; }
        input, textarea { width: 100%; padding: 12px 15px; border: 2px solid #e0e0e0; border-radius: 8px; font-size: 14px; font-family: monospace; transition: border-color 0.2s; }
        input:focus, textarea:focus { outline: none; border-color: #667eea; }
        textarea { resize: vertical; min-height: 80px; }
        .btn { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; border: none; padding: 14px 30px;
            border-radius: 8px; font-size: 16px; font-weight: 600; cursor: pointer; width: 100%; margin-top: 20px; transition: transform 0.2s, box-shadow 0.2s; }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 5px 20px rgba(102,126,234,0.4); }
        .status { margin-top: 20px; padding: 15px; border-radius: 8px; font-size: 14px; display: none; }
        .status.success { background: #d4edda; color: #155724; display: block; }
        .status.error { background: #f8d7da; color: #721c24; display: block; }
        .info-box { background: #f8f9fa; border-radius: 8px; padding: 15px; margin-bottom: 20px; font-size: 13px; color: #666; }
        .info-box code { background: #e9ecef; padding: 2px 6px; border-radius: 4px; }
        .api-info { background: #e8f4fd; border-left: 4px solid #667eea; padding: 15px; margin-top: 20px; border-radius: 0 8px 8px 0; }
        .api-info h3 { font-size: 14px; margin-bottom: 10px; color: #333; }
        .api-info pre { background: #fff; padding: 10px; border-radius: 4px; font-size: 12px; margin-top: 5px; overflow-x: auto; }
        .parsed-info { background: #f0f9ff; border: 1px solid #bae6fd; border-radius: 8px; padding: 15px; margin-top: 15px; font-size: 12px; display: none; }
        .parsed-info h4 { color: #0369a1; margin-bottom: 10px; }
        .parsed-info .item { margin: 5px 0; color: #555; }
        .parsed-info .item span { color: #059669; font-family: monospace; }
    </style>
</head>
<body>
    <div class="container">
        <div class="card">
            <h1>🤖 Gemini API 配置</h1>
            <p class="subtitle">配置 Google Gemini 的认证信息，保存后即可调用 API <a href="/admin/logout" style="float:right;color:#667eea;text-decoration:none;">退出登录</a></p>
            
            <div class="info-box">
                <strong>获取方法：</strong><br>
                1. 打开 <a href="https://gemini.google.com" target="_blank">gemini.google.com</a> 并登录<br>
                2. F12 → 网络 → 发送内容到聊天 →  点击任意请求 → Copy 请求头内完整cookie
            </div>
            
            <form id="configForm">
                <div class="section">
                    <div class="section-title">🔑 Cookie 配置</div>
                    <div class="form-group">
                        <label>完整 Cookie <span class="required">*</span></label>
                        <textarea name="FULL_COOKIE" id="FULL_COOKIE" rows="6" placeholder="粘贴从浏览器复制的完整 Cookie 字符串，系统会自动解析所需字段和 Token..." required></textarea>
                        <div id="parsedInfo" class="parsed-info">
                            <h4>✅ 已解析的字段：</h4>
                            <div id="parsedFields"></div>
                        </div>
                    </div>
                </div>
                
                <div class="section">
                    <div class="section-title">🎯 模型 ID 配置 <span class="optional">(可选，如果模型切换失效请更新)</span></div>
                    <div class="info-box">
                        <strong>获取方法：</strong>F12 → Network → 在 Gemini 中切换模型发送消息 → 找到请求头 <code>x-goog-ext-525001261-jspb</code> → 复制整个数组值粘贴到下方输入框
                    </div>
                    <div class="form-group">
                        <label>快速解析 <span class="optional">(粘贴请求头数组自动提取 ID)</span></label>
                        <input type="text" id="MODEL_ID_PARSER" placeholder='粘贴如: [1,null,null,null,"56fdd199312815e2",null,null,0,[4],null,null,2]'>
                        <div id="parsedModelId" class="parsed-info" style="margin-top:10px;">
                            <h4>✅ 已提取的模型 ID：</h4>
                            <div id="parsedModelIdValue"></div>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>极速版 (Flash) ID</label>
                        <input type="text" name="MODEL_ID_FLASH" id="MODEL_ID_FLASH" placeholder="56fdd199312815e2">
                    </div>
                    <div class="form-group">
                        <label>Pro 版 ID</label>
                        <input type="text" name="MODEL_ID_PRO" id="MODEL_ID_PRO" placeholder="e6fa609c3fa255c0">
                    </div>
                    <div class="form-group">
                        <label>思考版 (Thinking) ID</label>
                        <input type="text" name="MODEL_ID_THINKING" id="MODEL_ID_THINKING" placeholder="e051ce1aa80aa576">
                    </div>
                </div>
                
                <button type="submit" class="btn">💾 保存配置</button>
            </form>
            
            <div id="status" class="status"></div>
            
            <div class="api-info">
                <h3>📡 API 调用信息</h3>
                <p>Base URL: <strong id="baseUrl"></strong></p>
                <p>API Key: <strong id="apiKey"></strong></p>
                <p>可用模型: <code>gemini-3.0-flash</code> | <code>gemini-3.0-pro</code> | <code>gemini-3.0-flash-thinking</code></p>
                
                <h4 style="margin-top:15px;">💬 文本对话</h4>
<pre>from openai import OpenAI
client = OpenAI(base_url="<span id="codeUrl"></span>", api_key="<span id="codeKey"></span>")

response = client.chat.completions.create(
    model="gemini-3.0-flash",  # 或 gemini-3.0-pro / gemini-3.0-flash-thinking
    messages=[{"role": "user", "content": "你好"}]
)
print(response.choices[0].message.content)</pre>

                <h4 style="margin-top:15px;">🖼️ 图片识别</h4>
<pre>import base64
from openai import OpenAI
client = OpenAI(base_url="<span id="codeUrl2"></span>", api_key="<span id="codeKey2"></span>")

# 读取本地图片
with open("image.png", "rb") as f:
    img_b64 = base64.b64encode(f.read()).decode()

response = client.chat.completions.create(
    model="gemini-3.0-flash",
    messages=[{
        "role": "user",
        "content": [
            {"type": "text", "text": "请描述这张图片"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
        ]
    }]
)
print(response.choices[0].message.content)</pre>

                <h4 style="margin-top:15px;">🌊 流式响应</h4>
<pre>stream = client.chat.completions.create(
    model="gemini-3.0-flash",
    messages=[{"role": "user", "content": "写一首诗"}],
    stream=True
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)</pre>

                <h4 style="margin-top:15px;">📷 示例图片</h4>
                <p style="font-size:12px;color:#666;">以下是 image.png 示例图片，可用于测试图片识别功能（点击放大）：</p>
                <img id="sampleImage" src="/static/image.png" alt="示例图片" style="max-width:300px;border-radius:8px;margin-top:10px;border:1px solid #ddd;cursor:pointer;" onclick="showImageModal()" onerror="this.style.display='none';this.nextElementSibling.style.display='block';">
                <p style="display:none;font-size:12px;color:#999;">（示例图片不可用，请确保 image.png 文件存在）</p>
            </div>
        </div>
    </div>
    
    <!-- 图片放大模态框 -->
    <div id="imageModal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:1000;justify-content:center;align-items:center;cursor:pointer;" onclick="hideImageModal()">
        <img src="/static/image.png" alt="示例图片" style="max-width:90%;max-height:90%;border-radius:8px;box-shadow:0 0 30px rgba(0,0,0,0.5);">
        <span style="position:absolute;top:20px;right:30px;color:white;font-size:30px;cursor:pointer;">&times;</span>
    </div>
    
    <script>
        // 图片放大功能
        function showImageModal() {
            document.getElementById('imageModal').style.display = 'flex';
            document.body.style.overflow = 'hidden';
        }
        function hideImageModal() {
            document.getElementById('imageModal').style.display = 'none';
            document.body.style.overflow = 'auto';
        }
        // ESC 键关闭
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') hideImageModal();
        });
        
        const API_KEY = "''' + API_KEY + '''";
        const PORT = ''' + str(PORT) + ''';
        
        document.getElementById('baseUrl').textContent = 'http://localhost:' + PORT + '/v1';
        document.getElementById('apiKey').textContent = API_KEY;
        document.getElementById('codeUrl').textContent = 'http://localhost:' + PORT + '/v1';
        document.getElementById('codeKey').textContent = API_KEY;
        document.getElementById('codeUrl2').textContent = 'http://localhost:' + PORT + '/v1';
        document.getElementById('codeKey2').textContent = API_KEY;
        
        // 解析模型 ID (从 x-goog-ext-525001261-jspb 数组中提取)
        function parseModelId(input) {
            try {
                // 尝试解析 JSON 数组
                const arr = JSON.parse(input);
                if (Array.isArray(arr) && arr.length > 4 && typeof arr[4] === 'string') {
                    return arr[4];
                }
            } catch (e) {
                // 尝试用正则提取 16 位十六进制字符串
                const match = input.match(/["\']([a-f0-9]{16})["\']/i);
                if (match) {
                    return match[1];
                }
            }
            return null;
        }
        
        // 监听模型 ID 解析输入
        document.getElementById('MODEL_ID_PARSER').addEventListener('input', (e) => {
            const modelId = parseModelId(e.target.value);
            const container = document.getElementById('parsedModelIdValue');
            const infoBox = document.getElementById('parsedModelId');
            
            if (modelId) {
                container.innerHTML = '<div class="item">提取到的 ID: <span style="color:#059669;font-family:monospace;">' + modelId + '</span></div>' +
                    '<div style="margin-top:10px;">' +
                    '<button type="button" onclick="fillModelId(\\'flash\\', \\'' + modelId + '\\')" style="margin-right:5px;padding:5px 10px;cursor:pointer;">填入极速版</button>' +
                    '<button type="button" onclick="fillModelId(\\'pro\\', \\'' + modelId + '\\')" style="margin-right:5px;padding:5px 10px;cursor:pointer;">填入Pro版</button>' +
                    '<button type="button" onclick="fillModelId(\\'thinking\\', \\'' + modelId + '\\')" style="padding:5px 10px;cursor:pointer;">填入思考版</button>' +
                    '</div>';
                infoBox.style.display = 'block';
            } else {
                infoBox.style.display = 'none';
            }
        });
        
        // 填入模型 ID
        function fillModelId(type, id) {
            const fieldMap = {
                'flash': 'MODEL_ID_FLASH',
                'pro': 'MODEL_ID_PRO',
                'thinking': 'MODEL_ID_THINKING'
            };
            document.getElementById(fieldMap[type]).value = id;
        }
        
        // Cookie 字段映射
        const cookieFields = {
            '__Secure-1PSID': 'SECURE_1PSID',
            '__Secure-1PSIDTS': 'SECURE_1PSIDTS',
            'SAPISID': 'SAPISID',
            '__Secure-1PAPISID': 'SECURE_1PAPISID',
            'SID': 'SID',
            'HSID': 'HSID',
            'SSID': 'SSID',
            'APISID': 'APISID'
        };
        
        // 解析 Cookie 字符串
        function parseCookie(cookieStr) {
            const result = {};
            if (!cookieStr) return result;
            
            cookieStr.split(';').forEach(item => {
                const trimmed = item.trim();
                const eqIndex = trimmed.indexOf('=');
                if (eqIndex > 0) {
                    const key = trimmed.substring(0, eqIndex).trim();
                    const value = trimmed.substring(eqIndex + 1).trim();
                    if (cookieFields[key]) {
                        result[cookieFields[key]] = value;
                    }
                }
            });
            return result;
        }
        
        // 显示解析结果
        function showParsedFields(parsed) {
            const container = document.getElementById('parsedFields');
            const infoBox = document.getElementById('parsedInfo');
            
            const fieldNames = {
                'SECURE_1PSID': '__Secure-1PSID',
                'SECURE_1PSIDTS': '__Secure-1PSIDTS',
                'SAPISID': 'SAPISID',
                'SID': 'SID',
                'HSID': 'HSID',
                'SSID': 'SSID',
                'APISID': 'APISID'
            };
            
            let html = '';
            let hasFields = false;
            for (const [key, name] of Object.entries(fieldNames)) {
                if (parsed[key]) {
                    hasFields = true;
                    const shortValue = parsed[key].length > 30 ? parsed[key].substring(0, 30) + '...' : parsed[key];
                    html += '<div class="item">' + name + ': <span>' + shortValue + '</span></div>';
                }
            }
            
            if (hasFields) {
                container.innerHTML = html;
                infoBox.style.display = 'block';
            } else {
                infoBox.style.display = 'none';
            }
        }
        
        // 监听 Cookie 输入
        document.getElementById('FULL_COOKIE').addEventListener('input', (e) => {
            const parsed = parseCookie(e.target.value);
            showParsedFields(parsed);
        });
        
        // 加载配置
        fetch('/admin/config', {credentials: 'same-origin'}).then(r => {
            if (!r.ok) throw new Error('未登录');
            return r.json();
        }).then(config => {
            if (config.FULL_COOKIE) {
                document.getElementById('FULL_COOKIE').value = config.FULL_COOKIE;
                showParsedFields(parseCookie(config.FULL_COOKIE));
            }
            // 加载模型 ID
            if (config.MODEL_IDS) {
                document.getElementById('MODEL_ID_FLASH').value = config.MODEL_IDS.flash || '';
                document.getElementById('MODEL_ID_PRO').value = config.MODEL_IDS.pro || '';
                document.getElementById('MODEL_ID_THINKING').value = config.MODEL_IDS.thinking || '';
            }
        }).catch(err => {
            console.log('加载配置失败:', err);
        });
        
        document.getElementById('configForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const data = Object.fromEntries(formData.entries());
            
            // 构建模型 ID 对象
            data.MODEL_IDS = {
                flash: data.MODEL_ID_FLASH || '',
                pro: data.MODEL_ID_PRO || '',
                thinking: data.MODEL_ID_THINKING || ''
            };
            delete data.MODEL_ID_FLASH;
            delete data.MODEL_ID_PRO;
            delete data.MODEL_ID_THINKING;
            
            const statusEl = document.getElementById('status');
            statusEl.className = 'status';
            statusEl.style.display = 'none';
            statusEl.textContent = '';
            
            // 显示保存中状态
            const submitBtn = e.target.querySelector('button[type="submit"]');
            const originalText = submitBtn.textContent;
            submitBtn.textContent = '⏳ 保存中...';
            submitBtn.disabled = true;
            
            try {
                const resp = await fetch('/admin/save', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    credentials: 'same-origin',
                    body: JSON.stringify(data)
                });
                
                if (resp.status === 401) {
                    window.location.href = '/admin/login';
                    return;
                }
                
                const result = await resp.json();
                
                if (result.success) {
                    statusEl.className = 'status success';
                    statusEl.innerHTML = '✅ ' + result.message + '<br><br>💡 <strong>配置已生效，无需重启服务！</strong>';
                } else {
                    statusEl.className = 'status error';
                    statusEl.textContent = '❌ ' + result.message;
                }
                statusEl.style.display = 'block';
            } catch (err) {
                statusEl.className = 'status error';
                statusEl.textContent = '❌ 保存失败: ' + err.message;
                statusEl.style.display = 'block';
            } finally {
                submitBtn.textContent = originalText;
                submitBtn.disabled = false;
            }
        });
    </script>
</body>
</html>'''


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page():
    return get_login_html()


@app.post("/admin/login")
async def admin_login(request: Request):
    data = await request.json()
    username = data.get("username", "")
    password = data.get("password", "")
    
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        token = generate_session_token()
        _admin_sessions.add(token)
        response = JSONResponse({"success": True, "message": "登录成功"})
        response.set_cookie(key="admin_session", value=token, httponly=True, max_age=86400)
        return response
    else:
        return {"success": False, "message": "用户名或密码错误"}


@app.get("/admin/logout")
async def admin_logout(request: Request):
    token = request.cookies.get("admin_session")
    if token and token in _admin_sessions:
        _admin_sessions.discard(token)
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie("admin_session")
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not verify_admin_session(request):
        return RedirectResponse(url="/admin/login", status_code=302)
    return get_admin_html()


@app.post("/admin/save")
async def admin_save(request: Request):
    if not verify_admin_session(request):
        raise HTTPException(status_code=401, detail="未登录")
    
    global _client
    data = await request.json()
    
    # 处理完整 Cookie 字符串，去除前后空格
    full_cookie = data.get("FULL_COOKIE", "").strip()
    if not full_cookie:
        return {"success": False, "message": "Cookie 是必填项"}
    
    # 解析 Cookie 字符串
    parsed = parse_cookie_string(full_cookie)
    
    if not parsed.get("SECURE_1PSID"):
        return {"success": False, "message": "Cookie 中未找到 __Secure-1PSID 字段，请确保复制了完整的 Cookie"}
    
    # 从页面自动获取 SNLM0E 和 PUSH_ID
    tokens = fetch_tokens_from_page(full_cookie)
    
    if not tokens.get("snlm0e"):
        return {"success": False, "message": "无法自动获取 AT Token，请检查 Cookie 是否有效或已过期"}
    
    # 更新配置
    _config["FULL_COOKIE"] = full_cookie
    _config["SNLM0E"] = tokens["snlm0e"]
    _config["PUSH_ID"] = tokens.get("push_id", "")
    
    # 从解析结果更新各字段
    for field in ["SECURE_1PSID", "SECURE_1PSIDTS", "SAPISID", "SID", "HSID", "SSID", "APISID"]:
        _config[field] = parsed.get(field, "")
    
    # 使用自动获取的模型列表，如果获取失败则使用默认值
    if tokens.get("models"):
        _config["MODELS"] = tokens["models"]
    else:
        _config["MODELS"] = DEFAULT_MODELS.copy()
    
    # 处理模型 ID 配置
    model_ids = data.get("MODEL_IDS", {})
    if model_ids:
        # 只更新非空的值
        if model_ids.get("flash"):
            _config["MODEL_IDS"]["flash"] = model_ids["flash"]
        if model_ids.get("pro"):
            _config["MODEL_IDS"]["pro"] = model_ids["pro"]
        if model_ids.get("thinking"):
            _config["MODEL_IDS"]["thinking"] = model_ids["thinking"]
    
    save_config()
    _client = None
    
    # 构建结果信息
    parsed_fields = [k for k in ["SECURE_1PSID", "SECURE_1PSIDTS", "SAPISID", "SID", "HSID", "SSID", "APISID"] if parsed.get(k)]
    push_id_msg = f"，PUSH_ID ✓" if tokens.get("push_id") else "，PUSH_ID ✗ (图片功能不可用)"
    models_msg = f"，{len(_config['MODELS'])} 个模型" if _config.get("MODELS") else ""
    
    try:
        get_client()
        return {
            "success": True, 
            "message": f"配置已保存并验证成功！AT Token ✓{push_id_msg}{models_msg}",
            "need_restart": False
        }
    except Exception as e:
        return {
            "success": True, 
            "message": f"配置已保存，但连接测试失败: {str(e)[:50]}",
            "need_restart": False
        }


@app.get("/admin/config")
async def admin_get_config(request: Request):
    if not verify_admin_session(request):
        raise HTTPException(status_code=401, detail="未登录")
    return _config


# ============ API 路由 ============

class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]
    name: Optional[str] = None
    
    class Config:
        extra = "ignore"


class FunctionDefinition(BaseModel):
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None

class ToolDefinition(BaseModel):
    type: str = "function"
    function: FunctionDefinition

class ChatCompletionRequest(BaseModel):
    model: str = "gemini"
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    # Tools 支持
    tools: Optional[List[ToolDefinition]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    # OpenAI SDK 可能发送的额外字段
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stop: Optional[Union[str, List[str]]] = None
    n: Optional[int] = None
    user: Optional[str] = None
    
    class Config:
        extra = "ignore"  # 忽略未定义的额外字段


class ChatCompletionChoice(BaseModel):
    index: int
    message: Dict[str, Any]
    finish_reason: str


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: Usage


def verify_api_key(authorization: str = Header(None)):
    if not API_KEY:
        return True
    if not authorization or not authorization.startswith("Bearer ") or authorization[7:] != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


@app.get("/")
async def root():
    return RedirectResponse(url="/admin")


@app.get("/v1/models")
async def list_models(authorization: str = Header(None)):
    verify_api_key(authorization)
    models = _config.get("MODELS", DEFAULT_MODELS)
    created = int(time.time())
    return {
        "object": "list",
        "data": [{"id": m, "object": "model", "created": created, "owned_by": "google"} for m in models]
    }


def log_api_call(request_data: dict, response_data: dict, error: str = None):
    """记录 API 调用日志到文件"""
    import datetime
    log_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "request": request_data,
        "response": response_data,
        "error": error
    }
    try:
        with open("api_logs.json", "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False, indent=2) + "\n---\n")
    except Exception as e:
        print(f"[LOG ERROR] 写入日志失败: {e}")


# 用于追踪会话：保存上次请求的所有用户消息内容
_last_user_messages_hash = ""


def get_user_messages_hash(messages: list) -> str:
    """计算所有用户消息的 hash，用于判断是否是同一会话"""
    content_str = ""
    for m in messages:
        role = m.role if hasattr(m, 'role') else m.get('role', '')
        if role != "user":
            continue
        content = m.content if hasattr(m, 'content') else m.get('content', '')
        if isinstance(content, list):
            # 对于包含图片的消息，只取文本部分
            text_parts = [item.get('text', '') for item in content if item.get('type') == 'text']
            content_str += f"{' '.join(text_parts)}|"
        else:
            content_str += f"{content}|"
    return hashlib.md5(content_str.encode()).hexdigest()


def is_continuation(current_messages: list, last_hash: str) -> bool:
    """
    判断当前请求是否是上一次对话的延续
    
    逻辑：如果当前消息去掉最后一条用户消息后的 hash 等于上次的 hash，
    说明是同一对话的延续
    """
    if not last_hash:
        return False
    
    # 找到所有用户消息
    user_indices = [i for i, m in enumerate(current_messages) 
                    if (m.role if hasattr(m, 'role') else m.get('role', '')) == "user"]
    
    if len(user_indices) <= 1:
        # 只有一条用户消息，无法判断是否延续，视为新对话
        return False
    
    # 去掉最后一条用户消息，计算剩余消息的 hash
    last_user_idx = user_indices[-1]
    prev_messages = current_messages[:last_user_idx]
    prev_hash = get_user_messages_hash(prev_messages)
    
    return prev_hash == last_hash


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, authorization: str = Header(None)):
    global _last_user_messages_hash
    verify_api_key(authorization)
    
    # 记录请求入参 (图片内容截断显示)
    request_log = {
        "model": request.model,
        "stream": request.stream,
        "messages": [],
        "tools": [t.model_dump() for t in request.tools] if request.tools else None
    }
    for m in request.messages:
        msg_log = {"role": m.role}
        if isinstance(m.content, list):
            content_log = []
            for item in m.content:
                if item.get("type") == "image_url":
                    img_url = item.get("image_url", {})
                    if isinstance(img_url, dict):
                        url = img_url.get("url", "")
                    else:
                        url = str(img_url)
                    content_log.append({"type": "image_url", "url_preview": url[:100] + "..." if len(url) > 100 else url})
                else:
                    content_log.append(item)
            msg_log["content"] = content_log
        else:
            msg_log["content"] = m.content
        request_log["messages"].append(msg_log)
    
    try:
        client = get_client()
        
        if not is_continuation(request.messages, _last_user_messages_hash):
            client.reset()
        
        # 处理消息
        messages = []
        for m in request.messages:
            content = m.content
            if isinstance(content, list):
                messages.append({"role": m.role, "content": content})
            else:
                messages.append({"role": m.role, "content": content})
        
        # 如果有 tools，把工具提示词直接加到用户消息前面
        if request.tools and len(messages) > 0:
            tools_prompt = build_tools_prompt([t.model_dump() for t in request.tools])
            for i in range(len(messages) - 1, -1, -1):
                if messages[i]["role"] == "user":
                    original = messages[i]["content"]
                    if isinstance(original, str):
                        messages[i]["content"] = tools_prompt + original
                    break
        
        response = client.chat(messages=messages, model=request.model)
        _last_user_messages_hash = get_user_messages_hash(request.messages)
        
        reply_content = response.choices[0].message.content
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
        created_time = int(time.time())
        
        # 解析工具调用
        tool_calls = []
        final_content = reply_content
        if request.tools:
            tool_calls, final_content = parse_tool_calls(reply_content)
        
        # 处理流式响应
        if request.stream:
            async def generate_stream():
                chunk_data = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": request.model,
                    "choices": [{
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"
                
                if tool_calls:
                    # 流式返回工具调用
                    for tc in tool_calls:
                        chunk_data = {
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": created_time,
                            "model": request.model,
                            "choices": [{
                                "index": 0,
                                "delta": {"tool_calls": [tc]},
                                "finish_reason": None
                            }]
                        }
                        yield f"data: {json.dumps(chunk_data)}\n\n"
                else:
                    chunk_data = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": request.model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": final_content},
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"
                
                chunk_data = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created_time,
                    "model": request.model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": "tool_calls" if tool_calls else "stop"
                    }]
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"
                yield "data: [DONE]\n\n"
            
            return StreamingResponse(
                generate_stream(), 
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                }
            )
        
        # 构建响应消息
        response_message = {"role": "assistant"}
        if tool_calls:
            response_message["content"] = final_content if final_content else None
            response_message["tool_calls"] = tool_calls
            finish_reason = "tool_calls"
        else:
            response_message["content"] = final_content
            finish_reason = "stop"
        
        response_data = ChatCompletionResponse(
            id=completion_id,
            created=created_time,
            model=request.model,
            choices=[ChatCompletionChoice(index=0, message=response_message, finish_reason=finish_reason)],
            usage=Usage(prompt_tokens=response.usage.prompt_tokens, completion_tokens=response.usage.completion_tokens, total_tokens=response.usage.total_tokens)
        )
        
        log_api_call(request_log, response_data.model_dump())
        
        return JSONResponse(
            content=response_data.model_dump(),
            headers={
                "Cache-Control": "no-cache",
                "X-Request-Id": completion_id,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        error_msg = str(e)
        print(f"[ERROR] Chat error: {error_msg}")
        traceback.print_exc()
        log_api_call(request_log, None, error=error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


@app.post("/v1/chat/completions/reset")
async def reset_context(authorization: str = Header(None)):
    verify_api_key(authorization)
    global _client
    if _client:
        _client.reset()
    return {"status": "ok"}


load_config()

if __name__ == "__main__":
    print(f"""
╔══════════════════════════════════════════════════════════╗
║           Gemini OpenAI Compatible API Server            ║
╠══════════════════════════════════════════════════════════╣
║  后台配置: http://localhost:{PORT}/admin                   ║
║  API 地址: http://localhost:{PORT}/v1                      ║
║  API Key:  {API_KEY}                                     ║
╚══════════════════════════════════════════════════════════╝
""")
    uvicorn.run(app, host=HOST, port=PORT)
