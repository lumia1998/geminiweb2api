"""配置管理器 - 管理应用配置的读写"""

import toml
from pathlib import Path
from typing import Dict, Any, Optional, Literal


# 默认配置
DEFAULT_GEMINI = {
    "api_key": "",
    "proxy_url": "",
    "stream_timeout": 120,
}

DEFAULT_GLOBAL = {
    "base_url": "http://localhost:8000",
    "log_level": "INFO",
    "image_mode": "url",
    "admin_password": "admin",
    "admin_username": "admin",
    "image_cache_max_size_mb": 512,
}


class ConfigManager:
    """配置管理器"""

    def __init__(self) -> None:
        """初始化配置"""
        self.config_path: Path = Path(__file__).parents[2] / "data" / "setting.toml"
        self._storage: Optional[Any] = None
        self._ensure_exists()
        self.global_config: Dict[str, Any] = self.load("global")
        self.gemini_config: Dict[str, Any] = self.load("gemini")
    
    def _ensure_exists(self) -> None:
        """确保配置存在"""
        if not self.config_path.exists():
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            self._create_default()
    
    def _create_default(self) -> None:
        """创建默认配置"""
        default = {"gemini": DEFAULT_GEMINI.copy(), "global": DEFAULT_GLOBAL.copy()}
        with open(self.config_path, "w", encoding="utf-8") as f:
            toml.dump(default, f)
    
    def _normalize_proxy(self, proxy: str) -> str:
        """标准化代理URL（sock5/socks5 → socks5h://）"""
        if not proxy:
            return proxy

        proxy = proxy.strip()
        if proxy.startswith("sock5h://"):
            proxy = proxy.replace("sock5h://", "socks5h://", 1)
        if proxy.startswith("sock5://"):
            proxy = proxy.replace("sock5://", "socks5://", 1)
        if proxy.startswith("socks5://"):
            return proxy.replace("socks5://", "socks5h://", 1)
        return proxy

    def set_storage(self, storage: Any) -> None:
        """设置存储实例"""
        self._storage = storage

    def load(self, section: Literal["global", "gemini"]) -> Dict[str, Any]:
        """加载配置节"""
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                config = toml.load(f).get(section, {})

            # 标准化Gemini配置
            if section == "gemini":
                if "proxy_url" in config:
                    config["proxy_url"] = self._normalize_proxy(config["proxy_url"])

            return config
        except Exception as e:
            # 返回默认配置
            if section == "global":
                return DEFAULT_GLOBAL.copy()
            return DEFAULT_GEMINI.copy()
    
    async def reload(self) -> None:
        """重新加载配置"""
        self.global_config = self.load("global")
        self.gemini_config = self.load("gemini")
    
    async def _save_file(self, updates: Dict[str, Dict[str, Any]]) -> None:
        """保存到文件"""
        import aiofiles
        
        async with aiofiles.open(self.config_path, "r", encoding="utf-8") as f:
            config = toml.loads(await f.read())
        
        for section, data in updates.items():
            if section not in config:
                config[section] = {}
            config[section].update(data)
        
        async with aiofiles.open(self.config_path, "w", encoding="utf-8") as f:
            await f.write(toml.dumps(config))
    
    async def _save_storage(self, updates: Dict[str, Dict[str, Any]]) -> None:
        """保存到存储"""
        config = await self._storage.load_config()
        
        for section, data in updates.items():
            if section in config:
                config[section].update(data)
        
        await self._storage.save_config(config)

    async def save(self, global_config: Optional[Dict[str, Any]] = None, gemini_config: Optional[Dict[str, Any]] = None) -> None:
        """保存配置"""
        updates = {}
        
        if global_config:
            updates["global"] = global_config
        if gemini_config:
            updates["gemini"] = gemini_config
        
        # 选择存储方式
        if self._storage:
            await self._save_storage(updates)
        else:
            await self._save_file(updates)
        
        await self.reload()
    
    def get_proxy(self) -> str:
        """获取代理URL"""
        return self.gemini_config.get("proxy_url", "")


# 全局实例
setting = ConfigManager()
