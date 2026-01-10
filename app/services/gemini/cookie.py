"""Gemini Cookie 管理器 - 多Cookie负载均衡和状态管理"""

import time
import asyncio
import httpx
import re
from typing import Dict, Any, Optional, List
from pathlib import Path

from app.core.logger import logger


# 常量
TIMEOUT = 30


class GeminiCookieManager:
    """Cookie管理器（单例）"""

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
        self._dirty = False
        self._batch_save_task = None
        self._initialized = True

    def set_storage(self, storage) -> None:
        """设置存储实例"""
        self._storage = storage

    async def _load_data(self) -> None:
        """异步加载Cookie数据"""
        if not self._storage:
            logger.warning("[Cookie] 未设置存储，无法加载数据")
            return

        try:
            data = await self._storage.load_cookies()
            self._cookies = data.get("cookies", {})
            logger.info(f"[Cookie] 加载了 {len(self._cookies)} 个Cookie")
        except Exception as e:
            logger.error(f"[Cookie] 加载数据失败: {e}")
            self._cookies = {}

    async def _save_data(self) -> None:
        """保存Cookie数据"""
        if not self._storage:
            return

        try:
            await self._storage.save_cookies({"cookies": self._cookies})
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

    async def add_cookie(self, cookie_str: str) -> Dict[str, Any]:
        """添加Cookie并解析获取Token"""
        # 解析Cookie字符串
        parsed = self._parse_cookie_string(cookie_str)
        if not parsed.get("__Secure-1PSID"):
            raise ValueError("Cookie缺少必要的 __Secure-1PSID")

        # 生成唯一ID（使用前16位）
        cookie_id = parsed.get("__Secure-1PSID", "")[:16]
        
        if cookie_id in self._cookies:
            raise ValueError("该Cookie已存在")

        # 尝试获取SNLM0E和PUSH_ID
        tokens = await self._fetch_tokens(cookie_str)
        
        # 创建Cookie记录
        self._cookies[cookie_id] = {
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
            "tags": [],
            "note": "",
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

        logger.debug(f"[Cookie] 选择Cookie: {selected_id[:8]}...")
        return {"cookie_id": selected_id, **selected_data}

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

    async def update_cookie(self, cookie_id: str, cookie_str: str) -> Dict[str, Any]:
        """更新Cookie内容（保留原有的tags、note等元数据）"""
        if cookie_id not in self._cookies:
            raise ValueError("Cookie不存在")

        # 解析新的Cookie字符串
        parsed = self._parse_cookie_string(cookie_str)
        if not parsed.get("__Secure-1PSID"):
            raise ValueError("Cookie缺少必要的 __Secure-1PSID")

        # 保存原有元数据
        old_data = self._cookies[cookie_id]
        old_tags = old_data.get("tags", [])
        old_note = old_data.get("note", "")
        old_created_time = old_data.get("created_time")
        old_use_count = old_data.get("use_count", 0)

        # 尝试获取新的Token
        tokens = await self._fetch_tokens(cookie_str)

        # 更新Cookie记录
        self._cookies[cookie_id] = {
            "cookie_str": cookie_str,
            "parsed": parsed,
            "snlm0e": tokens.get("snlm0e", ""),
            "push_id": tokens.get("push_id", ""),
            "bl": tokens.get("bl", ""),
            "model_ids": tokens.get("model_ids", {}),
            "status": "正常" if tokens.get("snlm0e") else "失效",
            "created_time": old_created_time,
            "last_used": int(time.time()),
            "use_count": old_use_count,
            "failure_count": 0,
            "tags": old_tags,
            "note": old_note,
        }

        self._mark_dirty()
        await self._save_data()

        logger.info(f"[Cookie] 更新Cookie成功: {cookie_id[:8]}...")
        return {"cookie_id": cookie_id, "status": self._cookies[cookie_id]["status"]}


    def record_failure(self, cookie_id: str, error: str) -> None:
        """记录失败"""
        if cookie_id in self._cookies:
            self._cookies[cookie_id]["failure_count"] = self._cookies[cookie_id].get("failure_count", 0) + 1
            # 连续失败3次标记为失效
            if self._cookies[cookie_id]["failure_count"] >= 3:
                self._cookies[cookie_id]["status"] = "失效"
                logger.warning(f"[Cookie] {cookie_id[:8]}... 连续失败，标记为失效")
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

                # 提取 PUSH_ID - 使用多种模式匹配 feeds/xxx 格式
                push_id_patterns = [
                    r'"push[_-]?id"[:\s]+"(feeds/[a-z0-9]+)"',  # "push_id": "feeds/xxx"
                    r'"feedName"[:\s]+"(feeds/[a-z0-9]+)"',      # "feedName": "feeds/xxx"
                    r'"clientId"[:\s]+"(feeds/[a-z0-9]+)"',      # "clientId": "feeds/xxx"
                    r'(feeds/[a-z0-9]{10,})',                     # 直接匹配 feeds/xxx 格式
                ]
                for pattern in push_id_patterns:
                    push_id_match = re.search(pattern, text, re.IGNORECASE)
                    if push_id_match:
                        result["push_id"] = push_id_match.group(1)
                        break

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
        tokens = await self._fetch_tokens(cookie_data["cookie_str"])
        
        if tokens.get("snlm0e"):
            cookie_data["snlm0e"] = tokens["snlm0e"]
            cookie_data["push_id"] = tokens.get("push_id", cookie_data.get("push_id", ""))
            cookie_data["bl"] = tokens.get("bl", cookie_data.get("bl", ""))
            cookie_data["model_ids"] = tokens.get("model_ids", cookie_data.get("model_ids", {}))
            cookie_data["status"] = "正常"
            cookie_data["failure_count"] = 0
            self._mark_dirty()
            await self._save_data()
            return True
        else:
            cookie_data["status"] = "失效"
            self._mark_dirty()
            await self._save_data()
            return False


# 全局实例
cookie_manager = GeminiCookieManager()
