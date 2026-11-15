import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
ENV_FILE = DEPLOY / ".env.compose"
TLS_DIR = DEPLOY / "runtime/tls"
PROVIDER_KEYS = (
    "DEEPGRAM_API_KEY", "CARTESIA_API_KEY", "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID", "OPENAI_BASE_URL", "OPENAI_API_KEY",
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


def ensure_tls():
    required = ("ca.crt", "ca.key", "server.crt", "server.key")
    if all((TLS_DIR / name).exists() for name in required):
        return
    TLS_DIR.mkdir(parents=True, exist_ok=True)
    TLS_DIR.chmod(0o700)
    openssl = ["openssl"]
    subprocess.run(openssl + [
        "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(TLS_DIR / "ca.key"), "-out", str(TLS_DIR / "ca.crt"),
        "-subj", "/CN=Fastcall Local CA", "-days", "3650",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(openssl + [
        "req", "-new", "-newkey", "rsa:2048", "-nodes",
        "-keyout", str(TLS_DIR / "server.key"), "-out", str(TLS_DIR / "server.csr"),
        "-subj", "/CN=fastcall.internal",
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (TLS_DIR / "server.ext").write_text(
        "subjectAltName=DNS:fastcall.internal,DNS:localhost,IP:127.0.0.1\n"
        "extendedKeyUsage=serverAuth\n"
    )
    subprocess.run(openssl + [
        "x509", "-req", "-in", str(TLS_DIR / "server.csr"),
        "-CA", str(TLS_DIR / "ca.crt"), "-CAkey", str(TLS_DIR / "ca.key"),
        "-CAcreateserial", "-out", str(TLS_DIR / "server.crt"),
        "-days", "825", "-extfile", str(TLS_DIR / "server.ext"),
    ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name in ("ca.key", "server.key"):
        (TLS_DIR / name).chmod(0o600)


def container_database_url(value):
    parsed = urlsplit(value)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return value
    credentials, _, _ = parsed.netloc.rpartition("@")
    netloc = f"{credentials}@host.docker.internal:{parsed.port or 3306}" if credentials else f"host.docker.internal:{parsed.port or 3306}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, ""))


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
        "FASTCALL_SIP_ENABLED": source.get("FASTCALL_SIP_ENABLED", "false"),
        "FASTCALL_MOCK_CALLS_ENABLED": source.get("FASTCALL_MOCK_CALLS_ENABLED", "true"),
        "FREESWITCH_SIP_GATEWAY": source.get("FREESWITCH_SIP_GATEWAY", ""),
        "FREESWITCH_EXTERNAL_IP": source.get("FREESWITCH_EXTERNAL_IP", ""),
        "FASTCALL_HTTPS_PORT": source.get("FASTCALL_HTTPS_PORT", "8443"),
        "FASTCALL_DEBIAN_MIRROR": source.get("FASTCALL_DEBIAN_MIRROR", "http://deb.debian.org/debian"),
        "FASTCALL_DEBIAN_SECURITY_MIRROR": source.get(
            "FASTCALL_DEBIAN_SECURITY_MIRROR", "http://deb.debian.org/debian-security"
        ),
        "FASTCALL_GLOBAL_MAX_CONCURRENT": source.get("FASTCALL_GLOBAL_MAX_CONCURRENT", "50"),
        "FASTCALL_GLOBAL_CALLS_PER_MINUTE": source.get("FASTCALL_GLOBAL_CALLS_PER_MINUTE", "60"),
        "FASTCALL_MOCK_CALLBACK_SECRET_REF": "mock",
    }
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
    webhook_secrets.setdefault("mock", secrets.token_urlsafe(32))
    result["FASTCALL_WEBHOOK_SECRETS_JSON"] = json.dumps(webhook_secrets, separators=(",", ":"))

    ensure_tls()
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

    print(f"Console: https://localhost:{result['FASTCALL_HTTPS_PORT']}/console/")
    print("The local TLS CA is deploy/runtime/tls/ca.crt; install it only if you want to trust the local Console certificate.")


if __name__ == "__main__":
    main()
