# WaggleBot 환경별 실행 절차

---

## RTX 3080 Ti PC (메인 개발 환경)

### 전제 조건
- NVIDIA 드라이버 설치
- Docker + NVIDIA Container Toolkit 설치
- Ollama 설치 및 `qwen2.5:14b` 모델 다운로드

### 최초 설정

```bash
# 1. 모델 다운로드
ollama pull qwen2.5:14b

# 2. Ollama를 Docker 브리지에서 접근 가능하도록 설정
sudo systemctl edit ollama.service
```
```ini
# 아래 내용 입력 후 저장 (Ctrl+X → Y → Enter)
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
```
```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama
```

### 실행

```bash
# 기본 실행 (docker-compose.yml 사용)
docker compose up -d

# 로그 확인
docker compose logs -f ai_worker
```

### 상태 확인

```bash
# 전체 컨테이너 상태
docker compose ps

# Ollama 접근 확인 (컨테이너 내부에서)
docker compose exec ai_worker curl http://host.docker.internal:11434/api/tags

# GPU 사용 현황
nvidia-smi
```

### 종료

```bash
docker compose down
```

---

## 갤럭시북5 프로 (Intel Arc 130V, 테스트 환경)

### 전제 조건
- Docker 설치 (NVIDIA Container Toolkit 불필요)
- Ollama 설치 및 `qwen2.5:1.5b` 모델 다운로드

### 최초 설정

```bash
# 1. Ollama 설치 (미설치 시)
curl -fsSL https://ollama.com/install.sh | sh

# 2. Ollama를 Docker 브리지에서 접근 가능하도록 설정
sudo systemctl edit ollama.service
```
```ini
# 아래 내용 입력 후 저장
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
```
```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama

# 3. 경량 모델 다운로드
ollama pull qwen2.5:1.5b

# 4. Ollama 정상 동작 확인
curl http://localhost:11434/api/tags
```

### 실행

```bash
# 갤럭시북 전용 compose 파일 사용
docker compose -f docker-compose.galaxybook.yml up -d

# 로그 확인
docker compose -f docker-compose.galaxybook.yml logs -f ai_worker
```

### 상태 확인

```bash
# 전체 컨테이너 상태
docker compose -f docker-compose.galaxybook.yml ps

# Ollama 접근 확인 (컨테이너 내부에서)
docker compose -f docker-compose.galaxybook.yml exec ai_worker curl http://host.docker.internal:11434/api/tags

# 영상 렌더링 시 libx264 폴백 로그 확인
docker compose -f docker-compose.galaxybook.yml logs ai_worker | grep -i "nvenc\|libx264\|폴백"
```

### 종료

```bash
docker compose -f docker-compose.galaxybook.yml down
```

---

## 환경별 차이 요약

| 항목 | 3080 Ti PC | 갤럭시북5 프로 |
|------|-----------|--------------|
| Compose 파일 | `docker-compose.yml` | `docker-compose.galaxybook.yml` |
| Dockerfile | `Dockerfile.gpu` | `Dockerfile` |
| LLM 모델 | `qwen2.5:14b` | `qwen2.5:1.5b` |
| GPU 요건 | NVIDIA 필수 | 없음 (CPU 추론) |
| 영상 인코딩 | `h264_nvenc` | `libx264` 자동 폴백 |

---

## 공통 운영 명령어

```bash
# DB 초기화 (최초 1회)
# 3080 Ti
docker compose exec crawler python -c "from db.session import init_db; init_db()"
# 갤럭시북
docker compose -f docker-compose.galaxybook.yml exec crawler python -c "from db.session import init_db; init_db()"

# 대시보드 접속
open http://localhost:8501
```
