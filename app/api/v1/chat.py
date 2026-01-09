"""Chat Completions API - OpenAI兼容的聊天接口"""

import json
import time
import uuid
from typing import List, Dict, Any, Optional, Union
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth import auth_manager
from app.core.config import setting
from app.core.logger import logger
from app.services.gemini.cookie import cookie_manager

# 导入原有的 GeminiClient
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[3]))
from client import GeminiClient, CookieExpiredError


router = APIRouter()


# === 请求/响应模型 ===

class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]
    name: Optional[str] = None

    class Config:
        extra = "ignore"


class ChatCompletionRequest(BaseModel):
    model: str = "gemini-3.0-flash"
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    n: Optional[int] = None
    user: Optional[str] = None

    class Config:
        extra = "ignore"


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


# === 辅助函数 ===

def _create_gemini_client(cookie_data: Dict[str, Any]) -> GeminiClient:
    """根据Cookie数据创建GeminiClient"""
    base_url = setting.global_config.get("base_url", "http://localhost:8000")
    
    return GeminiClient(
        secure_1psid=cookie_data["parsed"].get("__Secure-1PSID", ""),
        secure_1psidts=cookie_data["parsed"].get("__Secure-1PSIDTS", ""),
        secure_1psidcc=cookie_data["parsed"].get("__Secure-1PSIDCC", ""),
        snlm0e=cookie_data.get("snlm0e", ""),
        bl=cookie_data.get("bl", ""),
        cookies_str=cookie_data.get("cookie_str", ""),
        push_id=cookie_data.get("push_id", ""),
        model_ids=cookie_data.get("model_ids", {}),
        debug=setting.global_config.get("log_level", "INFO") == "DEBUG",
        media_base_url=base_url,
    )


def _format_sse_message(data: dict) -> str:
    """格式化SSE消息"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_chat_response(client: GeminiClient, messages: List[Dict], model: str, cookie_id: str):
    """流式响应生成器"""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:8]}"
    created = int(time.time())

    try:
        # 调用Gemini API
        response = client.chat(
            messages=[{"role": m.role, "content": m.content} for m in messages],
            model=model
        )
        
        content = response.choices[0].message.content if response.choices else ""
        
        # 模拟流式输出（将内容分块发送）
        chunk_size = 20
        for i in range(0, len(content), chunk_size):
            chunk = content[i:i+chunk_size]
            yield _format_sse_message({
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": chunk},
                    "finish_reason": None
                }]
            })

        # 发送结束标记
        yield _format_sse_message({
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        })
        yield "data: [DONE]\n\n"

        # 成功后重置失败计数
        cookie_manager.reset_failure(cookie_id)

    except CookieExpiredError as e:
        logger.error(f"[Chat] Cookie过期: {e}")
        cookie_manager.record_failure(cookie_id, str(e))
        yield _format_sse_message({
            "error": {
                "message": "Cookie已过期，请在管理后台更新",
                "type": "authentication_error",
                "code": "cookie_expired"
            }
        })
    except Exception as e:
        logger.error(f"[Chat] 请求失败: {e}")
        cookie_manager.record_failure(cookie_id, str(e))
        yield _format_sse_message({
            "error": {
                "message": str(e),
                "type": "api_error",
                "code": "internal_error"
            }
        })


# === API端点 ===

@router.post("/chat/completions")
async def chat_completions(request: ChatCompletionRequest, _: str = Depends(auth_manager.verify)):
    """创建聊天补全"""
    # 选择Cookie
    cookie_data = cookie_manager.select_cookie()
    if not cookie_data:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "message": "没有可用的Cookie，请在管理后台添加Cookie",
                    "type": "service_unavailable",
                    "code": "no_cookies_available"
                }
            }
        )

    cookie_id = cookie_data["cookie_id"]
    logger.info(f"[Chat] 使用Cookie: {cookie_id[:8]}..., 模型: {request.model}")

    try:
        # 创建客户端
        client = _create_gemini_client(cookie_data)

        if request.stream:
            # 流式响应
            return StreamingResponse(
                _stream_chat_response(client, request.messages, request.model, cookie_id),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                }
            )
        else:
            # 非流式响应
            response = client.chat(
                messages=[{"role": m.role, "content": m.content} for m in request.messages],
                model=request.model
            )

            # 成功后重置失败计数
            cookie_manager.reset_failure(cookie_id)

            return ChatCompletionResponse(
                id=f"chatcmpl-{uuid.uuid4().hex[:8]}",
                created=int(time.time()),
                model=request.model,
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message={
                            "role": "assistant",
                            "content": response.choices[0].message.content if response.choices else ""
                        },
                        finish_reason="stop"
                    )
                ],
                usage=Usage(
                    prompt_tokens=response.usage.prompt_tokens,
                    completion_tokens=response.usage.completion_tokens,
                    total_tokens=response.usage.total_tokens
                )
            )

    except CookieExpiredError as e:
        logger.error(f"[Chat] Cookie过期: {cookie_id[:8]}... - {e}")
        cookie_manager.record_failure(cookie_id, str(e))
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "Cookie已过期，请在管理后台更新",
                    "type": "authentication_error",
                    "code": "cookie_expired"
                }
            }
        )
    except Exception as e:
        logger.error(f"[Chat] 请求失败: {e}")
        cookie_manager.record_failure(cookie_id, str(e))
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "message": str(e),
                    "type": "api_error",
                    "code": "internal_error"
                }
            }
        )
