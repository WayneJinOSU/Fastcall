import base64
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
ENV_FILE = DEPLOY / ".env.compose"
TLS_DIR = DEPLOY / "runtime/tls"
PROVIDER_KEYS = (
    "DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "ELEVENLABS_API_KEY",
    "OPENAI_BASE_URL", "OPENAI_API_KEY",
    "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "AZURE_LLM_ENDPOINT",
    "AZURE_LLM_API_KEY", "AZURE_LLM_DEPLOYMENT", "AZURE_LLM_API_VERSION",
    "AZURE_REALTIME_ENDPOINT", "AZURE_REALTIME_MODEL",
    "AZURE_REALTIME_TRANSCRIPTION_MODEL", "AZURE_REALTIME_API_KEY",
    "VOSK_MODEL_PATH",
)


def read_env(path):
    values = {}
    if not path.exists():
        return values
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if path == ENV_FILE:
            value = value.replace("$$", "$")
        values[key.strip()] = value
    return values


def password_hash(password):
    iterations = 310000
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "$".join((
        "pbkdf2_sha256", str(iterations),
        base64.urlsafe_b64encode(salt).decode(),
        base64.urlsafe_b64encode(digest).decode(),
    ))


def run_openssl(arguments):
    subprocess.run(
        ["openssl", *arguments], check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def subject_alt_name(domain):
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
            raise RuntimeError("FREESWITCH_DOMAIN must be a DNS hostname or IP address")
        return f"DNS:{domain}"
    return f"IP:{domain}"


def wss_url(domain, port):
    host = f"[{domain}]" if ":" in domain else domain
    return f"wss://{host}:{port}"


def certificate_matches_domain(certificate_names, domain):
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        for name in certificate_names:
            if name == domain:
                return True
            if (
                name.startswith("*.")
                and domain.count(".") == name.count(".")
                and domain.endswith(name[1:])
            ):
                return True
        return False
    return domain in certificate_names


def certificate_text(path):
    return subprocess.check_output([
        "openssl", "x509", "-in", str(path), "-noout", "-text",
    ], text=True, stderr=subprocess.DEVNULL)


def validate_local_certificate_extensions(certificate, is_ca=False):
    if "Fastcall Local CA" not in certificate:
        return
    required = [
        "X509v3 Subject Key Identifier",
        "X509v3 Authority Key Identifier",
        "X509v3 Basic Constraints",
        "CA:TRUE" if is_ca else "CA:FALSE",
    ]
    if any(extension not in certificate for extension in required):
        raise RuntimeError(
            "Local certificates lack required X.509 extensions. Move deploy/runtime/tls "
            "aside and rerun bootstrap, then trust the new ca.crt on the SIP App"
        )


def validate_wss_certificate(domain):
    certificate_path = TLS_DIR / "wss.crt"
    unexpired = subprocess.run([
        "openssl", "x509", "-in", str(certificate_path), "-noout", "-checkend", "0",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if unexpired.returncode:
        raise RuntimeError("The WSS certificate is expired; replace wss.crt, wss.key and wss.pem")
    certificate = certificate_text(certificate_path)
    validate_local_certificate_extensions(certificate)
    subject_alt_name(domain)
    certificate_names = re.findall(r"(?:DNS|IP Address):([^,\s]+)", certificate)
    if not certificate_matches_domain(certificate_names, domain):
        raise RuntimeError(
            "The WSS certificate does not match FREESWITCH_DOMAIN; "
            "replace wss.crt, wss.key and wss.pem for the current domain"
        )
    try:
        certificate_public_key = subprocess.check_output([
            "openssl", "x509", "-in", str(certificate_path), "-noout", "-pubkey",
        ], stderr=subprocess.DEVNULL)
        private_public_key = subprocess.check_output([
            "openssl", "pkey", "-in", str(TLS_DIR / "wss.key"), "-pubout",
        ], stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError as error:
        raise RuntimeError("The WSS certificate or private key is invalid") from error
    if certificate_public_key != private_public_key:
        raise RuntimeError("The WSS certificate and private key do not match")


def write_wss_pem():
    (TLS_DIR / "wss.pem").write_bytes(
        (TLS_DIR / "wss.crt").read_bytes() + (TLS_DIR / "wss.key").read_bytes()
    )
    (TLS_DIR / "wss.key").chmod(0o600)
    (TLS_DIR / "wss.pem").chmod(0o600)


def ensure_tls(domain="fastcall.internal"):
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    TLS_DIR.chmod(0o700)
    console_required = ("ca.crt", "ca.key", "server.crt", "server.key")
    if not all((TLS_DIR / name).exists() for name in console_required):
        run_openssl([
            "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(TLS_DIR / "ca.key"), "-out", str(TLS_DIR / "ca.crt"),
            "-subj", "/CN=Fastcall Local CA", "-days", "3650",
            "-addext", "basicConstraints=critical,CA:TRUE",
            "-addext", "keyUsage=critical,keyCertSign,cRLSign",
            "-addext", "subjectKeyIdentifier=hash",
            "-addext", "authorityKeyIdentifier=keyid:always",
        ])
        run_openssl([
            "req", "-new", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(TLS_DIR / "server.key"), "-out", str(TLS_DIR / "server.csr"),
            "-subj", "/CN=fastcall.internal",
        ])
        (TLS_DIR / "server.ext").write_text(
            "subjectAltName=DNS:fastcall.internal,DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
            "subjectKeyIdentifier=hash\n"
            "authorityKeyIdentifier=keyid,issuer\n"
        )
        run_openssl([
            "x509", "-req", "-in", str(TLS_DIR / "server.csr"),
            "-CA", str(TLS_DIR / "ca.crt"), "-CAkey", str(TLS_DIR / "ca.key"),
            "-CAcreateserial", "-out", str(TLS_DIR / "server.crt"),
            "-days", "825", "-extfile", str(TLS_DIR / "server.ext"),
        ])
    for name in ("ca.key", "server.key"):
        (TLS_DIR / name).chmod(0o600)
    validate_local_certificate_extensions(
        certificate_text(TLS_DIR / "ca.crt"), is_ca=True
    )
    validate_local_certificate_extensions(certificate_text(TLS_DIR / "server.crt"))

    wss_required = ("wss.crt", "wss.key", "wss.pem")
    if all((TLS_DIR / name).exists() for name in wss_required):
        validate_wss_certificate(domain)
        write_wss_pem()
        return
    if any((TLS_DIR / name).exists() for name in wss_required):
        raise RuntimeError(
            "WSS certificate files are incomplete; provide wss.crt, wss.key and wss.pem together"
        )
    san = subject_alt_name(domain)
    run_openssl([
        "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(TLS_DIR / "wss.key"), "-out", str(TLS_DIR / "wss.csr"),
        "-subj", f"/CN={domain}",
    ])
    (TLS_DIR / "wss.ext").write_text(
        f"subjectAltName={san}\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectKeyIdentifier=hash\n"
        "authorityKeyIdentifier=keyid,issuer\n"
    )
    run_openssl([
        "x509", "-req", "-in", str(TLS_DIR / "wss.csr"),
        "-CA", str(TLS_DIR / "ca.crt"), "-CAkey", str(TLS_DIR / "ca.key"),
        "-CAcreateserial", "-out", str(TLS_DIR / "wss.crt"),
        "-days", "825", "-extfile", str(TLS_DIR / "wss.ext"),
    ])
    (TLS_DIR / "wss.crt").write_bytes(
        (TLS_DIR / "wss.crt").read_bytes() + (TLS_DIR / "ca.crt").read_bytes()
    )
    write_wss_pem()


def container_database_url(value):
    parsed = urlsplit(value)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return value
    credentials, _, _ = parsed.netloc.rpartition("@")
    netloc = f"{credentials}@host.docker.internal:{parsed.port or 3306}" if credentials else f"host.docker.internal:{parsed.port or 3306}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


def sip_settings(source):
    enabled_value = source.get("FASTCALL_SIP_ENABLED", "true").strip().lower()
    if enabled_value not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise RuntimeError("FASTCALL_SIP_ENABLED must be a boolean")
    sip_enabled = enabled_value in {"1", "true", "yes", "on"}
    if not sip_enabled:
        raise RuntimeError("SIP must be enabled")
    external_ip = source.get("FREESWITCH_EXTERNAL_IP", "").strip()
    if not external_ip:
        raise RuntimeError("FREESWITCH_EXTERNAL_IP is required when SIP is enabled")
    domain = source.get("FREESWITCH_DOMAIN", "").strip() or external_ip or "fastcall.internal"
    subject_alt_name(domain)
    local_user = source.get("FREESWITCH_LOCAL_USER", "").strip() or "8615908280801"
    if not re.fullmatch(r"[0-9]{6,15}", local_user):
        raise RuntimeError("FREESWITCH_LOCAL_USER must contain 6 to 15 digits")
    local_password = source.get("FREESWITCH_LOCAL_PASSWORD", "") or secrets.token_urlsafe(12)
    if len(local_password) < 12:
        raise RuntimeError("FREESWITCH_LOCAL_PASSWORD must contain at least 12 characters")
    wss_port = int(source.get("FREESWITCH_WSS_PORT", "7443"))
    if not 1 <= wss_port <= 65535:
        raise RuntimeError("FREESWITCH_WSS_PORT must be a valid TCP port")
    gateway = source.get("FREESWITCH_SIP_GATEWAY", "").strip()
    gateway_host = source.get("FREESWITCH_SIP_HOST", "").strip()
    if bool(gateway) != bool(gateway_host):
        raise RuntimeError(
            "FREESWITCH_SIP_GATEWAY and FREESWITCH_SIP_HOST must be set together"
        )
    return {
        "FASTCALL_SIP_ENABLED": str(sip_enabled).lower(),
        "FREESWITCH_EXTERNAL_IP": external_ip,
        "FREESWITCH_DOMAIN": domain,
        "FREESWITCH_LOCAL_USER": local_user,
        "FREESWITCH_LOCAL_PASSWORD": local_password,
        "FREESWITCH_WSS_PORT": str(wss_port),
    }


def main():
    source = read_env(ROOT / ".env")
    source.update(read_env(ENV_FILE))
    source.update(os.environ)

    fastcall_db = source.get("FASTCALL_DATABASE_URL", "")
    agent_db = source.get("AGENT_DATABASE_URL", "")
    if not fastcall_db.startswith("mysql+pymysql://") or not agent_db.startswith("mysql+pymysql://"):
        raise RuntimeError(
            "Set FASTCALL_DATABASE_URL and AGENT_DATABASE_URL to separate mysql+pymysql URLs before bootstrap"
        )
    fastcall_name = urlsplit(fastcall_db).path.lstrip("/")
    agent_name = urlsplit(agent_db).path.lstrip("/")
    if not fastcall_name or not agent_name or fastcall_name == agent_name:
        raise RuntimeError("Fast-Call and Agent must use different databases")
    fastcall_db = container_database_url(fastcall_db)
    agent_db = container_database_url(agent_db)

    external_agent = source.get("FASTCALL_AGENT_EXTERNAL", "").lower() in {"1", "true", "yes", "on"}
    result = {
        "APP_ENV": source.get("APP_ENV", "prod"),
        "MODE": source.get("MODE", "prod"),
        "FASTCALL_DATABASE_URL": fastcall_db,
        "AGENT_DATABASE_URL": agent_db,
        "FASTCALL_IMAGE": source.get("FASTCALL_IMAGE", "fastcall:dev"),
        "FASTCALL_ADAPTER_IMAGE": source.get("FASTCALL_ADAPTER_IMAGE", "fastcall-adapter:dev"),
        "FREESWITCH_IMAGE": source.get("FREESWITCH_IMAGE", "fastcall-freeswitch:dev"),
        "COLLECTION_AGENT_IMAGE": source.get("COLLECTION_AGENT_IMAGE", "collection-agent:stable"),
        "FASTCALL_AGENT_EXTERNAL": str(external_agent).lower(),
        "COLLECTION_AGENT_WS_URL": (
            "ws://host.docker.internal:8765/ws-v2" if external_agent else "ws://agent:8765/ws-v2"
        ),
        "COLLECTION_AGENT_HTTP_URL": (
            "http://host.docker.internal:8765" if external_agent else "http://agent:8765"
        ),
        "FREESWITCH_SIP_GATEWAY": source.get("FREESWITCH_SIP_GATEWAY", ""),
        "FASTCALL_HTTPS_PORT": source.get("FASTCALL_HTTPS_PORT", "8443"),
        "FASTCALL_HTTP_PORT": source.get("FASTCALL_HTTP_PORT", "8080"),
        "FASTCALL_DEBIAN_MIRROR": source.get("FASTCALL_DEBIAN_MIRROR", "http://deb.debian.org/debian"),
        "FASTCALL_DEBIAN_SECURITY_MIRROR": source.get(
            "FASTCALL_DEBIAN_SECURITY_MIRROR", "http://deb.debian.org/debian-security"
        ),
        "FASTCALL_GLOBAL_MAX_CONCURRENT": source.get("FASTCALL_GLOBAL_MAX_CONCURRENT", "50"),
        "FASTCALL_GLOBAL_CALLS_PER_MINUTE": source.get("FASTCALL_GLOBAL_CALLS_PER_MINUTE", "60"),
    }
    generated_sip_password = not source.get("FREESWITCH_LOCAL_PASSWORD")
    result.update(sip_settings(source))
    for key in PROVIDER_KEYS:
        if source.get(key):
            result[key] = source[key]
    for key in (
        "FREESWITCH_SIP_HOST", "FREESWITCH_SIP_PORT", "FREESWITCH_SIP_REGISTER",
        "FREESWITCH_SIP_USERNAME", "FREESWITCH_SIP_PASSWORD", "FREESWITCH_SIP_FROM_USER",
        "FREESWITCH_SIP_CALLER_ID_IN_FROM", "FREESWITCH_SIP_TRANSPORT",
        "FREESWITCH_LOCAL_SIP_PORT", "FREESWITCH_RTP_START_PORT", "FREESWITCH_RTP_END_PORT",
    ):
        if source.get(key):
            result[key] = source[key]
    for key in (
        "FASTCALL_MANAGEMENT_TOKEN", "FASTCALL_RESOLVE_TOKEN", "FASTCALL_OPENAPI_TOKEN",
        "FASTCALL_ADMIN_TOKEN", "FASTCALL_ADAPTER_TOKEN", "FASTCALL_AGENT_TOKEN",
        "FASTCALL_MEDIA_TOKEN",
    ):
        result[key] = source.get(key) or secrets.token_urlsafe(32)
    result["FREESWITCH_ESL_PASSWORD"] = source.get("FREESWITCH_ESL_PASSWORD") or secrets.token_urlsafe(32)
    result["FASTCALL_CONSOLE_ADMIN_ACTOR_ID"] = source.get("FASTCALL_CONSOLE_ADMIN_ACTOR_ID", "admin")
    result["FASTCALL_CONSOLE_SESSION_SECRET"] = source.get("FASTCALL_CONSOLE_SESSION_SECRET") or secrets.token_urlsafe(48)
    result["FASTCALL_ENCRYPTION_KEYS_JSON"] = source.get("FASTCALL_ENCRYPTION_KEYS_JSON") or json.dumps(
        {"v1": base64.b64encode(secrets.token_bytes(32)).decode()}, separators=(",", ":")
    )
    result["FASTCALL_HMAC_KEYS_JSON"] = source.get("FASTCALL_HMAC_KEYS_JSON") or json.dumps(
        {"v1": base64.b64encode(secrets.token_bytes(32)).decode()}, separators=(",", ":")
    )

    generated_password = None
    if source.get("FASTCALL_CONSOLE_ADMIN_PASSWORD"):
        result["FASTCALL_CONSOLE_ADMIN_PASSWORD_HASH"] = password_hash(
            source["FASTCALL_CONSOLE_ADMIN_PASSWORD"]
        )
    elif source.get("FASTCALL_CONSOLE_ADMIN_PASSWORD_HASH"):
        result["FASTCALL_CONSOLE_ADMIN_PASSWORD_HASH"] = source["FASTCALL_CONSOLE_ADMIN_PASSWORD_HASH"]
    else:
        generated_password = secrets.token_urlsafe(18)
        result["FASTCALL_CONSOLE_ADMIN_PASSWORD_HASH"] = password_hash(generated_password)
    webhook_secrets = json.loads(source.get("FASTCALL_WEBHOOK_SECRETS_JSON", "{}"))
    result["FASTCALL_WEBHOOK_SECRETS_JSON"] = json.dumps(webhook_secrets, separators=(",", ":"))

    ensure_tls(result["FREESWITCH_DOMAIN"])
    lines = []
    for key in sorted(result):
        value = str(result[key])
        if "\n" in value or "\r" in value:
            raise RuntimeError(f"{key} cannot contain newlines")
        lines.append(f"{key}={value.replace('$', '$$')}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)
    print(
        "MySQL targets: "
        f"Fast-Call={urlsplit(fastcall_db).hostname}/{fastcall_name}, "
        f"Agent={urlsplit(agent_db).hostname}/{agent_name}",
        flush=True,
    )

    command = [
        "docker", "compose", "-p", "fastcall", "-f", "deploy/compose.yaml",
        "--env-file", "deploy/.env.compose",
    ]
    if not external_agent:
        command.extend(["--profile", "internal-agent"])
    command.extend([
        "up", "-d", "--build", "--wait", "--wait-timeout", "180",
    ])
    if generated_password:
        print(f"Generated Console password for {result['FASTCALL_CONSOLE_ADMIN_ACTOR_ID']}: {generated_password}", flush=True)
    subprocess.run(command, cwd=ROOT, check=True)

    print(f"Console (local): http://localhost:{result['FASTCALL_HTTP_PORT']}/console/")
    print(f"Console (HTTPS): https://fastcall.internal:{result['FASTCALL_HTTPS_PORT']}/console/")
    print("For HTTPS, map fastcall.internal to 127.0.0.1 in your hosts file.")
    print("The local TLS CA is deploy/runtime/tls/ca.crt; install it only if you want to trust the local Console certificate.")
    if result["FASTCALL_SIP_ENABLED"] == "true":
        print(f"SIP username: {result['FREESWITCH_LOCAL_USER']}")
        if generated_sip_password:
            print(f"Generated SIP password: {result['FREESWITCH_LOCAL_PASSWORD']}")
        else:
            print("SIP password: reuse FREESWITCH_LOCAL_PASSWORD from deploy/.env.compose")
        print(f"SIP domain: {result['FREESWITCH_DOMAIN']}")
        print(
            "SIP WSS URL: "
            + wss_url(result["FREESWITCH_DOMAIN"], result["FREESWITCH_WSS_PORT"])
        )
        print(f"Contact phone: +{result['FREESWITCH_LOCAL_USER']}")
        print(
            "For the generated self-signed WSS certificate, the SIP App must trust "
            "deploy/runtime/tls/ca.crt. A publicly trusted certificate needs no client CA install."
        )


if __name__ == "__main__":
    main()
