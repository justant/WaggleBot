# WaggleBot — RTX 3080 Ti (NVIDIA GPU) 환경

## 환경 개요

| 항목 | 사양 |
|------|------|
| 노드 | Windows PC (WSL2 Ubuntu 24.04) |
| GPU | NVIDIA RTX 3080 Ti (12GB VRAM) |
| Compose 파일 | `docker-compose.yml` |
| Dockerfile | `Dockerfile.gpu` |
| LLM 모델 | `.env`의 `OLLAMA_MODEL` (기본값: `qwen2.5:7b`) |
| 영상 인코딩 | `h264_nvenc` (GPU 가속) |

---

## WSL2 GPU Docker — 알려진 함정

> 이 환경에서 실제로 발생했던 두 가지 핵심 문제. 최초 설정 전 반드시 숙지.

### 함정 1: Docker Snap 패키지 → WSL2 NVIDIA 라이브러리 차단

Ubuntu에서 `snap install docker`로 설치한 경우 발생.

- Snap 샌드박스가 `/usr/lib/wsl/lib/` 접근을 차단
- `nvidia-container-cli`가 `libnvidia-ml.so.1`을 찾지 못해 컨테이너 생성 실패
- 증상: `OCI runtime create failed: ... libnvidia-ml.so.1: cannot open shared object file`
- 확인: `docker info --format '{{.DockerRootDir}}'` 결과가 `/var/snap/...`이면 Snap

**해결**: Snap 제거 → Docker Engine(apt) 설치 (`scripts/setup_docker_gpu.sh`가 자동 처리)

### 함정 2: WSL2 + systemd mount namespace 분리 → Docker 소켓 불가시

systemd 활성화(`/etc/wsl.conf`의 `systemd=true`) 환경에서 발생.

- systemd와 유저 셸이 서로 다른 mount namespace를 사용
- `docker.socket`이 생성하는 `/run/docker.sock`이 systemd namespace 안에만 존재
- 유저 셸, `sudo`, `nsenter -m -t 1` 어디서도 소켓 파일이 보이지 않음
- 증상: `dial unix /var/run/docker.sock: connect: no such file or directory`

**해결**: `docker.socket` 비활성화 + dockerd가 TCP(`127.0.0.1:2375`)로 직접 리스닝
→ TCP는 mount namespace와 무관하게 접근 가능

---

## 전제 조건

- Windows용 NVIDIA 드라이버 설치 (WSL2에서 `nvidia-smi` 또는 `/usr/lib/wsl/lib/nvidia-smi` 실행 가능)
- **Docker Engine(apt) 설치** — Snap 아님 (위 함정 1 참고)
- NVIDIA Container Toolkit 설치
- Ollama 설치 및 `.env`의 `OLLAMA_MODEL`에 지정한 모델 다운로드

---

## 최초 설정 (권장: 자동 스크립트)

```bash
cd ~/Data/WaggleBot
bash scripts/setup_docker_gpu.sh
```

스크립트가 수행하는 작업 (7단계):
1. NVIDIA GPU 확인 (`/usr/lib/wsl/lib/nvidia-smi`)
2. Snap Docker 감지 시 자동 제거
3. Docker Engine(apt) 설치 (없는 경우)
4. WSL2 mount namespace 우회 — TCP 리스너 설정 + `docker.socket` 비활성화
5. NVIDIA Container Toolkit 설치
6. NVIDIA 런타임 + `config.toml` 설정 (`no-cgroups=true` 포함)
7. GPU 접근 테스트

---

## 수동 설정

### 1. Docker Engine (apt) 설치

```bash
# Snap Docker 설치 여부 확인
docker info --format '{{.DockerRootDir}}'
# /var/snap/... 이 나오면 Snap → 제거 필요
sudo snap remove docker --purge

# Docker Engine 설치
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu \
$(. /etc/os-release && echo $VERSION_CODENAME) stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker $USER
```

### 2. WSL2 mount namespace 우회 설정

```bash
# override.conf — TCP 리스너 + WSL2 라이브러리 경로
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/override.conf << 'EOF'
[Service]
Environment="LD_LIBRARY_PATH=/usr/lib/wsl/lib"
ExecStart=
ExecStart=/usr/bin/dockerd -H tcp://127.0.0.1:2375 --containerd=/run/containerd/containerd.sock
EOF

# docker.socket 비활성화 (Unix 소켓 활성화 불필요)
sudo systemctl disable docker.socket
sudo systemctl stop docker.socket
```

### 3. NVIDIA Container Toolkit 설치

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
```

### 4. NVIDIA 런타임 설정

```bash
# daemon.json (hosts 키 없이 — ExecStart의 -H와 중복 금지)
sudo tee /etc/docker/daemon.json << 'EOF'
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
sudo nvidia-ctk runtime configure --runtime=docker
sudo nvidia-ctk config \
    --config-file /etc/nvidia-container-runtime/config.toml \
    --in-place \
    --set nvidia-container-cli.no-cgroups=true

# WSL2 라이브러리 경로 ldconfig 등록
echo "/usr/lib/wsl/lib" | sudo tee /etc/ld.so.conf.d/nvidia-wsl.conf
sudo ldconfig
```

### 5. Docker 시작 및 DOCKER_HOST 설정

```bash
sudo systemctl daemon-reload
sudo systemctl enable docker
sudo systemctl restart docker

# 영구 등록
echo 'export DOCKER_HOST=tcp://127.0.0.1:2375' >> ~/.bashrc
source ~/.bashrc

# 확인
docker ps
docker run --rm --gpus all ubuntu:22.04 nvidia-smi
```

### 6. Ollama 설치 및 0.0.0.0 Listen 설정

```bash
sudo systemctl edit ollama.service
```

아래 내용 입력 후 저장:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
ollama pull qwen2.5:7b
```

---

## 실행 / 종료

```bash
# 시작 (최초 1회는 빌드 시간 소요)
docker compose up -d

# 로그 확인
docker compose logs --tail 50 ai_worker

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
docker logs --tail 50 -f wagglebot-ai_worker-1
```

`requirements.txt` 변경 시에만 재빌드 필요:

```bash
docker compose build ai_worker
docker compose up -d ai_worker
```

---

## 문제 해결

### Docker 소켓 연결 실패 (WSL2 mount namespace)

**증상:**
```
dial unix /var/run/docker.sock: connect: no such file or directory
```
`sudo docker ps`도 실패, `nsenter -m -t 1`으로도 소켓 파일 없음.

**원인:** WSL2 + systemd 환경에서 systemd와 유저 셸이 다른 mount namespace 사용.

**해결:**
```bash
# override.conf에 TCP 리스너 추가 확인
cat /etc/systemd/system/docker.service.d/override.conf
# ExecStart에 -H tcp://127.0.0.1:2375 있어야 함

# DOCKER_HOST 설정 확인
echo $DOCKER_HOST
# tcp://127.0.0.1:2375 이어야 함

# 미설정 시
export DOCKER_HOST=tcp://127.0.0.1:2375
echo 'export DOCKER_HOST=tcp://127.0.0.1:2375' >> ~/.bashrc
```

### GPU 인식 실패 (Snap Docker)

**증상:**
```
nvidia-container-cli: initialization error: load library failed: libnvidia-ml.so.1
Auto-detected mode as 'legacy'
```

**확인:**
```bash
docker info --format '{{.DockerRootDir}}'
# /var/snap/ 이면 Snap Docker → 제거 필요
```

**해결:**
```bash
sudo snap remove docker --purge
# 이후 수동 설정 1~5 단계 진행
```

### GPU 인식 실패 (런타임 미설정)

**증상:**
```
could not select device driver "" with capabilities: [[gpu]]
```

**해결:**
```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker run --rm --gpus all ubuntu:22.04 nvidia-smi
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
