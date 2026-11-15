#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${FASTCALL_FREESWITCH_IMAGE:-fastcall-freeswitch:dev}"
CONTAINER="fastcall-fs-image-smoke-$$"
PASSWORD="$(openssl rand -base64 24)\""

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

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

start_container() {
    cleanup
    docker run -d --name "$CONTAINER" \
        -e FREESWITCH_ESL_PASSWORD="$PASSWORD" \
        -e FREESWITCH_ESL_ALLOWED_CIDR=127.0.0.1/32 \
        "$@" "$IMAGE" >/dev/null
    for _ in {1..45}; do
        if docker exec "$CONTAINER" fs_cli -p "$PASSWORD" -x status >/dev/null 2>&1; then
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

fs_cli() {
    docker exec "$CONTAINER" fs_cli -p "$PASSWORD" -x "$1"
}

check_modules() {
    for module in mod_sofia mod_loopback mod_audio_stream; do
        [[ "$(fs_cli "module_exists $module")" == "true" ]] || fail "$module is not loaded"
    done
    assert_contains "$(fs_cli 'show api uuid_audio_stream')" "mod_audio_stream"
    assert_contains "$(fs_cli 'sofia status profile external')" "BIND-URL"
    assert_contains "$(fs_cli 'show codecs')" "G.711 alaw"
    assert_contains "$(fs_cli 'show codecs')" "G.711 ulaw"
    if docker exec "$CONTAINER" fs_cli -p ClueCon -x status >/dev/null 2>&1; then
        fail "the default ESL password was accepted"
    fi
}

cd "$ROOT_DIR"
docker build \
    --build-arg DEBIAN_MIRROR="${FASTCALL_DEBIAN_MIRROR:-http://mirrors.aliyun.com/debian}" \
    --build-arg DEBIAN_SECURITY_MIRROR="${FASTCALL_DEBIAN_SECURITY_MIRROR:-http://mirrors.aliyun.com/debian-security}" \
    -f deploy/freeswitch/Dockerfile -t "$IMAGE" .

start_container -e FASTCALL_SIP_ENABLED=false -e FASTCALL_MOCK_CALLS_ENABLED=true
check_modules
docker exec "$CONTAINER" test ! -e /usr/local/freeswitch/etc/freeswitch/sip_profiles/external/fastcall.xml
fs_cli 'bgapi originate {origination_uuid=00000000-0000-0000-0000-000000000001,fastcall_mock_call=true}loopback/mock-call &park()' >/dev/null
sleep 2
assert_contains "$(fs_cli 'show channels')" "mock-call"
fs_cli 'hupall NORMAL_CLEARING' >/dev/null
sleep 1
assert_contains "$(fs_cli 'show channels')" "0 total."
echo "PASS: Mock-only image, modules, ESL authentication and loopback dialplan"

start_container \
    -e FASTCALL_SIP_ENABLED=true \
    -e FASTCALL_MOCK_CALLS_ENABLED=false \
    -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_SIP_GATEWAY=smoke \
    -e FREESWITCH_SIP_HOST=127.0.0.1 \
    -e FREESWITCH_SIP_REGISTER=false \
    -e 'FREESWITCH_SIP_FROM_USER=smoke"caller' \
    -e FREESWITCH_SIP_CALLER_ID_IN_FROM=true
check_modules
assert_contains "$(fs_cli 'sofia status gateway smoke')" "Proxy"
docker exec "$CONTAINER" test ! -e /usr/local/freeswitch/etc/freeswitch/dialplan/default/00_mock_call.xml
echo "PASS: SIP profile and IP-auth gateway configuration"

start_container \
    -e FASTCALL_SIP_ENABLED=true \
    -e FASTCALL_MOCK_CALLS_ENABLED=true \
    -e FREESWITCH_EXTERNAL_IP=127.0.0.1 \
    -e FREESWITCH_SIP_GATEWAY=smoke \
    -e FREESWITCH_SIP_HOST=127.0.0.1 \
    -e FREESWITCH_SIP_REGISTER=true \
    -e 'FREESWITCH_SIP_USERNAME=smoke"user' \
    -e 'FREESWITCH_SIP_PASSWORD=smoke"pass'
check_modules
assert_contains "$(fs_cli 'sofia status gateway smoke')" "Proxy"
docker exec "$CONTAINER" test -f /usr/local/freeswitch/etc/freeswitch/dialplan/default/00_mock_call.xml
fs_cli 'bgapi originate {origination_uuid=00000000-0000-0000-0000-000000000002,fastcall_mock_call=true}loopback/mock-call &park()' >/dev/null
sleep 2
assert_contains "$(fs_cli 'show channels')" "mock-call"
fs_cli 'hupall NORMAL_CLEARING' >/dev/null
echo "PASS: SIP and Mock Call enabled in the same FreeSWITCH instance"

echo "FreeSWITCH image smoke passed. No host ports were published."
