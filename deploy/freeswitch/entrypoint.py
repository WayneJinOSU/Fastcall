import ipaddress
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import sys
from xml.sax.saxutils import escape as xml_escape


def escape(value):
    return xml_escape(str(value), {'"': "&quot;"})

CONF_DIR = Path("/usr/local/freeswitch/etc/freeswitch")
LOG_DIR = Path("/usr/local/freeswitch/log")
DB_DIR = Path("/usr/local/freeswitch/db")
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


def configure_local_user():
    local_user = os.getenv("FREESWITCH_LOCAL_USER", "8615908280801").strip()
    local_password = os.getenv("FREESWITCH_LOCAL_PASSWORD", "")
    if not re.fullmatch(r"[0-9]{6,15}", local_user):
        raise RuntimeError("FREESWITCH_LOCAL_USER must contain 6 to 15 digits")
    if len(local_password) < 12:
        raise RuntimeError("FREESWITCH_LOCAL_PASSWORD must contain at least 12 characters")
    write_rendered("local_user.xml", {
        "LOCAL_USER": local_user,
        "LOCAL_PASSWORD": local_password,
    }, CONF_DIR / "directory/default/fastcall-local-user.xml")


def validate_gateway_settings(gateway_name, sip_host):
    if bool(gateway_name) != bool(sip_host):
        raise RuntimeError(
            "FREESWITCH_SIP_GATEWAY and FREESWITCH_SIP_HOST must be set together"
        )


def prepare_tls_certificates(source_dir=Path("/usr/local/freeswitch/certs")):
    destination_dir = CONF_DIR / "certs"
    destination_dir.mkdir(parents=True, exist_ok=True)
    for name in ("wss.crt", "wss.key", "wss.pem"):
        source = source_dir / name
        if not source.is_file():
            raise RuntimeError(f"Missing WSS certificate file: {source}")
        destination = destination_dir / name
        shutil.copyfile(source, destination)
        destination.chmod(0o600 if name in {"wss.key", "wss.pem"} else 0o644)


def drop_privileges():
    if os.getuid() != 0:
        return
    account = pwd.getpwnam("freeswitch")
    for runtime_directory in (CONF_DIR, LOG_DIR, DB_DIR):
        runtime_directory.mkdir(parents=True, exist_ok=True)
        for root, directories, files in os.walk(runtime_directory):
            os.chown(root, account.pw_uid, account.pw_gid, follow_symlinks=False)
            for name in directories + files:
                os.chown(
                    Path(root) / name, account.pw_uid, account.pw_gid,
                    follow_symlinks=False,
                )
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)


def main():
    sip_enabled = env_bool("FASTCALL_SIP_ENABLED", False)
    if not sip_enabled:
        raise RuntimeError("SIP must be enabled")

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
        configure_local_user()
    else:
        external_ip = "$${local_ip_v4}"
    prepare_tls_certificates()
    write_rendered("external.xml", {
        "LOCAL_SIP_PORT": port("FREESWITCH_LOCAL_SIP_PORT", 5080),
        "EXTERNAL_IP": external_ip,
    }, CONF_DIR / "sip_profiles/external.xml")

    gateway_dir = CONF_DIR / "sip_profiles/external"
    gateway_dir.mkdir(parents=True, exist_ok=True)
    for old_gateway in gateway_dir.glob("*.xml"):
        old_gateway.unlink()
    gateway_name = os.getenv("FREESWITCH_SIP_GATEWAY", "").strip()
    sip_host = os.getenv("FREESWITCH_SIP_HOST", "").strip()
    if sip_enabled:
        validate_gateway_settings(gateway_name, sip_host)
    if sip_enabled and gateway_name and sip_host:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", gateway_name):
            raise RuntimeError("FREESWITCH_SIP_GATEWAY contains invalid characters")
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

    legacy_mock_dialplan = CONF_DIR / "dialplan/default/00_mock_call.xml"
    if legacy_mock_dialplan.exists():
        legacy_mock_dialplan.unlink()

    vars_path = CONF_DIR / "vars.xml"
    freeswitch_domain = os.getenv("FREESWITCH_DOMAIN", "").strip() or "$${local_ip_v4}"
    vars_path.write_text(
        vars_path.read_text()
        .replace("{{DEFAULT_PASSWORD}}", escape(secrets.token_urlsafe(32)))
        .replace("{{FREESWITCH_DOMAIN}}", escape(freeswitch_domain))
    )
    vars_path.chmod(0o600)

    print(f"Fastcall FreeSWITCH configured: sip={str(sip_enabled).lower()}", flush=True)
    if len(sys.argv) < 2:
        raise RuntimeError("A FreeSWITCH command is required")
    drop_privileges()
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
