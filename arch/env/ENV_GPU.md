# WaggleBot — RTX 3080 Ti (NVIDIA GPU) 환경

## 환경 개요

| 항목 | 사양 |
|------|------|
| 노드 | Windows PC (WSL Ubuntu) |
| GPU | NVIDIA RTX 3080 Ti (12GB VRAM) |
| Compose 파일 | `docker-compose.yml` |
| Dockerfile | `Dockerfile.gpu` |
| LLM 모델 | `qwen2.5:14b` |
| 영상 인코딩 | `h264_nvenc` (GPU 가속) |

---

## 전제 조건

- NVIDIA 드라이버 설치 (`nvidia-smi` 정상 출력)
- Docker 설치
- NVIDIA Container Toolkit 설치
- Ollama 설치 및 `qwen2.5:14b` 모델 다운로드

---

## 최초 설정

### 1. Docker 설치

```bash
# Docker 공식 GPG 키 추가
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Docker 저장소 추가
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Docker 설치
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# 서비스 시작 및 자동 시작 설정
sudo systemctl start docker
sudo systemctl enable docker

# 현재 사용자를 docker 그룹에 추가 (sudo 없이 docker 실행)
sudo usermod -aG docker $USER
newgrp docker
```

### 2. NVIDIA Container Toolkit 설치

자동 설정 스크립트 실행:

```bash
cd /home/justant/Data/WaggleBot
bash scripts/setup_docker_gpu.sh
```

스크립트가 수행하는 작업:
1. NVIDIA GPU 확인
2. Docker 설치 확인
3. NVIDIA Container Toolkit Repository 추가
4. NVIDIA Container Toolkit 설치
5. Docker 데몬 설정 및 재시작
6. GPU 접근 테스트

수동 설정 (스크립트 실패 시):

```bash
# Repository 추가
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# 설치 및 Docker 데몬 설정
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 테스트
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

### 3. Ollama 설치 및 0.0.0.0 Listen 설정

```bash
# Ollama를 Docker 브리지 네트워크에서 접근 가능하도록 설정
sudo systemctl edit ollama.service
```

아래 내용 입력 후 저장:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

### 4. 모델 다운로드

```bash
ollama pull qwen2.5:14b
```

---

## 실행 / 종료

```bash
# 시작 (최초 1회는 빌드 시간 소요)
docker compose up -d

# 로그 확인
docker compose logs -f ai_worker

# 종료
docker compose down
```

---

## 상태 확인

```bash
# 전체 컨테이너 상태
docker compose ps

# GPU 사용 현황
nvidia-smi

# Ollama 연결 확인 (컨테이너 내부에서)
docker compose exec ai_worker curl http://host.docker.internal:11434/api/tags

# CUDA 인식 확인
docker exec wagglebot-ai_worker-1 python3 -c \
  "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"
```

---

## 개발 워크플로우 — Hot Reload

볼륨 마운트(`./:/app`) 덕분에 소스 코드 수정 후 재빌드 없이 컨테이너 재시작만으로 반영됨:

```bash
# 1. 코드 수정
# 2. 컨테이너 재시작 (빌드 없음)
docker restart wagglebot-ai_worker-1

# 3. 로그 확인
docker logs -f wagglebot-ai_worker-1
```

`requirements.txt` 변경 시에만 재빌드 필요:

```bash
docker compose build ai_worker
docker compose up -d ai_worker
```

---

## 문제 해결

### GPU 인식 실패

**증상:**
```
docker.errors.APIError: could not select device driver "" with capabilities: [[gpu]]
```

**해결:**
```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

### Ollama 연결 실패

**증상:**
```
ConnectionError: HTTPConnectionPool(host='host.docker.internal', port=11434): Connection refused
```

**해결 — Option 1: extra_hosts 확인 (권장)**

`docker-compose.yml`의 `ai_worker` 서비스에 아래 설정 확인:
```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
environment:
  OLLAMA_HOST: "http://host.docker.internal:11434"
```

**해결 — Option 2: network_mode: host (대안)**

```yaml
# docker-compose.yml의 ai_worker 서비스
network_mode: host
environment:
  OLLAMA_HOST: "http://127.0.0.1:11434"
  DATABASE_URL: "mysql+pymysql://wagglebot:password@127.0.0.1/wagglebot"
```

### 볼륨 마운트 권한

**증상:**
```
PermissionError: [Errno 13] Permission denied: '/app/media/...'
```

**해결:**
```bash
sudo chown -R $USER:$USER ./media
chmod -R 755 ./media
docker restart wagglebot-ai_worker-1
```

### Docker 데몬 연결 실패

**증상:**
```
Cannot connect to the Docker daemon at unix:///var/run/docker.sock.
```

**해결:**
```bash
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER
newgrp docker
```
