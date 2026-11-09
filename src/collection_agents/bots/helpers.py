"""bots 共享的纯工具函数集（无类、无继承，各 bot 显式调用）。

这里只放 standard / realtime / copilot 三个 bot 都会用到的通用逻辑：
- build_call_context: 从 call_data 提取一次通话的全部上下文字段
- build_session_state: 构造通话运行期状态 dict
"""

import time

from collection_agents.services.call_config import (
    BotV3ConfigManager,
    is_voicemail_hangup_action,
)


def build_call_context(call_data) -> dict:
    """Extract only the technical configuration needed to run a call."""
    config_manager = BotV3ConfigManager(call_data)
    config = call_data.get("data", {}).get("config", {})
    voicemail_config = config_manager.get_voicemail_config()

    return {
        "call_data": call_data,
        "config_manager": config_manager,
        "call_uuid": call_data.get("data", {}).get("callUUID", ""),
        "variables": config.get("variables", {}),
        "voicemail_config": voicemail_config,
        "voicemail_action": (voicemail_config.get("action") or "").strip(),
        "voicemail_hangup_on_detect": is_voicemail_hangup_action(voicemail_config),
        "robot_code": config.get("code"),
        "robot_name": config.get("name"),
        "robot_version_id": (
            config.get("robotVersionId") or config.get("robot_version_id")
        ),
    }


def build_session_state(websocket_client, stream_sid, config_manager, extra=None) -> dict:
    """通话运行期状态 dict（各 bot 的 session_context 公共部分 + 各自扩展）。"""
    state = {
        "websocket_client": websocket_client,
        "stream_sid": stream_sid,
        "on_answer": False,
        "is_end": False,
        "call_start_time": time.time(),
        "config_manager": config_manager,
        "no_response_count": 0,
        "no_response_hangup_queued": False,
        "no_response_hangup_task": None,
        "voicemail_detected": False,
    }
    if extra:
        state.update(extra)
    return state
