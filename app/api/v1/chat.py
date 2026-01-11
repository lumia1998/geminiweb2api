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
from app.services.gemini.tools import build_tools_prompt, parse_tool_calls

# 导入原有的 GeminiClient
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[3]))
from client import GeminiClient, CookieExpiredError


# 重试配置
MAX_RETRIES_PER_COOKIE = 3  # 每个Cookie最大重试次数
MAX_COOKIE_SWITCHES = 3     # 最大Cookie切换次数

# 注意：每次请求都创建新的 GeminiClient，不缓存会话状态
# 因为 Gemini 图片请求后会话上下文容易出问题


router = APIRouter()


# === 请求/响应模型 ===

class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]
    name: Optional[str] = None

    class Config:
        extra = "ignore"


class ToolFunction(BaseModel):
    """Tool function definition"""
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None


class Tool(BaseModel):
    """Tool definition"""
    type: str = "function"
    function: ToolFunction


class ChatCompletionRequest(BaseModel):
    model: str = "gemini-3.0-flash"
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    n: Optional[int] = None
    user: Optional[str] = None
    tools: Optional[List[Tool]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None

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
    gemini_config = setting.gemini_config
    
    # 优先使用设置中的模型ID，其次是Cookie中解析的
    model_ids = cookie_data.get("model_ids", {}).copy()
    if gemini_config.get("flash_id"):
        model_ids["flash"] = gemini_config["flash_id"]
    if gemini_config.get("pro_id"):
        model_ids["pro"] = gemini_config["pro_id"]
    if gemini_config.get("thinking_id"):
        model_ids["thinking"] = gemini_config["thinking_id"]
    
    return GeminiClient(
        secure_1psid=cookie_data["parsed"].get("__Secure-1PSID", ""),
        secure_1psidts=cookie_data["parsed"].get("__Secure-1PSIDTS", ""),
        secure_1psidcc=cookie_data["parsed"].get("__Secure-1PSIDCC", ""),
        snlm0e=cookie_data.get("snlm0e", ""),
        bl=cookie_data.get("bl", ""),
        cookies_str=cookie_data.get("cookie_str", ""),
        push_id=cookie_data.get("push_id", ""),
        model_ids=model_ids,
        debug=setting.global_config.get("log_level", "INFO") == "DEBUG",
        media_base_url=base_url,
        image_mode=setting.global_config.get("image_mode", "url"),
        proxy_url=gemini_config.get("proxy_url"),
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
    """创建聊天补全（支持自动重试和Cookie切换）"""
    last_error = None
    tried_cookies = set()
    
    for switch_count in range(MAX_COOKIE_SWITCHES):
        # 选择Cookie（排除已尝试过的失效Cookie）
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
        
        # 如果这个Cookie已经尝试过并失败，跳过
        if cookie_id in tried_cookies:
            continue
            
        logger.info(f"[Chat] 使用Cookie: {cookie_id[:8]}..., 模型: {request.model}, 切换次数: {switch_count}")

        # 对当前Cookie进行重试
        for retry in range(MAX_RETRIES_PER_COOKIE):
            try:
                # 每次都创建新的客户端，确保会话干净（避免图片请求后上下文问题）
                client = _create_gemini_client(cookie_data)

                if request.stream:
                    # 流式响应（流式不支持重试，直接返回）
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
                    # 构建消息列表
                    messages = [{"role": m.role, "content": m.content} for m in request.messages]
                    
                    # 如果有 tools，添加 tools 提示词到最后一条用户消息
                    tools_data = None
                    if request.tools:
                        tools_data = [{"type": t.type, "function": t.function.model_dump()} for t in request.tools]
                        tools_prompt = build_tools_prompt(tools_data)
                        if tools_prompt and messages:
                            # 找到最后一条用户消息并追加 tools 提示词
                            for i in range(len(messages) - 1, -1, -1):
                                if messages[i]["role"] == "user":
                                    if isinstance(messages[i]["content"], str):
                                        messages[i]["content"] = tools_prompt + messages[i]["content"]
                                    break
                    
                    # 非流式响应
                    response = client.chat(
                        messages=messages,
                        model=request.model
                    )

                    # 成功后重置失败计数
                    cookie_manager.reset_failure(cookie_id)
                    
                    # 更新会话 hash 缓存（用于下次判断是否是会话延续）
                    _session_hash_cache[cookie_id] = _get_user_messages_hash(request.messages)

                    content = response.choices[0].message.content if response.choices else ""
                    
                    # 如果有 tools，尝试解析 tool_calls
                    tool_calls = None
                    finish_reason = "stop"
                    if tools_data and content:
                        parsed_calls, remaining_content = parse_tool_calls(content)
                        if parsed_calls:
                            tool_calls = parsed_calls
                            content = None  # OpenAI 规范：有 tool_calls 时 content 为 null
                            finish_reason = "tool_calls"
                        else:
                            content = remaining_content or content
                    
                    # 构建响应消息
                    response_message = {"role": "assistant", "content": content}
                    if tool_calls:
                        response_message["tool_calls"] = tool_calls

                    return ChatCompletionResponse(
                        id=f"chatcmpl-{uuid.uuid4().hex[:8]}",
                        created=int(time.time()),
                        model=request.model,
                        choices=[
                            ChatCompletionChoice(
                                index=0,
                                message=response_message,
                                finish_reason=finish_reason
                            )
                        ],
                        usage=Usage(
                            prompt_tokens=response.usage.prompt_tokens,
                            completion_tokens=response.usage.completion_tokens,
                            total_tokens=response.usage.total_tokens
                        )
                    )

            except CookieExpiredError as e:
                logger.warning(f"[Chat] Cookie过期: {cookie_id[:8]}... - {e}")
                last_error = e
                cookie_manager.record_failure(cookie_id, str(e))
                tried_cookies.add(cookie_id)
                break  # Cookie过期直接切换，不重试
                
            except Exception as e:
                last_error = e
                if retry < MAX_RETRIES_PER_COOKIE - 1:
                    logger.warning(f"[Chat] 请求失败，重试 {retry + 1}/{MAX_RETRIES_PER_COOKIE}: {e}")
                else:
                    logger.error(f"[Chat] Cookie {cookie_id[:8]}... 重试{MAX_RETRIES_PER_COOKIE}次后失败: {e}")
                    cookie_manager.record_failure(cookie_id, str(e))
                    tried_cookies.add(cookie_id)
    
    # 所有Cookie都失败
    error_msg = str(last_error) if last_error else "所有Cookie都不可用"
    logger.error(f"[Chat] 所有Cookie都失败: {error_msg}")
    raise HTTPException(
        status_code=500,
        detail={
            "error": {
                "message": f"请求失败（已尝试{len(tried_cookies)}个Cookie）: {error_msg}",
                "type": "api_error",
                "code": "all_cookies_failed"
            }
        }
    )

