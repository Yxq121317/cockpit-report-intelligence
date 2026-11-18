"""应用配置：复用 backend/config.py 暴露的 Settings 实例。"""

from config import Settings, settings

__all__ = ["Settings", "settings"]
