"""机器人路由名单加载（routing.json -> 模块级 frozenset）。

providers.py 在创建 STT/LLM 服务时读取这里的名单决定走哪家供应商。
"""

import json
import logging
import os

from collection_agents.infra.paths import CONF_DIR


def _load_routing_config():
    """Load robot-code routing lists from conf/routing.json (path overridable
    via ROUTING_CONFIG_PATH). Missing/invalid file yields empty routing, which
    keeps every robot on the default providers."""
    path = os.getenv("ROUTING_CONFIG_PATH", str(CONF_DIR / "routing.json"))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        logging.info("Routing config not found at %s; using empty routing", path)
        return {}
    except Exception as exc:
        logging.warning("Routing config at %s unreadable (%s); using empty routing", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


_ROUTING = _load_routing_config()

DEEPSEEK_ROBOT_CODES = frozenset(_ROUTING.get("deepseek_robot_codes") or [])
VOSK_FORCED_ROBOT_CODES = frozenset(_ROUTING.get("vosk_forced_robot_codes") or [])
_DEEPGRAM_NOVA3_ROBOT_CODES = frozenset(_ROUTING.get("deepgram_nova3_robot_codes") or [])


def resolve_deepgram_model(robot_code):
    """Return the Deepgram model tier for a robot (single source of truth for
    the nova-3/nova-2 split, previously duplicated in three places)."""
    return "nova-3" if robot_code in _DEEPGRAM_NOVA3_ROBOT_CODES else "nova-2"
