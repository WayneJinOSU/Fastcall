"""机器人配置本地注册表：robot_code -> conf/robots/<robot_code>.json。

开源版约定：WebSocket 建联时只需传 robot_code，机器人能力配置
（ASR / TTS / LLM / prompt / 流程节点）由服务端按 robot_code 在本地解析。
后续接入配置管理系统时，只需替换 load_robot_config 的取数实现，
入口调用方（server/routes.py）不变。

配置示例见 conf/robots/example.json。
"""

import copy
import json
import logging
import re

from collection_agents.infra.paths import CONF_DIR

ROBOTS_DIR = CONF_DIR / "robots"

# robot_code 只允许字母/数字/下划线/中划线，防止路径穿越。
_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# code -> (mtime, config)。文件变更即生效，无需重启进程。
_cache: dict[str, tuple[float, dict]] = {}


def load_robot_config(robot_code) -> dict:
    """按 robot_code 加载机器人配置。找不到/格式错误时抛出清晰异常。"""
    code = str(robot_code or "").strip()
    if not _CODE_PATTERN.match(code):
        raise ValueError(f"非法 robot_code: {robot_code!r}")
    path = ROBOTS_DIR / f"{code}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"机器人配置不存在: {path}（请在 conf/robots/ 下创建 {code}.json，"
            "可参考 conf/robots/example.json）"
        )
    mtime = path.stat().st_mtime
    cached = _cache.get(code)
    if cached and cached[0] == mtime:
        return copy.deepcopy(cached[1])
    with open(path, "r", encoding="utf-8") as fh:
        config = json.load(fh)
    if not isinstance(config, dict):
        raise ValueError(f"机器人配置必须是 JSON 对象: {path}")
    config.setdefault("code", code)
    _cache[code] = (mtime, config)
    logging.info("机器人配置已加载: %s", path)
    return copy.deepcopy(config)


def apply_robot_config(data: dict) -> dict:
    """入口适配：消息未携带完整 config 时，按 robot_code 本地解析并注入。

    - 消息已带 ``config``（旧协议）-> 原样透传，bot 行为不变。
    - 消息只带 ``robot_code``（开源协议）-> 本地加载配置注入 ``data["config"]``；
      消息里的 ``variables``（每次通话的动态数据：客户姓名、金额等）会与
      配置中的默认 variables 合并，消息侧优先。
    """
    if not isinstance(data, dict):
        return data
    config = data.get("config")
    if isinstance(config, dict) and config:
        return data

    robot_code = data.get("robot_code") or data.get("robotCode")
    if not robot_code:
        raise ValueError("建联消息缺少 config，也未提供 robot_code")

    config = load_robot_config(robot_code)
    variables = data.get("variables")
    if isinstance(variables, dict) and variables:
        merged = config.get("variables")
        merged = merged if isinstance(merged, dict) else {}
        merged.update(variables)
        config["variables"] = merged
    data["config"] = config
    logging.info("按 robot_code=%s 注入本地机器人配置", robot_code)
    return data
