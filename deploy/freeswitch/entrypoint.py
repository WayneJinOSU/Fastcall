import ipaddress
import os
from pathlib import Path
import re
import secrets
import sys
from xml.sax.saxutils import escape as xml_escape


def escape(value):
    return xml_escape(str(value), {'"': "&quot;"})

CONF_DIR = Path("/usr/local/freeswitch/etc/freeswitch")
TEMPLATE_DIR = Path("/usr/local/share/fastcall-freeswitch/templates")


def env_bool(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean")


def render(name, values):
    content = (TEMPLATE_DIR / f"{name}.template").read_text()
    for key, value in values.items():
        content = content.replace("{{" + key + "}}", escape(str(value)))
    return content


def write_rendered(name, values, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(name, values))
    destination.chmod(0o600)


def port(name, default):
    value = int(os.getenv(name, str(default)))
    if not 1 <= value <= 65535:
        raise RuntimeError(f"{name} must be a valid TCP/UDP port")
    return value


def main():
    sip_enabled = env_bool("FASTCALL_SIP_ENABLED", False)
    mock_enabled = env_bool("FASTCALL_MOCK_CALLS_ENABLED", True)
    if not sip_enabled and not mock_enabled:
        raise RuntimeError("At least one of SIP or Mock Call must be enabled")

    esl_password = os.getenv("FREESWITCH_ESL_PASSWORD", "")
    if len(esl_password) < 16 or esl_password == "ClueCon":
        raise RuntimeError("FREESWITCH_ESL_PASSWORD must be a non-default value of at least 16 characters")
    esl_cidr = str(ipaddress.ip_network(os.getenv("FREESWITCH_ESL_ALLOWED_CIDR", "127.0.0.1/32")))

    rtp_start = port("FREESWITCH_RTP_START_PORT", 16384)
    rtp_end = port("FREESWITCH_RTP_END_PORT", 16483)
    if rtp_end < rtp_start:
        raise RuntimeError("FREESWITCH_RTP_END_PORT must not be smaller than FREESWITCH_RTP_START_PORT")

    write_rendered("event_socket.conf.xml", {
        "ESL_PASSWORD": esl_password,
        "ESL_ACL": "fastcall_esl",
    }, CONF_DIR / "autoload_configs/event_socket.conf.xml")
    write_rendered("acl.conf.xml", {"ESL_CIDR": esl_cidr}, CONF_DIR / "autoload_configs/acl.conf.xml")
    write_rendered("switch.conf.xml", {
        "RTP_START_PORT": rtp_start,
        "RTP_END_PORT": rtp_end,
    }, CONF_DIR / "autoload_configs/switch.conf.xml")

    external_ip = os.getenv("FREESWITCH_EXTERNAL_IP", "").strip()
    if sip_enabled:
        if not external_ip:
            raise RuntimeError("FREESWITCH_EXTERNAL_IP is required when SIP is enabled")
        ipaddress.ip_address(external_ip)
    else:
        external_ip = "$${local_ip_v4}"
    write_rendered("external.xml", {
        "LOCAL_SIP_PORT": port("FREESWITCH_LOCAL_SIP_PORT", 5080),
        "EXTERNAL_IP": external_ip,
    }, CONF_DIR / "sip_profiles/external.xml")

    gateway_dir = CONF_DIR / "sip_profiles/external"
    gateway_dir.mkdir(parents=True, exist_ok=True)
    for old_gateway in gateway_dir.glob("*.xml"):
        old_gateway.unlink()
    if sip_enabled:
        gateway_name = os.getenv("FREESWITCH_SIP_GATEWAY", "fastcall").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]+", gateway_name):
            raise RuntimeError("FREESWITCH_SIP_GATEWAY contains invalid characters")
        sip_host = os.getenv("FREESWITCH_SIP_HOST", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9.-]+", sip_host):
            raise RuntimeError("FREESWITCH_SIP_HOST must be a DNS hostname or IPv4 address")
        register = env_bool("FREESWITCH_SIP_REGISTER", True)
        username = os.getenv("FREESWITCH_SIP_USERNAME", "")
        password = os.getenv("FREESWITCH_SIP_PASSWORD", "")
        if register and (not username or not password):
            raise RuntimeError("SIP username and password are required for a registering gateway")
        transport = os.getenv("FREESWITCH_SIP_TRANSPORT", "udp").strip().lower()
        if transport not in {"udp", "tcp"}:
            raise RuntimeError("FREESWITCH_SIP_TRANSPORT must be udp or tcp")
        from_user = os.getenv("FREESWITCH_SIP_FROM_USER", "").strip() or (username if register else "")
        from_user_param = ""
        if from_user:
            from_user_param = f'  <param name="from-user" value="{escape(from_user)}"/>'
        credentials = ""
        if register:
            credentials = (
                f'  <param name="username" value="{escape(username)}"/>\n'
                f'  <param name="password" value="{escape(password)}"/>'
            )
        values = {
            "GATEWAY_NAME": gateway_name,
            "SIP_HOST": sip_host,
            "SIP_PORT": port("FREESWITCH_SIP_PORT", 5060),
            "SIP_CALLER_ID_IN_FROM": str(env_bool("FREESWITCH_SIP_CALLER_ID_IN_FROM", False)).lower(),
            "SIP_REGISTER": str(register).lower(),
            "SIP_TRANSPORT": transport,
        }
        content = render("gateway.xml", values).replace(
            "{{SIP_FROM_USER}}", from_user_param
        ).replace("{{SIP_CREDENTIALS}}", credentials)
        gateway_path = gateway_dir / f"{gateway_name}.xml"
        gateway_path.write_text(content)
        gateway_path.chmod(0o600)

    mock_dialplan = CONF_DIR / "dialplan/default/00_mock_call.xml"
    if not mock_enabled and mock_dialplan.exists():
        mock_dialplan.unlink()

    vars_path = CONF_DIR / "vars.xml"
    vars_path.write_text(
        vars_path.read_text().replace("{{DEFAULT_PASSWORD}}", escape(secrets.token_urlsafe(32)))
    )
    vars_path.chmod(0o600)

    print(f"Fastcall FreeSWITCH configured: sip={str(sip_enabled).lower()} mock={str(mock_enabled).lower()}", flush=True)
    if len(sys.argv) < 2:
        raise RuntimeError("A FreeSWITCH command is required")
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
