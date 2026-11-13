#!/usr/bin/env bash
set -euo pipefail

COMMIT="a25fb1fe530ec6a612d321ff04f70be69b1a257c"
WORK_DIR="${TMPDIR:-/tmp}/fastcall-mod-audio-stream"
if [[ ! -d "$WORK_DIR/.git" ]]; then
  git clone https://github.com/AlchemillaHQ/mod-audio-stream.git "$WORK_DIR"
fi
git -C "$WORK_DIR" fetch origin "$COMMIT"
git -C "$WORK_DIR" checkout --detach "$COMMIT"
test "$(git -C "$WORK_DIR" rev-parse HEAD)" = "$COMMIT"

COMPOSE_FILE="$WORK_DIR/test/integration/compose.yaml"
cleanup() {
  docker compose -f "$COMPOSE_FILE" down -v
}
trap cleanup EXIT
docker compose -f "$COMPOSE_FILE" build
docker compose -f "$COMPOSE_FILE" up -d
for _ in {1..30}; do
  if docker compose -f "$COMPOSE_FILE" exec -T freeswitch fs_cli -x version >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker compose -f "$COMPOSE_FILE" exec -T freeswitch bash /usr/src/mod_audio_stream/test/integration/run.sh
