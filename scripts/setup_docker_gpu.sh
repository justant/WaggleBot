#!/bin/bash

# WaggleBot — Docker GPU 환경 설정 스크립트
# WSL2 Ubuntu 24.04 + NVIDIA RTX 3080 Ti 기준
#
# 해결하는 문제:
#   1. Docker Snap 패키지는 WSL2 NVIDIA 라이브러리(/usr/lib/wsl/lib) 접근 불가
#   2. WSL2 + systemd mount namespace 분리로 Unix 소켓 접근 불가
#   → Docker Engine(apt) + TCP 리스너로 해결

set -e

echo "============================================"
echo " WaggleBot Docker GPU 환경 설정"
echo " WSL2 Ubuntu 24.04 + NVIDIA GPU"
echo "============================================"
echo

# ── 0. nvidia-smi 확인 ──────────────────────────────────────────────────────
echo "[0/7] NVIDIA GPU 확인 중..."
if ! /usr/lib/wsl/lib/nvidia-smi &>/dev/null && ! nvidia-smi &>/dev/null; then
    echo "❌ nvidia-smi를 찾을 수 없습니다."
    echo "   Windows NVIDIA 드라이버가 설치되어 있는지 확인하세요."
    exit 1
fi
echo "✅ GPU 감지됨:"
nvidia-smi --query-gpu=name,driver_model.current --format=csv,noheader 2>/dev/null \
    || /usr/lib/wsl/lib/nvidia-smi --query-gpu=name --format=csv,noheader
echo

# ── 1. Snap Docker 감지 및 제거 ──────────────────────────────────────────────
echo "[1/7] Docker 설치 방식 확인 중..."
DOCKER_ROOT=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo "")
if echo "$DOCKER_ROOT" | grep -q "snap"; then
    echo "⚠️  Snap Docker 감지됨 ($DOCKER_ROOT)"
    echo "   Snap은 WSL2 NVIDIA 라이브러리 접근이 차단됩니다."
    echo "   Snap Docker를 제거하고 Docker Engine(apt)으로 전환합니다."
    sudo snap remove docker --purge
    echo "✅ Snap Docker 제거 완료"
elif command -v docker &>/dev/null; then
    echo "✅ Docker Engine(apt) 이미 설치됨"
    docker --version
fi
echo

# ── 2. Docker Engine (apt) 설치 ──────────────────────────────────────────────
echo "[2/7] Docker Engine(apt) 설치 확인 중..."
if ! command -v docker &>/dev/null || echo "$DOCKER_ROOT" | grep -q "snap"; then
    echo "   Docker Engine 설치 중..."
    sudo apt-get update -qq
    sudo apt-get install -y ca-certificates curl gnupg

    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -qq
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin

    sudo usermod -aG docker "$USER"
    echo "✅ Docker Engine 설치 완료"
else
    echo "✅ Docker Engine 이미 설치됨"
fi
echo

# ── 3. WSL2 mount namespace 우회 — TCP 리스너 설정 ──────────────────────────
# WSL2 + systemd 환경에서 Unix 소켓(/run/docker.sock)은 systemd의 mount
# namespace 안에만 생성되어 유저 셸에서 접근 불가.
# docker.socket 비활성화 + TCP 리스너로 namespace 우회.
echo "[3/7] WSL2 mount namespace 우회 설정 (TCP 리스너)..."

sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/override.conf > /dev/null << 'EOF'
[Service]
Environment="LD_LIBRARY_PATH=/usr/lib/wsl/lib"
ExecStart=
ExecStart=/usr/bin/dockerd -H tcp://127.0.0.1:2375 --containerd=/run/containerd/containerd.sock
EOF

# docker.socket 비활성화 (fd:// 소켓 활성화 불필요)
sudo systemctl disable docker.socket 2>/dev/null || true
sudo systemctl stop docker.socket 2>/dev/null || true

echo "✅ override.conf 설정 완료 (TCP 127.0.0.1:2375)"
echo

# ── 4. NVIDIA Container Toolkit 설치 ─────────────────────────────────────────
echo "[4/7] NVIDIA Container Toolkit 확인 중..."
if ! command -v nvidia-ctk &>/dev/null; then
    echo "   설치 중..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null

    sudo apt-get update -qq
    sudo apt-get install -y nvidia-container-toolkit
    echo "✅ NVIDIA Container Toolkit 설치 완료"
else
    echo "✅ NVIDIA Container Toolkit 이미 설치됨 ($(nvidia-ctk --version 2>&1 | head -1))"
fi
echo

# ── 5. NVIDIA 런타임 및 config.toml 설정 ─────────────────────────────────────
echo "[5/7] NVIDIA 런타임 설정 중..."

# daemon.json — hosts 키 없이 (ExecStart의 -H 와 중복 금지)
sudo tee /etc/docker/daemon.json > /dev/null << 'EOF'
{
  "runtimes": {
    "nvidia": {
      "args": [],
      "path": "nvidia-container-runtime"
    }
  }
}
EOF

# config.toml 생성 + WSL2 cgroups 우회
sudo nvidia-ctk runtime configure --runtime=docker 2>/dev/null || true
sudo nvidia-ctk config \
    --config-file /etc/nvidia-container-runtime/config.toml \
    --in-place \
    --set nvidia-container-cli.no-cgroups=true 2>/dev/null || true

# WSL2 라이브러리 경로 ldconfig 등록
if ! grep -q "/usr/lib/wsl/lib" /etc/ld.so.conf.d/nvidia-wsl.conf 2>/dev/null; then
    echo "/usr/lib/wsl/lib" | sudo tee /etc/ld.so.conf.d/nvidia-wsl.conf > /dev/null
    sudo ldconfig
fi

echo "✅ NVIDIA 런타임 설정 완료"
echo

# ── 6. Docker 서비스 시작 ─────────────────────────────────────────────────────
echo "[6/7] Docker 서비스 시작 중..."
sudo systemctl daemon-reload
sudo systemctl enable docker
sudo systemctl restart docker
sleep 3

# DOCKER_HOST 환경변수 설정 (현재 세션)
export DOCKER_HOST=tcp://127.0.0.1:2375

# .bashrc 영구 등록
if ! grep -q "DOCKER_HOST" ~/.bashrc; then
    echo 'export DOCKER_HOST=tcp://127.0.0.1:2375' >> ~/.bashrc
    echo "✅ ~/.bashrc에 DOCKER_HOST 등록 완료"
fi
echo

# ── 7. GPU 접근 테스트 ────────────────────────────────────────────────────────
echo "[7/7] Docker GPU 접근 테스트 중..."
if docker run --rm --gpus all ubuntu:22.04 nvidia-smi &>/dev/null; then
    echo "✅ Docker GPU 접근 성공!"
    echo
    docker run --rm --gpus all ubuntu:22.04 nvidia-smi
else
    echo "❌ GPU 접근 실패. 아래를 확인하세요:"
    echo "   - sudo systemctl status docker"
    echo "   - sudo journalctl -u docker --no-pager -n 20"
    echo "   - /etc/docker/daemon.json 내용 확인"
    exit 1
fi

echo
echo "============================================"
echo "✅ 모든 설정 완료!"
echo "============================================"
echo
echo "⚠️  새 터미널을 열거나 아래를 실행하세요:"
echo "   source ~/.bashrc"
echo
echo "WaggleBot 실행:"
echo "   cd ~/Data/WaggleBot"
echo "   docker compose up -d"
echo "   docker compose logs --tail 30 ai_worker"
echo
