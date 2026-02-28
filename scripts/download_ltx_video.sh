#!/bin/bash
set -euo pipefail

# LTX-Video 모델 다운로드 스크립트
# 사용: bash scripts/download_ltx_video.sh

MODEL_DIR="checkpoints/ltx-video"
MODEL_FILE="ltx-video-2b-v0.9.1.safetensors"
HF_REPO="Lightricks/LTX-Video"

echo "=== LTX-Video 모델 다운로드 ==="
echo "대상 디렉터리: ${MODEL_DIR}"

mkdir -p "${MODEL_DIR}"

if [ -f "${MODEL_DIR}/${MODEL_FILE}" ]; then
    echo "모델 파일이 이미 존재합니다: ${MODEL_DIR}/${MODEL_FILE}"
    echo "재다운로드하려면 파일을 삭제하고 다시 실행하세요."
    exit 0
fi

# huggingface_hub CLI 확인
if ! command -v huggingface-cli &> /dev/null; then
    echo "huggingface-cli 미설치 — pip install huggingface_hub 실행"
    pip install huggingface_hub
fi

echo "다운로드 시작: ${HF_REPO}..."
huggingface-cli download "${HF_REPO}" \
    --local-dir "${MODEL_DIR}" \
    --include "*.safetensors" "*.json" "*.txt"

echo ""
echo "=== 다운로드 완료 ==="
echo "확인:"
ls -lh "${MODEL_DIR}/"
echo ""
echo "다음 단계: docker-compose.yml에 comfyui 서비스가 추가되었는지 확인하세요."
