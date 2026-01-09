"""存储抽象层 - 文件存储"""

import json
import toml
import asyncio
import aiofiles
from pathlib import Path
from typing import Dict, Any

from app.core.logger import logger


class FileStorage:
    """文件存储"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.cookie_file = data_dir / "cookies.json"
        self.config_file = data_dir / "setting.toml"
        self._cookie_lock = asyncio.Lock()
        self._config_lock = asyncio.Lock()

    async def init_db(self) -> None:
        """初始化文件存储"""
        self.data_dir.mkdir(parents=True, exist_ok=True)

        if not self.cookie_file.exists():
            await self._write(self.cookie_file, json.dumps({"cookies": {}}, indent=2))
            logger.info("[Storage] 创建cookie文件")

        if not self.config_file.exists():
            default = {
                "global": {
                    "base_url": "http://localhost:8000",
                    "admin_username": "admin",
                    "admin_password": "admin",
                    "log_level": "INFO",
                    "image_mode": "url",
                    "image_cache_max_size_mb": 512,
                },
                "gemini": {
                    "api_key": "",
                    "proxy_url": "",
                    "stream_timeout": 120,
                }
            }
            await self._write(self.config_file, toml.dumps(default))
            logger.info("[Storage] 创建配置文件")

    async def _read(self, path: Path) -> str:
        """读取文件"""
        async with aiofiles.open(path, "r", encoding="utf-8") as f:
            return await f.read()

    async def _write(self, path: Path, content: str) -> None:
        """写入文件"""
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)

    async def _load_json(self, path: Path, default: Dict, lock: asyncio.Lock) -> Dict[str, Any]:
        """加载JSON"""
        try:
            async with lock:
                if not path.exists():
                    return default
                return json.loads(await self._read(path))
        except Exception as e:
            logger.error(f"[Storage] 加载{path.name}失败: {e}")
            return default

    async def _save_json(self, path: Path, data: Dict, lock: asyncio.Lock) -> None:
        """保存JSON"""
        try:
            async with lock:
                await self._write(path, json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.error(f"[Storage] 保存{path.name}失败: {e}")
            raise

    async def _load_toml(self, path: Path, default: Dict, lock: asyncio.Lock) -> Dict[str, Any]:
        """加载TOML"""
        try:
            async with lock:
                if not path.exists():
                    return default
                return toml.loads(await self._read(path))
        except Exception as e:
            logger.error(f"[Storage] 加载{path.name}失败: {e}")
            return default

    async def _save_toml(self, path: Path, data: Dict, lock: asyncio.Lock) -> None:
        """保存TOML"""
        try:
            async with lock:
                await self._write(path, toml.dumps(data))
        except Exception as e:
            logger.error(f"[Storage] 保存{path.name}失败: {e}")
            raise

    async def load_cookies(self) -> Dict[str, Any]:
        """加载cookies"""
        return await self._load_json(self.cookie_file, {"cookies": {}}, self._cookie_lock)

    async def save_cookies(self, data: Dict[str, Any]) -> None:
        """保存cookies"""
        await self._save_json(self.cookie_file, data, self._cookie_lock)

    async def load_config(self) -> Dict[str, Any]:
        """加载配置"""
        return await self._load_toml(self.config_file, {"global": {}, "gemini": {}}, self._config_lock)

    async def save_config(self, data: Dict[str, Any]) -> None:
        """保存配置"""
        await self._save_toml(self.config_file, data, self._config_lock)


class StorageManager:
    """存储管理器（单例）"""

    _instance = None
    _storage = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def init(self) -> None:
        """初始化存储"""
        if self._initialized:
            return

        data_dir = Path(__file__).parents[2] / "data"
        self._storage = FileStorage(data_dir)
        await self._storage.init_db()
        self._initialized = True
        logger.info("[Storage] 使用file模式")

    def get_storage(self) -> FileStorage:
        """获取存储实例"""
        if not self._initialized or not self._storage:
            raise RuntimeError("StorageManager未初始化")
        return self._storage

    async def close(self) -> None:
        """关闭存储"""
        pass


# 全局实例
storage_manager = StorageManager()
