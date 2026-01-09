# GeminiWeb2API

将 Google Gemini 网页版转换为 OpenAI 兼容 API，支持多账号管理和 Web 管理界面。

> 基于 [grok2api](https://github.com/chenyme/grok2api) 的架构风格重构

## ✨ 特性

- 🔄 **OpenAI 兼容** - 支持 `/v1/chat/completions` 和 `/v1/models` 接口
- 🍪 **多 Cookie 管理** - 支持添加多个 Gemini 账号，自动负载均衡
- 🔑 **自定义 API Key** - 在 Web 界面配置访问密钥
- 🌐 **自定义服务地址** - 解决 Docker 端口映射后图片 URL 问题
- 🖼️ **图文对话** - 支持图片识别和图片生成
- 📺 **视频生成** - 支持异步视频生成（需在官网查看结果）
- 🎨 **现代 UI** - TailwindCSS 风格的管理后台

## 📦 部署

### Docker Compose（推荐）

```yaml
version: '3.8'
services:
  geminiweb2api:
    image: lumia1998/geminiweb2api:latest
    container_name: geminiweb2api
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./media_cache:/app/media_cache
    environment:
      - HOST=0.0.0.0
      - PORT=8000
    restart: unless-stopped
```

```bash
docker-compose up -d
```

### 本地运行

```bash
git clone https://github.com/xxx/geminiweb2api.git
cd geminiweb2api
pip install -r requirements.txt
python main.py
```

## 🔧 配置

访问 `http://localhost:8000/login`，默认账号密码：`admin` / `admin`

### 1. 配置 Cookie

1. 打开后台管理页面 `http://localhost:8000/admin`
2. 使用默认账号登录：
   - 用户名: `admin`
   - 密码: `admin`
3. 获取 Cookie：
   - 登录 [Gemini 网页版](https://gemini.google.com)
   - 按 `F12` 打开开发者工具
   - 切换到 `Application` 标签页
   - 左侧选择 `Cookies` → `https://gemini.google.com`
   - 右键任意 cookie → **Copy all as Header String**
4. 粘贴到后台配置页面的「Cookie 字符串」输入框，点击保存

> 💡 系统会自动解析 Cookie 并获取所需 Token（SNLM0E、PUSH_ID 等），无需手动填写

### 2. 配置模型 ID（可选）

如果发现模型切换不生效（例如选择 Pro 版但实际使用的是极速版），需要手动更新模型 ID：

**抓包获取模型 ID：**

1. 打开 [Gemini 网页版](https://gemini.google.com)，按 `F12` 打开开发者工具
2. 切换到 `Network` 标签页
3. 在 Gemini 网页中切换到目标模型（如 Pro 版），发送一条消息
4. 在 Network 中找到 `StreamGenerate` 请求
5. 查看请求头 `x-goog-ext-525001261-jspb`，格式如下：

   ```json
   [1,null,null,null,"e6fa609c3fa255c0",null,null,0,[4],null,null,2]
   ```

6. 第 5 个元素（`e6fa609c3fa255c0`）即为该模型的 ID

**配置模型 ID：**

在后台管理页面的「模型 ID 配置」区域，将抓取到的 ID 填入对应输入框：

| 模型 | 默认 ID | 说明 |
|------|---------|------|
| 极速版 (Flash) | `56fdd199312815e2` | 响应最快 |
| Pro 版 | `e6fa609c3fa255c0` | 质量更高 |
| 思考版 (Thinking) | `e051ce1aa80aa576` | 深度推理 |

> ⚠️ Google 可能会更新模型 ID，如果模型切换失效请重新抓包获取最新 ID

### 3. 调用 API

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="sk-gemini"
)

response = client.chat.completions.create(
    model="gemini-3.0-flash",
    messages=[{"role": "user", "content": "你好"}]
)
print(response.choices[0].message.content)
```

## 📡 API 信息

| 项目 | 值 |
|------|-----|
| Base URL | `http://localhost:8000/v1` |
| API Key | `sk-gemini` |
| 后台地址 | `http://localhost:8000/admin` |
| 登录账号 | `admin` / `admin` |

### 可用模型

- `gemini-3.0-flash` - 快速响应（极速版）
- `gemini-3.0-flash-thinking` - 思考模式
- `gemini-3.0-pro` - 专业版

### 模型切换

API 支持通过 `model` 参数切换不同版本的 Gemini：

```python
# 使用极速版
response = client.chat.completions.create(
    model="gemini-3.0-flash",
    messages=[{"role": "user", "content": "你好"}]
)

# 使用 Pro 版
response = client.chat.completions.create(
    model="gemini-3.0-pro",
    messages=[{"role": "user", "content": "你好"}]
)

# 使用思考版
response = client.chat.completions.create(
    model="gemini-3.0-flash-thinking",
    messages=[{"role": "user", "content": "你好"}]
)
```URL 示例

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
    "model": "gemini-3.0-flash",
    "messages": [{"role": "user", "content": "你好"}]
  }'
```

## 📁 项目结构

```
geminiweb2api/
├── app/
│   ├── api/           # API 路由
│   │   ├── admin/     # 管理后台 API
│   │   └── v1/        # OpenAI 兼容 API
│   ├── core/          # 核心模块
│   │   ├── auth.py    # 认证
│   │   ├── config.py  # 配置管理
│   │   └── storage.py # 存储
│   ├── services/      # 业务服务
│   │   └── gemini/    # Gemini 相关
│   └── template/      # HTML 模板
├── data/              # 数据存储
├── client.py          # Gemini 客户端
├── main.py            # 入口
└── requirements.txt
```

## 🎯 可用模型

| 模型 ID | 说明 |
|---------|------|
| `gemini-3.0-flash` | 快速版（默认） |
| `gemini-3.0-pro` | Pro 版 |
| `gemini-3.0-flash-thinking` | 思考版 |

## 📝 License

MIT
