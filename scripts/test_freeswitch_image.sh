#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${FASTCALL_FREESWITCH_IMAGE:-fastcall-freeswitch:dev}"
DEBIAN_IMAGE="${FASTCALL_DEBIAN_IMAGE:-debian:bookworm-slim@sha256:734a72b3d6142a71993b4bc580ca01d8ca687f77a2c422d96a961d3376b179bc}"
CONTAINER="fastcall-fs-image-smoke-$$"
LOG_VOLUME="fastcall-smoke-log-$$"
DB_VOLUME="fastcall-smoke-db-$$"
PASSWORD="$(openssl rand -base64 24)\""
TLS_DIR=""

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
cleanup_all() {
    cleanup
    docker volume rm -f "$LOG_VOLUME" "$DB_VOLUME" >/dev/null 2>&1 || true
    if [[ -n "$TLS_DIR" ]]; then
        rm -rf "$TLS_DIR"
    fi
}
trap cleanup_all EXIT

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

assert_contains() {
    local value="$1"
    local expected="$2"
    case "$value" in
        *"$expected"*) ;;
        *) fail "expected output to contain: $expected" ;;
    esac
}

container_arguments() {
    CONTAINER_ARGS=(
        -e FREESWITCH_ESL_PASSWORD="$PASSWORD"
        -e FREESWITCH_ESL_ALLOWED_CIDR=127.0.0.1/32
        -e FREESWITCH_LOCAL_PASSWORD=smoke-local-pass
        -e FREESWITCH_DIAL_MODE=gateway
        -v "$TLS_DIR/ca.crt:/run/fastcall-wss-certs/ca.crt:ro"
        -v "$TLS_DIR/wss.crt:/run/fastcall-wss-certs/wss.crt:ro"
        -v "$TLS_DIR/wss.key:/run/fastcall-wss-certs/wss.key:ro"
        -v "$TLS_DIR/wss.pem:/run/fastcall-wss-certs/wss.pem:ro"
    )
}

start_container() {
    cleanup
    container_arguments
    docker run -d --name "$CONTAINER" \
        "${CONTAINER_ARGS[@]}" \
        -v "$LOG_VOLUME:/var/log/freeswitch" \
        -v "$DB_VOLUME:/var/lib/freeswitch" \
        "$@" "$IMAGE" >/dev/null
    for _ in {1..45}; do
        if docker exec "$CONTAINER" /usr/bin/fs_cli -p "$PASSWORD" -x status >/dev/null 2>&1; then
            return
        fi
        if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" != "true" ]]; then
            docker logs "$CONTAINER" >&2
            fail "FreeSWITCH exited during startup"
        fi
        sleep 2
    done
    docker logs "$CONTAINER" >&2
    fail "FreeSWITCH did not become ready"
}

assert_start_fails() {
    local label="$1"
    local expected="$2"
    shift 2
    cleanup
    container_arguments
    docker run -d --name "$CONTAINER" \
        "${CONTAINER_ARGS[@]}" "$@" "$IMAGE" >/dev/null
    for _ in {1..10}; do
        if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER")" != "true" ]]; then
            if [[ "$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER")" == "0" ]]; then
                fail "$label exited successfully"
            fi
            local output
            output="$(docker logs "$CONTAINER" 2>&1)"
            assert_contains "$output" "$expected"
            cleanup
            echo "PASS: rejected $label"
            return
        fi
        sleep 1
    done
    fail "$label did not fail within 10 seconds"
}

fs_cli() {
    docker exec "$CONTAINER" /usr/bin/fs_cli -p "$PASSWORD" -x "$1"
}

check_modules() {
    for module in mod_sofia mod_audio_stream; do
        [[ "$(fs_cli "module_exists $module")" == "true" ]] || fail "$module is not loaded"
    done
    assert_contains "$(fs_cli 'show api uuid_audio_stream')" "mod_audio_stream"
    assert_contains "$(fs_cli 'sofia status profile external')" "BIND-URL"
    assert_contains "$(fs_cli 'show codecs')" "G.711 alaw"
    assert_contains "$(fs_cli 'show codecs')" "G.711 ulaw"
    if docker exec "$CONTAINER" /usr/bin/fs_cli -p ClueCon -x status >/dev/null 2>&1; then
        fail "the default ESL password was accepted"
    fi
    docker exec -u freeswitch "$CONTAINER" test -w /var/log/freeswitch/upgrade-check
    docker exec -u freeswitch "$CONTAINER" test -w /var/lib/freeswitch/upgrade-check
    docker exec -u freeswitch "$CONTAINER" test -w /run/freeswitch
    docker exec "$CONTAINER" test -x /usr/bin/freeswitch
    docker exec "$CONTAINER" test -x /usr/bin/fs_cli
    docker exec "$CONTAINER" test -d /usr/lib/freeswitch/mod
    if docker exec "$CONTAINER" sh -c \
        'ldd /usr/bin/freeswitch /usr/lib/freeswitch/mod/mod_audio_stream.so | grep -q "not found"'; then
        fail "a runtime library is missing"
    fi
    local manifest
    manifest="$(docker exec "$CONTAINER" cat /usr/local/share/fastcall-freeswitch/build-manifest.txt)"
    assert_contains "$manifest" "freeswitch=1.11.4-release-44405182291-795228ad43~bookworm~arm64-1~bookworm+1"
    assert_contains "$manifest" "mod_audio_stream_commit=a25fb1fe530ec6a612d321ff04f70be69b1a257c"
}

check_wss() {
    assert_contains "$(fs_cli 'sofia status profile external')" "WSS-BIND-URL"
    docker exec -u freeswitch "$CONTAINER" test -r /etc/freeswitch/certs/wss.pem
    docker exec -u freeswitch "$CONTAINER" python3 -c '
import socket
import ssl
context = ssl.create_default_context(cafile="/run/fastcall-wss-certs/ca.crt")
address = socket.gethostbyname(socket.gethostname())
with socket.create_connection((address, 7443), timeout=5) as connection:
    with context.wrap_socket(connection, server_hostname="127.0.0.1") as tls:
        assert tls.version()
        assert tls.getpeercert()
'
    check_wss_registration
    assert_contains "$(fs_cli 'sofia status profile external reg 8615908280801')" "Auth-User"
}

check_wss_registration() {
    docker exec -u freeswitch "$CONTAINER" python3 -c '
import base64
import hashlib
import os
import re
import secrets
import socket
import ssl
import struct

DOMAIN = "127.0.0.1"
USER = "8615908280801"


def digest(value):
    return hashlib.md5(value.encode()).hexdigest()


def send_frame(connection, opcode, payload):
    mask = os.urandom(4)
    length = len(payload)
    header = bytearray([0x80 | opcode])
    if length < 126:
        header.append(0x80 | length)
    elif length <= 65535:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", length))
    masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    connection.sendall(header + mask + masked)


def read_exact(stream, length):
    data = b""
    while len(data) < length:
        chunk = stream.read(length - len(data))
        if not chunk:
            raise AssertionError("WebSocket closed unexpectedly")
        data += chunk
    return data


def read_message(stream, connection):
    while True:
        first, second = read_exact(stream, 2)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            length = struct.unpack("!H", read_exact(stream, 2))[0]
        elif length == 127:
            length = struct.unpack("!Q", read_exact(stream, 8))[0]
        mask = read_exact(stream, 4) if second & 0x80 else None
        payload = read_exact(stream, length)
        if mask:
            payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        if opcode == 9:
            send_frame(connection, 10, payload)
        elif opcode == 8:
            raise AssertionError("WebSocket closed before a SIP response")
        elif opcode == 1:
            return payload.decode()


def status_code(message):
    return int(message.split(" ", 2)[1])


def register(password):
    context = ssl.create_default_context(cafile="/run/fastcall-wss-certs/ca.crt")
    address = socket.gethostbyname(socket.gethostname())
    with socket.create_connection((address, 7443), timeout=5) as connection:
        with context.wrap_socket(connection, server_hostname=DOMAIN) as tls:
            stream = tls.makefile("rb")
            key = base64.b64encode(os.urandom(16)).decode()
            request = (
                "GET / HTTP/1.1\r\n"
                f"Host: {DOMAIN}:7443\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Protocol: sip\r\n"
                "Sec-WebSocket-Version: 13\r\n\r\n"
            )
            tls.sendall(request.encode())
            response = b""
            while not response.endswith(b"\r\n\r\n"):
                response += read_exact(stream, 1)
            assert response.startswith(b"HTTP/1.1 101"), response

            call_id = secrets.token_hex(8)
            from_tag = secrets.token_hex(6)
            uri = f"sip:{DOMAIN}"
            common = (
                f"From: <sip:{USER}@{DOMAIN}>;tag={from_tag}\r\n"
                f"To: <sip:{USER}@{DOMAIN}>\r\n"
                f"Call-ID: {call_id}\r\n"
                f"Contact: <sip:{USER}@{DOMAIN};transport=wss>\r\n"
                "Max-Forwards: 70\r\n"
                "Expires: 60\r\n"
            )

            def request_register(cseq, authorization=""):
                message = (
                    f"REGISTER {uri} SIP/2.0\r\n"
                    f"Via: SIP/2.0/WSS {DOMAIN};branch=z9hG4bK{secrets.token_hex(6)}\r\n"
                    f"{common}"
                    f"CSeq: {cseq} REGISTER\r\n"
                    f"{authorization}"
                    "Content-Length: 0\r\n\r\n"
                )
                send_frame(tls, 1, message.encode())
                return read_message(stream, tls)

            first = request_register(1)
            first_status = status_code(first)
            if first_status == 200:
                return 200
            assert first_status in {401, 407}, first
            headers = {}
            for line in first.split("\r\n")[1:]:
                name, separator, value = line.partition(":")
                if separator:
                    headers[name.lower()] = value.strip()
            authenticate = headers.get("www-authenticate", headers.get("proxy-authenticate", ""))
            assert authenticate.startswith("Digest "), first
            parameters = {
                match.group(1): match.group(2) if match.group(2) is not None else match.group(3)
                for match in re.finditer(r"(\w+)=(?:\"([^\"]*)\"|([^,\s]+))", authenticate)
            }
            nonce = parameters["nonce"]
            realm = parameters.get("realm", DOMAIN)
            qop = parameters.get("qop", "").split(",")[0]
            ha1 = digest(f"{USER}:{realm}:{password}")
            ha2 = digest(f"REGISTER:{uri}")
            cnonce = secrets.token_hex(8)
            if qop:
                response_digest = digest(f"{ha1}:{nonce}:00000001:{cnonce}:{qop}:{ha2}")
            else:
                response_digest = digest(f"{ha1}:{nonce}:{ha2}")
            authorization = (
                f"Authorization: Digest username=\"{USER}\", realm=\"{realm}\", "
                f"nonce=\"{nonce}\", uri=\"{uri}\", response=\"{response_digest}\", "
                "algorithm=MD5"
            )
            if qop:
                authorization += f", qop={qop}, nc=00000001, cnonce=\"{cnonce}\""
            authorization += "\r\n"
            return status_code(request_register(2, authorization))


assert register("smoke-local-pass") == 200
assert register("wrong-test-password") in {401, 403}
'
}

cd "$ROOT_DIR"
if [[ -z "${SIGNALWIRE_TOKEN:-}" ]]; then
    fail "SIGNALWIRE_TOKEN is required as a build secret; it is never stored in Compose service environment"
fi
if [[ -x "$ROOT_DIR/venv/bin/python" ]]; then
    PYTHON="$ROOT_DIR/venv/bin/python"
else
    PYTHON="${PYTHON:-python3}"
fi
if grep -Eq 'PCRE_VERSION|SPANDSP_COMMIT|SOFIA_SIP_COMMIT|FREESWITCH_SHA256|git apply' \
    deploy/freeswitch/Dockerfile; then
    fail "the image contains a removed source build or patch path"
fi
mkdir -p "$ROOT_DIR/deploy/runtime"
TLS_DIR="$(mktemp -d "$ROOT_DIR/deploy/runtime/wss-smoke.XXXXXX")"
"$PYTHON" -c '
import sys
from pathlib import Path
from scripts import bootstrap_compose
bootstrap_compose.TLS_DIR = Path(sys.argv[1])
bootstrap_compose.ensure_tls("127.0.0.1")
' "$TLS_DIR"
docker build \
    --secret id=signalwire_token,env=SIGNALWIRE_TOKEN \
    --build-arg DEBIAN_IMAGE="$DEBIAN_IMAGE" \
    --build-arg DEBIAN_MIRROR="${FASTCALL_DEBIAN_MIRROR:-http://mirrors.aliyun.com/debian}" \
    --build-arg DEBIAN_SECURITY_MIRROR="${FASTCALL_DEBIAN_SECURITY_MIRROR:-http://mirrors.aliyun.com/debian-security}" \
    -f deploy/freeswitch/Dockerfile -t "$IMAGE" .
docker volume create "$LOG_VOLUME" >/dev/null
docker volume create "$DB_VOLUME" >/dev/null
docker run --rm --user root \
    -v "$LOG_VOLUME:/var/log/freeswitch" \
    -v "$DB_VOLUME:/var/lib/freeswitch" \
    --entrypoint python3 "$IMAGE" -c '
from pathlib import Path
Path("/var/log/freeswitch/upgrade-check").write_text("root owned")
Path("/var/lib/freeswitch/upgrade-check").write_text("root owned")
'
docker run --rm --entrypoint sh "$IMAGE" -c '
    ! command -v gcc &&
    ! command -v g++ &&
    ! command -v git &&
    test ! -e /etc/apt/auth.conf.d/freeswitch.conf &&
    test ! -e /.env
'

assert_start_fails "production with SIP disabled" "SIP must be enabled" \
    -e APP_ENV=prod -e FASTCALL_SIP_ENABLED=false
assert_start_fails "production without gateway or host" "are required when APP_ENV=prod" \
    -e APP_ENV=prod -e FASTCALL_SIP_ENABLED=true -e FREESWITCH_EXTERNAL_IP=127.0.0.1
assert_start_fails "production without gateway host" "are required when APP_ENV=prod" \
    -e APP_ENV=prod -e FASTCALL_SIP_ENABLED=true -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_SIP_GATEWAY=smoke -e FREESWITCH_SIP_HOST=
assert_start_fails "production user dial mode" "must be gateway when APP_ENV=prod" \
    -e APP_ENV=prod -e FASTCALL_SIP_ENABLED=true -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_SIP_GATEWAY=smoke -e FREESWITCH_SIP_HOST=127.0.0.1 \
    -e FREESWITCH_DIAL_MODE=user
assert_start_fails "production loopback dial mode" "must be gateway when APP_ENV=prod" \
    -e APP_ENV=prod -e FASTCALL_SIP_ENABLED=true -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_SIP_GATEWAY=smoke -e FREESWITCH_SIP_HOST=127.0.0.1 \
    -e FREESWITCH_DIAL_MODE=loopback
assert_start_fails "production without external IP" "FREESWITCH_EXTERNAL_IP is required" \
    -e APP_ENV=prod -e FASTCALL_SIP_ENABLED=true -e FREESWITCH_EXTERNAL_IP= \
    -e FREESWITCH_SIP_GATEWAY=smoke -e FREESWITCH_SIP_HOST=127.0.0.1
assert_start_fails "production with default ESL password" "non-default value" \
    -e APP_ENV=prod -e FASTCALL_SIP_ENABLED=true -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_SIP_GATEWAY=smoke -e FREESWITCH_SIP_HOST=127.0.0.1 \
    -e FREESWITCH_ESL_PASSWORD=ClueCon
assert_start_fails "production with weak ESL password" "at least 16 characters" \
    -e APP_ENV=prod -e FASTCALL_SIP_ENABLED=true -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_SIP_GATEWAY=smoke -e FREESWITCH_SIP_HOST=127.0.0.1 \
    -e FREESWITCH_ESL_PASSWORD=weak
assert_start_fails "production with weak local password" "strong, non-repetitive" \
    -e APP_ENV=prod -e FASTCALL_SIP_ENABLED=true -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_SIP_GATEWAY=smoke -e FREESWITCH_SIP_HOST=127.0.0.1 \
    -e FREESWITCH_LOCAL_PASSWORD=aaaaaaaaaaaa

start_container \
    -e APP_ENV=prod \
    -e FASTCALL_SIP_ENABLED=true \
    -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_DOMAIN=127.0.0.1 \
    -e FREESWITCH_SIP_GATEWAY=smoke \
    -e FREESWITCH_SIP_HOST=127.0.0.1 \
    -e FREESWITCH_SIP_REGISTER=false \
    -e 'FREESWITCH_SIP_FROM_USER=smoke"caller' \
    -e FREESWITCH_SIP_CALLER_ID_IN_FROM=true
check_modules
check_wss
assert_contains "$(fs_cli 'sofia status gateway smoke')" "Proxy"
docker exec "$CONTAINER" test ! -e /etc/freeswitch/dialplan/default/00_mock_call.xml
docker exec "$CONTAINER" test ! -e /etc/freeswitch/directory/default/1000.xml
echo "PASS: production SIP profile, IP-auth gateway, modules and WSS REGISTER"

start_container \
    -e APP_ENV=prod \
    -e FASTCALL_SIP_ENABLED=true \
    -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_DOMAIN=127.0.0.1 \
    -e FREESWITCH_SIP_GATEWAY=smoke \
    -e FREESWITCH_SIP_HOST=127.0.0.1 \
    -e FREESWITCH_SIP_REGISTER=true \
    -e 'FREESWITCH_SIP_USERNAME=smoke"user' \
    -e 'FREESWITCH_SIP_PASSWORD=smoke"pass'
check_modules
check_wss
assert_contains "$(fs_cli 'sofia status gateway smoke')" "Proxy"
echo "PASS: production registering gateway configuration"

start_container \
    -e APP_ENV=test \
    -e FASTCALL_SIP_ENABLED=true \
    -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_DOMAIN=127.0.0.1 \
    -e FREESWITCH_LOCAL_USER=8615908280801
check_modules
check_wss
docker exec "$CONTAINER" test -f /etc/freeswitch/directory/default/fastcall-local-user.xml
docker exec "$CONTAINER" test ! -e /etc/freeswitch/sip_profiles/external/smoke.xml
[[ "$(docker exec "$CONTAINER" find /etc/freeswitch/directory/default -name '*.xml' | wc -l)" -eq 1 ]] \
    || fail "default directory users were loaded alongside the internal test user"
echo "PASS: non-production local SIP test user without a gateway"

echo "FreeSWITCH image smoke passed; no host ports were published. Carrier SIP/RTP and AI media remain unverified."
