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

### Cookie 管理

1. 登录 [gemini.google.com](https://gemini.google.com)
2. F12 打开开发者工具 → Application → Cookies
3. 右键任意 cookie → Copy all as Header String
4. 在管理后台「Cookie 管理」中粘贴添加

系统会自动解析 Cookie 并获取所需 Token（SNLM0E、PUSH_ID 等）

### 设置配置

| 配置项 | 说明 |
|--------|------|
| **服务网址** | 公网访问地址，用于图片 URL。如 `http://10.1.2.30:42180` |
| **API Key** | 自定义密钥，调用 API 时需携带。留空则不验证 |
| **Proxy URL** | HTTP 代理地址（可选） |

## 📡 API 使用

### 在其他框架中使用

配置 OpenAI 兼容接口：

| 配置项 | 值 |
|--------|-----|
| API Base | `http://你的服务地址/v1` |
| API Key | 你设置的 API Key |
| Model | `gemini-3.0-flash` / `gemini-3.0-pro` / `gemini-3.0-flash-thinking` |

### cURL 示例

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
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
