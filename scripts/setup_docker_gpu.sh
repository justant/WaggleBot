#!/bin/bash

# Docker용 NVIDIA Container Toolkit 설정 스크립트
# WSL2 환경에서 Docker Compose가 GPU를 인식하도록 설정합니다.

set -e

echo "============================================"
echo "Docker용 NVIDIA Container Toolkit 설정"
echo "============================================"
echo

# 1. NVIDIA GPU 확인
echo "[1/5] NVIDIA GPU 확인 중..."
if ! nvidia-smi &>/dev/null; then
    echo "❌ 오류: nvidia-smi를 찾을 수 없습니다. NVIDIA 드라이버가 설치되어 있는지 확인하세요."
    exit 1
fi
echo "✅ NVIDIA GPU 감지됨"
nvidia-smi --query-gpu=name --format=csv,noheader
echo

# 2. Docker 설치 확인
echo "[2/5] Docker 설치 확인 중..."
if ! command -v docker &>/dev/null; then
    echo "❌ 오류: Docker가 설치되어 있지 않습니다."
    echo "Docker 설치 가이드: https://docs.docker.com/engine/install/ubuntu/"
    exit 1
fi
echo "✅ Docker 설치 확인됨"
docker --version
echo

# 3. NVIDIA Container Toolkit Repository 추가
echo "[3/5] NVIDIA Container Toolkit Repository 추가 중..."
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
echo "✅ Repository 추가 완료"
echo

# 4. NVIDIA Container Toolkit 설치
echo "[4/5] NVIDIA Container Toolkit 설치 중..."
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
echo "✅ 설치 완료"
echo

# 5. Docker 데몬 설정 및 재시작
echo "[5/5] Docker 데몬 설정 중..."
sudo nvidia-ctk runtime configure --runtime=docker
echo "✅ Docker 데몬 설정 완료"
echo

echo "Docker 데몬 재시작 중..."
sudo service docker restart
sleep 3
echo "✅ Docker 데몬 재시작 완료"
echo

# 6. 설정 확인
echo "============================================"
echo "설정 확인 중..."
echo "============================================"
echo

# Docker GPU 테스트
echo "Docker GPU 테스트 실행 중..."
if docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi &>/dev/null; then
    echo "✅ Docker GPU 설정 성공!"
    echo
    docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
else
    echo "❌ 오류: Docker가 GPU를 인식하지 못했습니다."
    echo "다음을 확인하세요:"
    echo "  1. WSL2에서 NVIDIA 드라이버가 올바르게 설치되었는지 확인"
    echo "  2. Docker 데몬이 정상적으로 재시작되었는지 확인"
    echo "  3. /etc/docker/daemon.json 파일을 확인하여 nvidia 런타임이 추가되었는지 확인"
    exit 1
fi

echo
echo "============================================"
echo "✅ 모든 설정이 완료되었습니다!"
echo "============================================"
echo
echo "다음 명령어로 WaggleBot을 실행할 수 있습니다:"
echo "  cd /home/justant/Data/WaggleBot"
echo "  docker compose up -d"
echo
