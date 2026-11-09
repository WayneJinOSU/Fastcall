"""bots 共享的纯工具函数集（无类、无继承，各 bot 显式调用）。

这里只放 standard / realtime / copilot 三个 bot 都会用到的通用逻辑：
- build_call_context: 从 call_data 提取一次通话的全部上下文字段
- build_session_state: 构造通话运行期状态 dict
"""

import logging
import time

from collection_agents.services.call_config import (
    BotV3ConfigManager,
    is_voicemail_hangup_action,
    resolve_task_type,
)
from collection_agents.text.time_utils import utc2tz


def build_call_context(call_data, *, platform: str) -> dict:
    """从 call_data 提取一次通话的全部上下文字段。

    standard 与 realtime 两个 bot 的入口曾各自重复这段约 50 行的提取逻辑；
    这里统一成一处。返回普通 dict，调用方按需取用。

    副作用：variables 会就地补 ``contact_time`` 和解析后的 ``task_type``
    （intelligent_notes_distribution 从 call_data 里读这两个值）。
    """
    config_manager = BotV3ConfigManager(call_data)
    config = call_data.get("data", {}).get("config", {})
    variables = config.get("variables", {})

    country = variables.get("country", "")
    voicemail_config = config_manager.get_voicemail_config()

    robot_name = config.get("name")
    task_type = resolve_task_type(variables.get("task_type", "collection"), robot_name)
    if task_type != variables.get("task_type", "collection"):
        logging.info(
            "Task type resolved from robot name: robot_name=%r task_type=%s",
            robot_name,
            task_type,
        )
    # intelligent_notes_distribution reads the value from call_data, not
    # the local variable, so keep both sources consistent.
    variables["task_type"] = task_type
    variables["contact_time"] = utc2tz(time.time(), country)

    return {
        "call_data": call_data,
        "config_manager": config_manager,
        "call_uuid": call_data.get("data", {}).get("callUUID", ""),
        "business_id": call_data.get("data", {}).get("businessID", ""),
        "variables": variables,
        "country": country,
        "task_type": task_type,
        "voicemail_config": voicemail_config,
        "voicemail_action": (voicemail_config.get("action") or "").strip(),
        "voicemail_hangup_on_detect": is_voicemail_hangup_action(voicemail_config),
        "follower_id": variables.get("follower_id", "待传入"),
        "dial_schedule": variables.get("dial_schedule", ["待传入"]),
        "customer_group": variables.get("customer_group", "待传入"),
        "has_sms": variables.get("has_sms", 0),
        "has_app_push": variables.get("has_app_push", 0),
        "tenant_id": variables.get("tenant_id", ""),
        "app_type": variables.get("app_type", ""),
        "work_order_id": variables.get("work_order_id", ""),
        "outbound_source": variables.get("outbound_source") or "hermes",
        "platform": platform,
        "is_round_robin": 1,
        "is_human_assist": 0,
        "robot_code": config.get("code"),
        "robot_name": robot_name,
        "org_code": config.get("orgCode"),
        "org_name": config.get("orgName"),
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
