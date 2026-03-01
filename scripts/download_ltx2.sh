#!/usr/bin/env bash
set -euo pipefail

# LTX-2 모델 다운로드 스크립트
# 사용: bash scripts/download_ltx2.sh
# Repo: Lightricks/LTX-2
#
# 다운로드 대상:
# 1. ltx-2-19b-dev-fp8.safetensors (~27GB) — 풀 모델 (FP8, RTX 3090 weight streaming)
# 2. ltx-2-19b-distilled-fp8.safetensors (~27GB) — Distilled (8스텝, weight streaming)
# 3. gemma-3-12b-it-qat-q4_0-unquantized/ — Gemma 3 텍스트 인코더
# 4. ltx-2-spatial-upscaler-x2-1.0.safetensors (~1GB) — 공간 업스케일러
# 5. ltx-2-temporal-upscaler-x2-1.0.safetensors (~0.3GB) — 시간 업스케일러
# 6. ltx-2-19b-distilled-lora-384.safetensors (~7.7GB) — Distilled LoRA

echo "=== LTX-2 모델 다운로드 (Lightricks/LTX-2) ==="

# Python huggingface_hub 확인
python3 -c "import huggingface_hub" 2>/dev/null || {
    echo "huggingface_hub 미설치 — pip3 install huggingface_hub 실행"
    pip3 install --user huggingface_hub
}

REPO_ID="Lightricks/LTX-2"

# Python 헬퍼 함수: 단일 파일 다운로드
download_file() {
    local filename="$1"
    local local_dir="$2"
    python3 << PYEOF
from huggingface_hub import hf_hub_download
import shutil
from pathlib import Path

local_dir = Path("${local_dir}")
local_dir.mkdir(parents=True, exist_ok=True)
target = local_dir / "${filename}"

if target.exists():
    print(f"  이미 존재, 스킵: {target} ({target.stat().st_size / 1e9:.1f} GB)")
else:
    print(f"  다운로드 중: ${REPO_ID}/${filename} → {local_dir}/")
    cached = hf_hub_download(
        repo_id="${REPO_ID}",
        filename="${filename}",
    )
    shutil.copy2(cached, target)
    print(f"  완료: {target} ({target.stat().st_size / 1e9:.1f} GB)")
PYEOF
}

# ── 1. 메인 체크포인트 (FP8, ~27GB) ──
echo "[1/6] ltx-2-19b-dev-fp8.safetensors (~27GB)..."
download_file "ltx-2-19b-dev-fp8.safetensors" "checkpoints/ltx-2"

# ── 2. Distilled 체크포인트 (FP8, ~27GB) ──
echo "[2/6] ltx-2-19b-distilled-fp8.safetensors (~27GB)..."
download_file "ltx-2-19b-distilled-fp8.safetensors" "checkpoints/ltx-2"

# ── 3. Gemma 3 텍스트 인코더 ──
echo "[3/6] gemma-3-12b-it-qat-q4_0-unquantized (텍스트 인코더)..."
TE_DIR="checkpoints/text_encoders/gemma-3-12b-it-qat-q4_0-unquantized"
python3 << PYEOF
from huggingface_hub import snapshot_download
from pathlib import Path

local_dir = Path("${TE_DIR}")
if local_dir.exists() and (local_dir / "config.json").exists():
    print(f"  이미 존재, 스킵: {local_dir}")
else:
    print(f"  다운로드 중: google/gemma-3-12b-it-qat-q4_0-unquantized → {local_dir}/")
    local_dir.parent.mkdir(parents=True, exist_ok=True)
    snapshot_download(
        repo_id="google/gemma-3-12b-it-qat-q4_0-unquantized",
        local_dir=str(local_dir),
    )
    print(f"  완료: {local_dir}")
PYEOF

# ── 4. Spatial Upscaler (~1GB) ──
echo "[4/6] ltx-2-spatial-upscaler-x2-1.0.safetensors (~1GB)..."
download_file "ltx-2-spatial-upscaler-x2-1.0.safetensors" "checkpoints/latent_upscale_models"

# ── 5. Temporal Upscaler (~0.3GB) ──
echo "[5/6] ltx-2-temporal-upscaler-x2-1.0.safetensors (~0.3GB)..."
download_file "ltx-2-temporal-upscaler-x2-1.0.safetensors" "checkpoints/latent_upscale_models"

# ── 6. Distilled LoRA (~7.7GB) ──
echo "[6/6] ltx-2-19b-distilled-lora-384.safetensors (~7.7GB)..."
download_file "ltx-2-19b-distilled-lora-384.safetensors" "checkpoints/loras"

echo ""
echo "=== 다운로드 완료 ==="
echo ""
echo "디렉터리 구조 확인:"
find checkpoints/ltx-2 checkpoints/latent_upscale_models checkpoints/loras \
    -name "*.safetensors" 2>/dev/null | while read f; do
    size=$(du -h "$f" | cut -f1)
    echo "  $f ($size)"
done
echo ""
echo "다음 단계:"
echo "  1. docker compose build comfyui"
echo "  2. docker compose up -d comfyui"
echo ""
echo "구버전 모델 정리 (수동):"
echo "  rm -rf checkpoints/ltx-video/"
echo "  rm -rf checkpoints/clip/"
