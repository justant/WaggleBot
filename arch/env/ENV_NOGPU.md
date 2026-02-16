# WaggleBot — 갤럭시북5 프로 (Intel Arc) 저사양 환경

## 환경 개요

| 항목 | 사양 |
|------|------|
| 노드 | 갤럭시북5 프로 (Intel Arc 130V) |
| GPU | 없음 (CPU 추론) |
| Compose 파일 | `docker-compose.galaxybook.yml` |
| Dockerfile | `Dockerfile` |
| LLM 모델 | `.env`의 `OLLAMA_MODEL` (기본값: `qwen2.5:1.5b`, 경량) |
| 영상 인코딩 | `libx264` (자동 폴백) |

### 고사양 환경과의 차이

| 항목 | 3080 Ti PC | 갤럭시북5 프로 |
|------|-----------|--------------|
| Compose 파일 | `docker-compose.yml` | `docker-compose.galaxybook.yml` |
| Dockerfile | `Dockerfile.gpu` | `Dockerfile` |
| LLM 모델 | `.env`: `OLLAMA_MODEL=qwen2.5:14b` | `.env`: `OLLAMA_MODEL=qwen2.5:1.5b` |
| GPU 요건 | NVIDIA 필수 | 없음 (CPU 추론) |
| 영상 인코딩 | `h264_nvenc` | `libx264` 자동 폴백 |
| NVIDIA Container Toolkit | 필요 | 불필요 |

---

## 전제 조건

- Docker 설치 (NVIDIA Container Toolkit **불필요**)
- Ollama 설치 및 `.env`의 `OLLAMA_MODEL`에 지정한 모델 다운로드 (기본값: `qwen2.5:1.5b`)

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

# 현재 사용자를 docker 그룹에 추가
sudo usermod -aG docker $USER
newgrp docker
```

### 2. Ollama 설치 및 0.0.0.0 Listen 설정

```bash
# Ollama 설치
curl -fsSL https://ollama.com/install.sh | sh

# Docker 브리지 네트워크에서 접근 가능하도록 설정
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

### 3. `.env` 설정 및 모델 다운로드

`.env`에서 사용할 모델을 지정합니다 (기본값: `qwen2.5:1.5b`):

```bash
# .env
OLLAMA_MODEL=qwen2.5:1.5b
```

이후 해당 모델을 다운로드합니다:

```bash
ollama pull qwen2.5:1.5b
# 다른 모델을 .env에 지정한 경우 해당 모델명으로 변경

# 정상 동작 확인
curl http://localhost:11434/api/tags
```

---

## 실행 / 종료

```bash
# 시작
docker compose -f docker-compose.galaxybook.yml up -d

# 로그 확인
docker compose -f docker-compose.galaxybook.yml logs -f ai_worker

# 종료
docker compose -f docker-compose.galaxybook.yml down
```

---

## 상태 확인

```bash
# 전체 컨테이너 상태
docker compose -f docker-compose.galaxybook.yml ps

# Ollama 연결 확인 (컨테이너 내부에서)
docker compose -f docker-compose.galaxybook.yml exec ai_worker curl http://host.docker.internal:11434/api/tags

# libx264 폴백 로그 확인
docker compose -f docker-compose.galaxybook.yml logs ai_worker | grep -i "nvenc\|libx264\|폴백"
```

---

## 문제 해결

### Ollama 연결 실패

**증상:**
```
ConnectionError: HTTPConnectionPool(host='host.docker.internal', port=11434): Connection refused
```

**해결 — systemd 설정 확인:**

```bash
# Ollama가 0.0.0.0에서 listen하는지 확인
sudo systemctl edit ollama.service
# [Service] 섹션에 Environment="OLLAMA_HOST=0.0.0.0" 있는지 확인

sudo systemctl daemon-reload && sudo systemctl restart ollama
curl http://localhost:11434/api/tags
```

**해결 — docker-compose.galaxybook.yml 설정 확인:**

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
environment:
  OLLAMA_HOST: "http://host.docker.internal:11434"
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
docker compose -f docker-compose.galaxybook.yml restart ai_worker
```
