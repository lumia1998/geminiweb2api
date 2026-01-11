"""Gemini Cookie 管理器 - 多Cookie负载均衡、会话持久化和状态管理

参考 CLIProxyAPI 的设计模式，实现:
- Cookie 格式规范化 (secure_1psid, secure_1psidts, label 等)
- 会话持久化 (conversations)
- 会话绑定 (同一 session_key 使用同一 Cookie)
- 自动重试切换 Cookie
"""

import time
import asyncio
import hashlib
import httpx
import re
from typing import Dict, Any, Optional, List, Callable, TypeVar
from dataclasses import dataclass, asdict
from datetime import datetime

from app.core.logger import logger


# 常量
TIMEOUT = 30
MAX_RETRY_COUNT = 3  # 最大重试次数

T = TypeVar('T')


@dataclass
class SessionContext:
    """会话上下文 - 参考 CLIProxyAPI 的 ConversationRecord"""
    cookie_id: str
    conversation_id: str = ""
    response_id: str = ""
    choice_id: str = ""
    model: str = ""
    updated_at: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionContext":
        return cls(
            cookie_id=data.get("cookie_id", ""),
            conversation_id=data.get("conversation_id", ""),
            response_id=data.get("response_id", ""),
            choice_id=data.get("choice_id", ""),
            model=data.get("model", ""),
            updated_at=data.get("updated_at", 0),
        )


class GeminiCookieManager:
    """Cookie管理器（单例）- 参考 CLIProxyAPI 的设计"""

    _instance: Optional['GeminiCookieManager'] = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return

        self._storage = None
        self._cookies: Dict[str, Any] = {}
        self._sessions: Dict[str, SessionContext] = {}  # 会话存储
        self._session_mapping: Dict[str, str] = {}  # session_key -> cookie_id 映射
        self._dirty = False
        self._batch_save_task = None
        self._initialized = True

    def set_storage(self, storage) -> None:
        """设置存储实例"""
        self._storage = storage

    async def _load_data(self) -> None:
        """异步加载Cookie和会话数据"""
        if not self._storage:
            logger.warning("[Cookie] 未设置存储，无法加载数据")
            return

        try:
            data = await self._storage.load_cookies()
            self._cookies = data.get("cookies", {})
            
            # 加载会话数据
            sessions_data = data.get("sessions", {})
            self._sessions = {}
            for key, val in sessions_data.items():
                try:
                    self._sessions[key] = SessionContext.from_dict(val)
                except Exception:
                    continue
            
            logger.info(f"[Cookie] 加载了 {len(self._cookies)} 个Cookie, {len(self._sessions)} 个会话")
        except Exception as e:
            logger.error(f"[Cookie] 加载数据失败: {e}")
            self._cookies = {}
            self._sessions = {}

    async def _save_data(self) -> None:
        """保存Cookie和会话数据"""
        if not self._storage:
            return

        try:
            # 序列化会话数据
            sessions_data = {k: v.to_dict() for k, v in self._sessions.items()}
            
            await self._storage.save_cookies({
                "cookies": self._cookies,
                "sessions": sessions_data,
            })
        except Exception as e:
            logger.error(f"[Cookie] 保存数据失败: {e}")

    def _mark_dirty(self) -> None:
        """标记有待保存的数据"""
        self._dirty = True

    async def _batch_save_worker(self) -> None:
        """批量保存后台任务"""
        while True:
            await asyncio.sleep(5)
            if self._dirty:
                await self._save_data()
                self._dirty = False

    async def start_batch_save(self) -> None:
        """启动批量保存任务"""
        if self._batch_save_task is None:
            self._batch_save_task = asyncio.create_task(self._batch_save_worker())

    async def shutdown(self) -> None:
        """关闭并刷新所有待保存数据"""
        if self._batch_save_task:
            self._batch_save_task.cancel()
        if self._dirty:
            await self._save_data()
        logger.info("[Cookie] Cookie管理器已关闭")

    def _generate_cookie_id(self, psid: str) -> str:
        """生成稳定的 Cookie ID - 参考 CLIProxyAPI 的 hash 方式"""
        if not psid:
            return f"gemini-web-{int(time.time())}"
        
        # 使用 SHA256 hash 的前16位作为 ID
        hash_value = hashlib.sha256(psid.encode()).hexdigest()[:16]
        return f"gemini-web-{hash_value}"

    async def add_cookie(self, cookie_str: str, label: str = "") -> Dict[str, Any]:
        """添加Cookie并解析获取Token - 使用 CLIProxyAPI 格式"""
        # 解析Cookie字符串
        parsed = self._parse_cookie_string(cookie_str)
        psid = parsed.get("__Secure-1PSID", "")
        if not psid:
            raise ValueError("Cookie缺少必要的 __Secure-1PSID")

        # 生成稳定 ID（参考 CLIProxyAPI）
        cookie_id = self._generate_cookie_id(psid)
        
        if cookie_id in self._cookies:
            raise ValueError("该Cookie已存在")

        # 尝试获取SNLM0E和PUSH_ID
        tokens = await self._fetch_tokens(cookie_str)

        # 创建Cookie记录 - 使用 CLIProxyAPI 格式
        self._cookies[cookie_id] = {
            # CLIProxyAPI 标准字段
            "secure_1psid": psid,
            "secure_1psidts": parsed.get("__Secure-1PSIDTS", ""),
            "type": "gemini-web",
            "label": label or cookie_id[:20],
            "last_refresh": datetime.now().isoformat(),
            
            # 扩展字段
            "cookie_str": cookie_str,
            "parsed": parsed,
            "snlm0e": tokens.get("snlm0e", ""),
            "push_id": tokens.get("push_id", ""),
            "bl": tokens.get("bl", ""),
            "model_ids": tokens.get("model_ids", {}),
            "status": "正常" if tokens.get("snlm0e") else "失效",
            "created_time": int(time.time()),
            "last_used": None,
            "use_count": 0,
            "failure_count": 0,
        }

        self._mark_dirty()
        await self._save_data()
        
        return {"cookie_id": cookie_id, "status": self._cookies[cookie_id]["status"]}

    async def delete_cookies(self, cookie_ids: List[str]) -> int:
        """批量删除Cookie"""
        deleted = 0
        for cookie_id in cookie_ids:
            if cookie_id in self._cookies:
                del self._cookies[cookie_id]
                deleted += 1
                
                # 同时删除关联的会话
                keys_to_delete = [k for k, v in self._sessions.items() if v.cookie_id == cookie_id]
                for key in keys_to_delete:
                    del self._sessions[key]

        if deleted > 0:
            self._mark_dirty()
            await self._save_data()

        return deleted

    def get_cookies(self) -> Dict[str, Any]:
        """获取所有Cookie"""
        return self._cookies

    def select_cookie(self) -> Optional[Dict[str, Any]]:
        """选择最优Cookie（负载均衡）"""
        available = []
        
        for cookie_id, data in self._cookies.items():
            if data.get("status") == "正常" and data.get("snlm0e"):
                available.append((cookie_id, data))

        if not available:
            logger.warning("[Cookie] 没有可用的Cookie")
            return None

        # 按使用次数排序，选择使用最少的
        available.sort(key=lambda x: x[1].get("use_count", 0))
        selected_id, selected_data = available[0]

        # 更新使用统计
        self._cookies[selected_id]["last_used"] = int(time.time())
        self._cookies[selected_id]["use_count"] = selected_data.get("use_count", 0) + 1
        self._mark_dirty()

        logger.debug(f"[Cookie] 选择Cookie: {selected_id}")
        return {"cookie_id": selected_id, **selected_data}

    def select_cookie_for_session(self, session_key: str = None) -> Optional[Dict[str, Any]]:
        """为特定会话选择Cookie（保持会话连续性）
        
        参考 CLIProxyAPI 的会话绑定策略：
        - 如果 session_key 有已绑定的 Cookie，优先使用
        - 否则使用负载均衡选择
        """
        if session_key:
            # 检查是否有已保存的会话
            if session_key in self._sessions:
                session = self._sessions[session_key]
                cookie_id = session.cookie_id
                
                # 验证 Cookie 仍然可用
                if cookie_id in self._cookies and self._cookies[cookie_id].get("status") == "正常":
                    self._cookies[cookie_id]["last_used"] = int(time.time())
                    self._cookies[cookie_id]["use_count"] = self._cookies[cookie_id].get("use_count", 0) + 1
                    self._mark_dirty()
                    
                    logger.debug(f"[Cookie] 复用会话绑定的Cookie: {cookie_id}")
                    return {"cookie_id": cookie_id, "session": session, **self._cookies[cookie_id]}
        
        # 否则使用负载均衡选择
        cookie = self.select_cookie()
        
        # 记录会话映射
        if session_key and cookie:
            self._session_mapping[session_key] = cookie["cookie_id"]
        
        return cookie

    def get_session(self, session_key: str) -> Optional[SessionContext]:
        """获取会话上下文"""
        return self._sessions.get(session_key)

    def save_session(self, session_key: str, cookie_id: str, 
                     conversation_id: str = "", response_id: str = "", 
                     choice_id: str = "", model: str = "") -> None:
        """保存会话上下文 - 参考 CLIProxyAPI 的 persistConversation"""
        self._sessions[session_key] = SessionContext(
            cookie_id=cookie_id,
            conversation_id=conversation_id,
            response_id=response_id,
            choice_id=choice_id,
            model=model,
            updated_at=int(time.time()),
        )
        self._mark_dirty()
        logger.debug(f"[Cookie] 保存会话: {session_key}, conv_id: {conversation_id[:16] if conversation_id else 'None'}...")

    def delete_session(self, session_key: str) -> None:
        """删除会话"""
        if session_key in self._sessions:
            del self._sessions[session_key]
            self._mark_dirty()

    def get_all_sessions(self) -> Dict[str, SessionContext]:
        """获取所有会话"""
        return self._sessions

    async def execute_with_retry(
        self, 
        request_func: Callable[[Dict[str, Any]], T],
        session_key: str = None,
        max_retries: int = MAX_RETRY_COUNT
    ) -> T:
        """执行请求，失败自动切换Cookie重试
        
        参考 CLIProxyAPI 的重试逻辑
        """
        last_error = None
        tried_cookies = set()
        
        for attempt in range(max_retries):
            # 首次尝试使用会话绑定的Cookie，后续尝试切换
            if attempt == 0:
                cookie = self.select_cookie_for_session(session_key)
            else:
                cookie = self.select_cookie()
            
            if not cookie:
                break
            
            cookie_id = cookie["cookie_id"]
            
            # 跳过已尝试过的Cookie
            if cookie_id in tried_cookies:
                # 尝试获取其他Cookie
                for cid, data in self._cookies.items():
                    if cid not in tried_cookies and data.get("status") == "正常":
                        cookie = {"cookie_id": cid, **data}
                        cookie_id = cid
                        break
                else:
                    break  # 没有更多可用Cookie
            
            tried_cookies.add(cookie_id)
            
            try:
                result = await request_func(cookie)
                self.reset_failure(cookie_id)  # 成功，重置失败计数
                return result
            except Exception as e:
                last_error = e
                self.record_failure(cookie_id, str(e))
                logger.warning(f"[Cookie] 第{attempt+1}次尝试失败 ({cookie_id[:16]}...): {e}")
                
                if attempt < max_retries - 1:
                    logger.info(f"[Cookie] 切换Cookie重试...")
        
        raise last_error or Exception("所有Cookie都失败了")

    async def update_cookie_tags(self, cookie_id: str, tags: List[str]) -> None:
        """更新Cookie标签"""
        if cookie_id in self._cookies:
            self._cookies[cookie_id]["tags"] = tags
            self._mark_dirty()
            await self._save_data()

    async def update_cookie_note(self, cookie_id: str, note: str) -> None:
        """更新Cookie备注"""
        if cookie_id in self._cookies:
            self._cookies[cookie_id]["note"] = note
            self._mark_dirty()
            await self._save_data()

    async def update_cookie_label(self, cookie_id: str, label: str) -> None:
        """更新Cookie标签（参考 CLIProxyAPI）"""
        if cookie_id in self._cookies:
            self._cookies[cookie_id]["label"] = label
            self._mark_dirty()
            await self._save_data()

    def record_failure(self, cookie_id: str, error: str) -> None:
        """记录失败"""
        if cookie_id in self._cookies:
            self._cookies[cookie_id]["failure_count"] = self._cookies[cookie_id].get("failure_count", 0) + 1
            # 连续失败3次标记为失效
            if self._cookies[cookie_id]["failure_count"] >= 3:
                self._cookies[cookie_id]["status"] = "失效"
                logger.warning(f"[Cookie] {cookie_id[:16]}... 连续失败，标记为失效")
            self._mark_dirty()

    def reset_failure(self, cookie_id: str) -> None:
        """重置失败计数"""
        if cookie_id in self._cookies:
            self._cookies[cookie_id]["failure_count"] = 0
            self._cookies[cookie_id]["status"] = "正常"
            self._mark_dirty()

    def _parse_cookie_string(self, cookie_str: str) -> Dict[str, str]:
        """解析Cookie字符串"""
        result = {}
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                result[key.strip()] = value.strip()
        return result

    async def _fetch_tokens(self, cookie_str: str) -> Dict[str, Any]:
        """从Gemini页面获取SNLM0E、PUSH_ID等Token"""
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                # 设置cookies
                cookies = {}
                for item in cookie_str.split(";"):
                    item = item.strip()
                    if "=" in item:
                        key, value = item.split("=", 1)
                        cookies[key.strip()] = value.strip()

                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                }

                resp = await client.get("https://gemini.google.com/", cookies=cookies, headers=headers)
                text = resp.text

                result = {}

                # 提取 SNlM0e
                snlm0e_match = re.search(r'"SNlM0e":"([^"]+)"', text)
                if snlm0e_match:
                    result["snlm0e"] = snlm0e_match.group(1)

                # 提取 BL
                bl_match = re.search(r'"cfb2h":"([^"]+)"', text)
                if bl_match:
                    result["bl"] = bl_match.group(1)

                # 提取 PUSH_ID (从页面中查找)
                push_id_match = re.search(r'"FdrFJe":"([^"]+)"', text)
                if push_id_match:
                    result["push_id"] = push_id_match.group(1)

                # 提取模型ID
                model_ids = {}
                # Flash
                flash_match = re.search(r'"flash"[^}]*"id":"([^"]+)"', text)
                if flash_match:
                    model_ids["flash"] = flash_match.group(1)
                # Pro  
                pro_match = re.search(r'"pro"[^}]*"id":"([^"]+)"', text)
                if pro_match:
                    model_ids["pro"] = pro_match.group(1)
                # Thinking
                thinking_match = re.search(r'"thinking"[^}]*"id":"([^"]+)"', text)
                if thinking_match:
                    model_ids["thinking"] = thinking_match.group(1)

                if model_ids:
                    result["model_ids"] = model_ids

                logger.info(f"[Cookie] Token获取成功: snlm0e={'是' if result.get('snlm0e') else '否'}, push_id={'是' if result.get('push_id') else '否'}")
                return result

        except Exception as e:
            logger.error(f"[Cookie] 获取Token失败: {e}")
            return {}

    async def refresh_cookie(self, cookie_id: str) -> bool:
        """刷新Cookie的Token"""
        if cookie_id not in self._cookies:
            return False

        cookie_data = self._cookies[cookie_id]
        
        # 尝试自动刷新 1PSIDTS
        new_psidts = await self._rotate_1psidts(cookie_data)
        if new_psidts:
            cookie_data["secure_1psidts"] = new_psidts
            if cookie_data.get("parsed"):
                cookie_data["parsed"]["__Secure-1PSIDTS"] = new_psidts
            # 更新 cookie_str
            cookie_data["cookie_str"] = self._rebuild_cookie_str(cookie_data)
            logger.info(f"[Cookie] 自动刷新 1PSIDTS 成功: {cookie_id[:16]}...")
        
        # 重新获取 tokens
        tokens = await self._fetch_tokens(cookie_data.get("cookie_str", ""))
        
        if tokens.get("snlm0e"):
            cookie_data["snlm0e"] = tokens["snlm0e"]
            cookie_data["push_id"] = tokens.get("push_id", cookie_data.get("push_id", ""))
            cookie_data["bl"] = tokens.get("bl", cookie_data.get("bl", ""))
            cookie_data["model_ids"] = tokens.get("model_ids", cookie_data.get("model_ids", {}))
            cookie_data["status"] = "正常"
            cookie_data["failure_count"] = 0
            cookie_data["last_refresh"] = datetime.now().isoformat()
            self._mark_dirty()
            await self._save_data()
            return True
        else:
            cookie_data["status"] = "失效"
            self._mark_dirty()
            await self._save_data()
            return False

    async def _rotate_1psidts(self, cookie_data: Dict[str, Any]) -> Optional[str]:
        """自动刷新 __Secure-1PSIDTS Cookie
        
        参考 CLIProxyAPI 的 rotate1PSIDTS 实现:
        调用 https://accounts.google.com/RotateCookies 端点
        """
        ENDPOINT_ROTATE_COOKIES = "https://accounts.google.com/RotateCookies"
        
        # 获取必要的 cookie
        psid = cookie_data.get("secure_1psid") or cookie_data.get("parsed", {}).get("__Secure-1PSID", "")
        psidts = cookie_data.get("secure_1psidts") or cookie_data.get("parsed", {}).get("__Secure-1PSIDTS", "")
        
        if not psid:
            logger.warning("[Cookie] 无法刷新: 缺少 __Secure-1PSID")
            return None
        
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                # 设置 cookies
                cookies = {
                    "__Secure-1PSID": psid,
                }
                if psidts:
                    cookies["__Secure-1PSIDTS"] = psidts
                
                # 添加其他可能需要的 cookies
                if cookie_data.get("parsed"):
                    for key in ["NID", "SID", "HSID", "SSID", "APISID", "SAPISID"]:
                        if cookie_data["parsed"].get(key):
                            cookies[key] = cookie_data["parsed"][key]
                
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                }
                
                # 发送请求 - 参考 CLIProxyAPI 的请求格式
                # body: [000,"-0000000000000000000"]
                body = '[000,"-0000000000000000000"]'
                
                resp = await client.post(
                    ENDPOINT_ROTATE_COOKIES,
                    content=body,
                    cookies=cookies,
                    headers=headers,
                )
                
                if resp.status_code == 401:
                    logger.warning("[Cookie] 刷新 1PSIDTS 失败: 未授权")
                    return None
                
                if resp.status_code < 200 or resp.status_code >= 300:
                    logger.warning(f"[Cookie] 刷新 1PSIDTS 失败: {resp.status_code}")
                    return None
                
                # 从响应 cookies 中提取新的 __Secure-1PSIDTS
                for cookie in resp.cookies.jar:
                    if cookie.name == "__Secure-1PSIDTS":
                        logger.debug(f"[Cookie] 获取到新的 1PSIDTS: {cookie.value[:20]}...")
                        return cookie.value
                
                # 检查 Set-Cookie header
                set_cookie = resp.headers.get("set-cookie", "")
                if "__Secure-1PSIDTS=" in set_cookie:
                    import re
                    match = re.search(r'__Secure-1PSIDTS=([^;]+)', set_cookie)
                    if match:
                        return match.group(1)
                
                logger.debug("[Cookie] 刷新请求成功但未返回新的 1PSIDTS")
                return None
                
        except Exception as e:
            logger.error(f"[Cookie] 刷新 1PSIDTS 异常: {e}")
            return None

    def _rebuild_cookie_str(self, cookie_data: Dict[str, Any]) -> str:
        """重建 cookie 字符串"""
        parsed = cookie_data.get("parsed", {})
        
        # 使用新值覆盖
        if cookie_data.get("secure_1psid"):
            parsed["__Secure-1PSID"] = cookie_data["secure_1psid"]
        if cookie_data.get("secure_1psidts"):
            parsed["__Secure-1PSIDTS"] = cookie_data["secure_1psidts"]
        
        # 重建字符串
        parts = [f"{k}={v}" for k, v in parsed.items() if v]
        return "; ".join(parts)

    async def auto_refresh_all(self) -> Dict[str, bool]:
        """自动刷新所有 Cookie 的 1PSIDTS
        
        返回: {cookie_id: success}
        """
        results = {}
        for cookie_id in list(self._cookies.keys()):
            try:
                success = await self.refresh_cookie(cookie_id)
                results[cookie_id] = success
                if success:
                    logger.info(f"[Cookie] 刷新成功: {cookie_id[:16]}...")
                else:
                    logger.warning(f"[Cookie] 刷新失败: {cookie_id[:16]}...")
            except Exception as e:
                logger.error(f"[Cookie] 刷新异常 {cookie_id[:16]}...: {e}")
                results[cookie_id] = False
        return results


# 全局实例
cookie_manager = GeminiCookieManager()

