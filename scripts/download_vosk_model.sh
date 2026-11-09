#!/usr/bin/env bash
# 下载并解压 Vosk 西班牙语小模型到 models/ 目录。
# 用法: bash scripts/download_vosk_model.sh
# 然后在 .env 中设置 VOSK_MODEL_PATH=<项目根>/models/vosk-model-small-es-0.42
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL_DIR="${ROOT_DIR}/models"
MODEL_NAME="vosk-model-small-es-0.42"
MODEL_URL="https://alphacephei.com/vosk/models/${MODEL_NAME}.zip"

mkdir -p "${MODEL_DIR}"
cd "${MODEL_DIR}"

if [ -d "${MODEL_NAME}" ]; then
    echo "模型已存在: ${MODEL_DIR}/${MODEL_NAME}"
    exit 0
fi

echo "下载 ${MODEL_URL} ..."
curl -fSL -o "${MODEL_NAME}.zip" "${MODEL_URL}"
unzip -q "${MODEL_NAME}.zip"
rm "${MODEL_NAME}.zip"
echo "完成。请设置: VOSK_MODEL_PATH=${MODEL_DIR}/${MODEL_NAME}"
