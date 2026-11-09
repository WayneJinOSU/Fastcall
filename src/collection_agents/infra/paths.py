"""项目根目录定位。所有需要访问仓库级资源（assets/、conf/、data/）的模块统一从这里取。"""

from pathlib import Path

# src/collection_agents/infra/paths.py -> 仓库根目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]

ASSETS_DIR = PROJECT_ROOT / "assets"
CONF_DIR = PROJECT_ROOT / "conf"
DATA_DIR = PROJECT_ROOT / "data"
