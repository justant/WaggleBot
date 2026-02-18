#!/bin/bash
# Fish Speech 1.5 모델 다운로드 (WSL2 / Ubuntu 환경용)
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DEST="$PROJECT_ROOT/checkpoints/fish-speech-1.5"
VENV_DIR="$PROJECT_ROOT/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

echo "========================================"
echo "  Fish Speech 1.5 모델 다운로드 (WSL2)"
echo "========================================"
echo "대상 경로: $DEST"
echo ""

# ── 1. venv 존재 확인 / 생성
if [ ! -f "$VENV_PYTHON" ]; then
    echo "[1/5] venv 생성 중..."
    python3 -m venv "$VENV_DIR"
    echo "      venv 생성 완료: $VENV_DIR"
else
    echo "[1/5] venv 확인 완료: $VENV_DIR"
fi

# ── 2. 디스크 여유 공간 확인 (최소 5GB 권장)
echo "[2/5] 디스크 공간 확인..."
AVAIL_KB=$(df -k "$PROJECT_ROOT" | tail -1 | awk '{print $4}')
AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
if [ "$AVAIL_GB" -lt 5 ]; then
    echo "경고: 디스크 여유 공간 ${AVAIL_GB}GB — 5GB 이상 권장 (모델 약 2.5GB)"
    read -r -p "계속 진행하시겠습니까? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
else
    echo "      여유 공간: ${AVAIL_GB}GB ✓"
fi

# ── 3. huggingface_hub 설치 확인
echo "[3/5] huggingface_hub 확인..."
"$VENV_PYTHON" -c "import huggingface_hub" 2>/dev/null || \
    "$VENV_PIP" install --quiet "huggingface_hub[cli]"
echo "      huggingface_hub 준비 완료 ✓"

# ── 4. 모델 다운로드
#   local_dir_use_symlinks=False: WSL2에서 심링크 대신 실제 파일 복사
#   (Windows NTFS 마운트 경로에서 심링크 오류 방지)
echo "[4/5] 모델 다운로드 시작 (약 2.5GB)..."
echo "      첫 다운로드는 10~30분 소요됩니다."
echo ""

"$VENV_PYTHON" - "$DEST" << 'PYEOF'
import sys
from huggingface_hub import snapshot_download

local_dir = sys.argv[1]
print(f"저장 위치: {local_dir}")
snapshot_download(
    repo_id="fishaudio/fish-speech-1.5",
    local_dir=local_dir,
    local_dir_use_symlinks=False,
)
print(f"\n다운로드 완료: {local_dir}")
PYEOF

# ── 5. 핵심 파일 검증
echo ""
echo "[5/5] 다운로드 검증..."
MISSING=0
for f in \
    "model.pth" \
    "firefly-gan-vq-fsq-8x1024-21hz-generator.pth" \
    "tokenizer.tiktoken" \
    "config.json"; do
    if [ -f "$DEST/$f" ]; then
        SIZE=$(du -sh "$DEST/$f" | cut -f1)
        echo "  ✓ $f ($SIZE)"
    else
        echo "  ✗ $f — 누락!"
        MISSING=$((MISSING + 1))
    fi
done

if [ "$MISSING" -gt 0 ]; then
    echo ""
    echo "오류: 파일 ${MISSING}개 누락. 스크립트를 다시 실행하세요."
    exit 1
fi

# ── 참조 오디오 확인
#   settings.py의 VOICE_PRESETS["default"] = "korean_man_default.wav" 기준
echo ""
echo "── 참조 오디오 확인 ──"
VOICES_DIR="$PROJECT_ROOT/assets/voices"
DEFAULT_WAV="$VOICES_DIR/korean_man_default.wav"
DEFAULT_MAN_WAV="$VOICES_DIR/korean_man_default.wav"

if [ ! -f "$DEFAULT_WAV" ]; then
    if [ -f "DEFAULT_MAN_WAV" ]; then
        ln -sf "korean_man_default.wav" "$DEFAULT_WAV"
        echo "  ✓ korean_man_default.wav → korean_man_default.wav (심링크 생성)"
    else
        echo "  ⚠ korean_man_default.wav 없음"
        echo "    WAV 파일 (16kHz 이상, 10~30초, 잡음 없는 한국어 남성 음성)을 준비하세요:"
        echo "    $DEFAULT_WAV"
    fi
else
    echo "  ✓ korean_man_default.wav 존재 ✓"
fi

# ── 완료 메시지
echo ""
echo "========================================"
echo "  설치 완료! 다음 단계:"
echo "========================================"
echo ""
echo "  1. fish-speech 컨테이너 시작"
echo "     docker compose up fish-speech -d"
echo ""
echo "  2. 모델 로딩 완료 대기 (~90초) 및 로그 확인"
echo "     docker compose logs fish-speech -f"
echo "     # 'Application startup complete.' 메시지 확인"
echo ""
echo "  3. 상태 확인"
echo "     docker compose ps"
echo "     # fish-speech → healthy"
echo ""
echo "  4. TTS 테스트"
echo "     source venv/bin/activate"
echo "     python test/test_tts.py"
echo ""
echo "  5. (테스트 통과 후) ai_worker 기동"
echo "     docker compose up ai_worker -d"
echo ""
