"""GeminiWeb2API - OpenAI兼容的Gemini API服务"""

import os
import sys
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse

from app.core.logger import logger
from app.core.storage import storage_manager
from app.core.config import setting
from app.services.gemini.cookie import cookie_manager
from app.api.admin.manage import router as admin_router
from app.api.v1.chat import router as chat_router
from app.api.v1.models import router as models_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    logger.info("=" * 50)
    logger.info("GeminiWeb2API 启动中...")
    
    # 初始化存储
    await storage_manager.init()
    storage = storage_manager.get_storage()
    
    # 设置存储到各服务
    setting.set_storage(storage)
    cookie_manager.set_storage(storage)
    
    # 加载Cookie数据
    await cookie_manager._load_data()
    
    # 启动批量保存任务
    await cookie_manager.start_batch_save()
    
    logger.info(f"服务已启动: http://{os.getenv('HOST', '0.0.0.0')}:{os.getenv('PORT', '8000')}")
    logger.info("=" * 50)
    
    yield
    
    # 关闭服务
    logger.info("GeminiWeb2API 关闭中...")
    await cookie_manager.shutdown()
    await storage_manager.close()
    logger.info("服务已关闭")


# 创建应用
app = FastAPI(
    title="GeminiWeb2API",
    description="OpenAI Compatible API for Gemini Web",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件目录
STATIC_DIR = Path(__file__).parent / "static"
MEDIA_CACHE_DIR = Path(__file__).parent / "media_cache"
STATIC_DIR.mkdir(exist_ok=True)
MEDIA_CACHE_DIR.mkdir(exist_ok=True)

# 挂载静态文件
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# === 路由注册 ===

# Admin路由
app.include_router(admin_router, tags=["admin"])

# V1 API路由
app.include_router(chat_router, prefix="/v1", tags=["chat"])
app.include_router(models_router, prefix="/v1", tags=["models"])


# === 页面路由 ===

@app.get("/")
async def root():
    """首页重定向到管理后台"""
    return RedirectResponse(url="/login")


@app.get("/admin")
async def admin_redirect():
    """兼容旧的admin路径"""
    return RedirectResponse(url="/manage")


# === 媒体文件路由 ===

@app.get("/media/{media_id}")
async def get_media(media_id: str):
    """获取缓存的媒体文件"""
    # 尝试不同扩展名
    for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm"]:
        path = MEDIA_CACHE_DIR / f"{media_id}{ext}"
        if path.exists():
            return FileResponse(path)
    
    # 不带扩展名查找
    for f in MEDIA_CACHE_DIR.iterdir():
        if f.stem == media_id:
            return FileResponse(f)
    
    return {"error": "Media not found"}


# === 健康检查 ===

@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}


# === 入口 ===

def main():
    """主函数"""
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
