"""后端配置入口：复用项目根目录 config.py 的 Settings 定义。

后端按规格以 ``backend/`` 为工作目录运行（``uvicorn app.main:app``），
此时仓库根目录不在 sys.path 上，因此这里用 importlib 按文件路径加载，
避免与名为 ``config`` 的本模块冲突。
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
_ROOT_CONFIG_PATH = ROOT_DIR / "config.py"

_spec = spec_from_file_location("_smartdeck_root_config", _ROOT_CONFIG_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"无法加载根目录配置：{_ROOT_CONFIG_PATH}")
_root_config = module_from_spec(_spec)
_spec.loader.exec_module(_root_config)

Settings = _root_config.Settings
settings = _root_config.settings

__all__ = ["Settings", "settings"]
