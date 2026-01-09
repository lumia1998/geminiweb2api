"""管理接口 - Cookie管理和系统配置"""

import os
import secrets
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Header
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from app.core.config import setting
from app.core.logger import logger
from app.services.gemini.cookie import cookie_manager


# 常量
TEMPLATE_DIR = Path(__file__).parents[2] / "template"
TEMP_DIR = Path(__file__).parents[3] / "media_cache"
SESSION_EXPIRE_HOURS = 24
BYTES_PER_MB = 1024 * 1024

# 会话存储
_sessions: Dict[str, datetime] = {}

router = APIRouter()


# === 请求/响应模型 ===

class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    success: bool
    token: Optional[str] = None
    message: str


class AddCookieRequest(BaseModel):
    cookie_str: str


class DeleteCookiesRequest(BaseModel):
    cookie_ids: List[str]


class CookieInfo(BaseModel):
    cookie_id: str
    status: str
    created_time: Optional[int] = None
    last_used: Optional[int] = None
    use_count: int
    tags: List[str] = []
    note: str = ""


class CookieListResponse(BaseModel):
    success: bool
    data: List[CookieInfo]
    total: int


class UpdateSettingsRequest(BaseModel):
    global_config: Optional[Dict[str, Any]] = None
    gemini_config: Optional[Dict[str, Any]] = None


class UpdateCookieTagsRequest(BaseModel):
    cookie_id: str
    tags: List[str]


class UpdateCookieNoteRequest(BaseModel):
    cookie_id: str
    note: str


class RefreshCookieRequest(BaseModel):
    cookie_id: str


# === 辅助函数 ===

def verify_admin_session(authorization: Optional[str] = Header(None)) -> bool:
    """验证管理员会话"""
    if not authorization:
        raise HTTPException(status_code=401, detail={"success": False, "message": "未登录"})

    token = authorization.replace("Bearer ", "")
    if token not in _sessions:
        raise HTTPException(status_code=401, detail={"success": False, "message": "会话已过期"})

    if datetime.now() > _sessions[token]:
        del _sessions[token]
        raise HTTPException(status_code=401, detail={"success": False, "message": "会话已过期"})

    return True


def _calculate_dir_size(directory: Path) -> int:
    """计算目录大小"""
    total = 0
    if directory.exists():
        for f in directory.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return total


def _format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < BYTES_PER_MB:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / BYTES_PER_MB:.1f} MB"


# === 页面路由 ===

@router.get("/login", response_class=HTMLResponse)
async def login_page():
    """登录页面"""
    html_path = TEMPLATE_DIR / "login.html"
    if html_path.exists():
        return FileResponse(html_path)
    raise HTTPException(status_code=404, detail="登录页面未找到")


@router.get("/manage", response_class=HTMLResponse)
async def manage_page():
    """管理页面"""
    html_path = TEMPLATE_DIR / "admin.html"
    if html_path.exists():
        return FileResponse(html_path)
    raise HTTPException(status_code=404, detail="管理页面未找到")


# === API端点 ===

@router.post("/api/login", response_model=LoginResponse)
async def admin_login(request: LoginRequest):
    """管理员登录"""
    admin_user = setting.global_config.get("admin_username", "admin")
    admin_pass = setting.global_config.get("admin_password", "admin")

    if request.username == admin_user and request.password == admin_pass:
        token = secrets.token_urlsafe(32)
        _sessions[token] = datetime.now() + timedelta(hours=SESSION_EXPIRE_HOURS)
        logger.info(f"[Admin] 管理员登录成功")
        return LoginResponse(success=True, token=token, message="登录成功")

    logger.warning(f"[Admin] 登录失败: {request.username}")
    return LoginResponse(success=False, message="用户名或密码错误")


@router.post("/api/logout")
async def admin_logout(_: bool = Depends(verify_admin_session), authorization: Optional[str] = Header(None)):
    """管理员登出"""
    if authorization:
        token = authorization.replace("Bearer ", "")
        if token in _sessions:
            del _sessions[token]
    return {"success": True, "message": "已登出"}


@router.get("/api/cookies", response_model=CookieListResponse)
async def list_cookies(_: bool = Depends(verify_admin_session)):
    """获取Cookie列表"""
    cookies = cookie_manager.get_cookies()

    data = []
    for cookie_id, info in cookies.items():
        data.append(CookieInfo(
            cookie_id=cookie_id,
            status=info.get("status", "未知"),
            created_time=info.get("created_time"),
            last_used=info.get("last_used"),
            use_count=info.get("use_count", 0),
            tags=info.get("tags", []),
            note=info.get("note", ""),
        ))

    return CookieListResponse(success=True, data=data, total=len(data))


@router.post("/api/cookies/add")
async def add_cookie(request: AddCookieRequest, _: bool = Depends(verify_admin_session)):
    """添加Cookie"""
    try:
        result = await cookie_manager.add_cookie(request.cookie_str)
        logger.info(f"[Admin] 添加Cookie成功: {result['cookie_id'][:8]}...")
        return {"success": True, "message": "添加成功", "data": result}
    except ValueError as e:
        return {"success": False, "message": str(e)}
    except Exception as e:
        logger.error(f"[Admin] 添加Cookie失败: {e}")
        return {"success": False, "message": f"添加失败: {e}"}


@router.post("/api/cookies/delete")
async def delete_cookies(request: DeleteCookiesRequest, _: bool = Depends(verify_admin_session)):
    """批量删除Cookie"""
    deleted = await cookie_manager.delete_cookies(request.cookie_ids)
    logger.info(f"[Admin] 删除Cookie: {deleted}个")
    return {"success": True, "message": f"已删除{deleted}个Cookie", "deleted": deleted}


@router.get("/api/settings")
async def get_settings(_: bool = Depends(verify_admin_session)):
    """获取配置"""
    return {
        "success": True,
        "global_config": setting.global_config,
        "gemini_config": setting.gemini_config,
    }


@router.post("/api/settings")
async def update_settings(request: UpdateSettingsRequest, _: bool = Depends(verify_admin_session)):
    """更新配置"""
    await setting.save(
        global_config=request.global_config,
        gemini_config=request.gemini_config
    )
    logger.info("[Admin] 配置已更新")
    return {"success": True, "message": "配置已更新"}


@router.get("/api/cache/size")
async def get_cache_size(_: bool = Depends(verify_admin_session)):
    """获取缓存大小"""
    image_size = _calculate_dir_size(TEMP_DIR)
    return {
        "success": True,
        "image_cache_size": _format_size(image_size),
        "image_cache_bytes": image_size,
        "total_cache_size": _format_size(image_size),
        "total_cache_bytes": image_size,
    }


@router.post("/api/cache/clear")
async def clear_cache(_: bool = Depends(verify_admin_session)):
    """清理所有缓存"""
    cleared = 0
    if TEMP_DIR.exists():
        for f in TEMP_DIR.rglob("*"):
            if f.is_file():
                try:
                    f.unlink()
                    cleared += 1
                except:
                    pass
    logger.info(f"[Admin] 清理缓存: {cleared}个文件")
    return {"success": True, "message": f"已清理{cleared}个缓存文件"}


@router.get("/api/stats")
async def get_stats(_: bool = Depends(verify_admin_session)):
    """获取统计信息"""
    cookies = cookie_manager.get_cookies()
    total = len(cookies)
    active = sum(1 for c in cookies.values() if c.get("status") == "正常")
    failed = sum(1 for c in cookies.values() if c.get("status") == "失效")
    unused = sum(1 for c in cookies.values() if c.get("use_count", 0) == 0)

    return {
        "success": True,
        "total": total,
        "active": active,
        "failed": failed,
        "unused": unused,
    }


@router.post("/api/cookies/tags")
async def update_cookie_tags(request: UpdateCookieTagsRequest, _: bool = Depends(verify_admin_session)):
    """更新Cookie标签"""
    await cookie_manager.update_cookie_tags(request.cookie_id, request.tags)
    return {"success": True, "message": "标签已更新"}


@router.post("/api/cookies/note")
async def update_cookie_note(request: UpdateCookieNoteRequest, _: bool = Depends(verify_admin_session)):
    """更新Cookie备注"""
    await cookie_manager.update_cookie_note(request.cookie_id, request.note)
    return {"success": True, "message": "备注已更新"}


@router.post("/api/cookies/refresh")
async def refresh_cookie(request: RefreshCookieRequest, _: bool = Depends(verify_admin_session)):
    """刷新Cookie Token"""
    success = await cookie_manager.refresh_cookie(request.cookie_id)
    if success:
        return {"success": True, "message": "Cookie刷新成功"}
    return {"success": False, "message": "Cookie刷新失败，可能已过期"}


@router.get("/api/cookies/tags/all")
async def get_all_tags(_: bool = Depends(verify_admin_session)):
    """获取所有标签"""
    cookies = cookie_manager.get_cookies()
    all_tags = set()
    for c in cookies.values():
        for tag in c.get("tags", []):
            all_tags.add(tag)
    return {"success": True, "tags": list(all_tags)}
