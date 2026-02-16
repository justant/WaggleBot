# WaggleBot

> 커뮤니티 인기 게시글을 자동으로 수집하여 유튜브 쇼츠 영상으로 변환하는 AI 파이프라인

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)
[![GPU](https://img.shields.io/badge/GPU-NVIDIA%20RTX%203080%20Ti-76B900.svg)](https://www.nvidia.com/)

---

## 프로젝트 개요

WaggleBot은 커뮤니티 게시글을 크롤링하고, LLM으로 요약한 뒤, TTS와 FFmpeg를 이용해 쇼츠 영상(9:16)을 자동 생성하는 완전 자동화 시스템입니다.

### 주요 기능

- **자동 크롤링**: 네이트판 등 커뮤니티 사이트에서 인기 게시글 수집
- **AI 요약**: 로컬 LLM(Ollama)을 사용한 쇼츠 대본 생성
- **TTS 음성 합성**: Edge-TTS, Kokoro-82M, GPT-SoVITS 지원
- **영상 렌더링**: FFmpeg + NVENC GPU 가속
- **관리 대시보드**: Streamlit 기반 웹 UI로 게시글 승인/거절
- **자동 업로드**: YouTube 쇼츠 자동 업로드 (Phase 3)

### 시스템 플로우

```
커뮤니티 크롤링 → MariaDB 저장 → Streamlit 검수 → AI 워커 (LLM → TTS → 렌더링) → YouTube 업로드
```

### 기술 스택

| 분류 | 기술 |
|------|------|
| **언어** | Python 3.12 |
| **LLM** | Ollama (qwen2.5:14b / qwen2.5:1.5b) |
| **TTS** | Edge-TTS, Kokoro-82M, GPT-SoVITS |
| **DB** | MariaDB 11.x + SQLAlchemy ORM |
| **영상** | FFmpeg (h264_nvenc 코덱) |
| **웹** | Streamlit Dashboard |
| **인프라** | Docker Compose (GPU 지원) |

---

## 설치 가이드

두 가지 환경을 지원합니다:

| 환경 | 가이드 |
|------|--------|
| **RTX 3080 Ti (NVIDIA GPU)** | 아래 절차 또는 [arch/env/ENV_GPU.md](arch/env/ENV_GPU.md) |
| **갤럭시북5 프로 (Intel Arc, GPU 없음)** | [arch/env/ENV_NOGPU.md](arch/env/ENV_NOGPU.md) |

아래는 **NVIDIA GPU 환경** 기준 설치 절차입니다.

---

### 1. WSL2 + Ubuntu 설치

```powershell
# PowerShell (관리자 권한)
wsl --install -d Ubuntu-22.04
```

이후 Ubuntu 터미널에서 진행합니다.

---

### 2. Docker 설치

```bash
# Docker 공식 저장소 추가
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 설치
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker

# sudo 없이 docker 사용
sudo usermod -aG docker $USER && newgrp docker
```

---

### 3. NVIDIA Container Toolkit 설치

```bash
bash scripts/setup_docker_gpu.sh
```

스크립트 없이 수동 설치:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 확인
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

---

### 4. Ollama 설치 및 설정

```bash
# 설치
curl -fsSL https://ollama.com/install.sh | sh

# Docker 브리지에서 접근 가능하도록 설정
sudo systemctl edit ollama.service
```

아래 내용 입력 후 저장:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart ollama

# 모델 다운로드
ollama pull qwen2.5:14b

# 확인
curl http://localhost:11434/api/tags
```

---

### 5. 프로젝트 설정

```bash
git clone https://github.com/justant/WaggleBot.git
cd WaggleBot

cp .env.example .env
nano .env
```

`.env` 최소 설정:

```env
DB_ROOT_PASSWORD=your_secure_password
DB_PASSWORD=another_secure_password
HF_TOKEN=hf_your_token_here
```

---

### 6. 컨테이너 실행

```bash
# 빌드 및 시작
docker compose up -d

# 상태 확인
docker compose ps

# 대시보드 접속
# http://localhost:8501
```

설치 검증:

```bash
# GPU 인식 확인
docker exec wagglebot-ai_worker-1 python3 -c \
  "import torch; print('CUDA:', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"

# Ollama 연결 확인
docker compose exec ai_worker curl http://host.docker.internal:11434/api/tags
```

---

### 개발 워크플로우

볼륨 마운트(`./:/app`)로 소스 코드 변경 시 재빌드 없이 즉시 반영됩니다.

```bash
# 코드 수정 후 컨테이너 재시작만으로 반영
docker restart wagglebot-ai_worker-1

# 로그 확인
docker logs -f wagglebot-ai_worker-1
```

`requirements.txt` 변경 시에만 재빌드 필요:

```bash
docker compose build ai_worker && docker compose up -d ai_worker
```

테스트:

```bash
# 전체 테스트
docker exec wagglebot-crawler-1 pytest

# 크롤러 1회 실행 (테스트)
docker exec wagglebot-crawler-1 python main.py --once
```

---

## 프로젝트 구조

```
WaggleBot/
├── CLAUDE.md                      # AI 개발 규칙
├── docker-compose.yml             # GPU 환경 Compose
├── docker-compose.galaxybook.yml  # 저사양 환경 Compose
├── arch/
│   ├── dev_spec.md                # 상세 기술 명세
│   └── env/
│       ├── ENV_GPU.md             # NVIDIA GPU 환경 가이드
│       └── ENV_NOGPU.md           # 저사양 환경 가이드
├── crawlers/
│   ├── ADDING_CRAWLER.md          # 크롤러 추가 가이드
│   ├── base.py                    # BaseCrawler 추상 클래스
│   └── nate.py                    # 네이트판 크롤러
├── db/
│   ├── models.py                  # SQLAlchemy 모델
│   └── session.py                 # DB 세션 관리
├── ai_worker/
│   ├── main.py                    # DB 폴링 메인 루프
│   ├── llm.py                     # LLM 요약기
│   ├── tts.py                     # TTS 생성기
│   └── renderer.py                # FFmpeg 영상 렌더러
├── assets/
│   ├── backgrounds/               # 9:16 배경 영상
│   └── fonts/                     # 한글 폰트
├── config/
│   └── settings.py                # 중앙화된 설정
├── monitoring/
│   ├── alerting.py                # 알림 관리자
│   └── daemon.py                  # 헬스체크 데몬
├── main.py                        # 크롤러 진입점
├── scheduler.py                   # Cron 스케줄러
└── dashboard.py                   # Streamlit 대시보드
```

---

## 사용법

### 대시보드 (http://localhost:8501)

**수신함 탭** — 크롤링된 게시글 검수
- [승인]: AI 워커가 자동으로 영상 생성 시작
- [거절]: 해당 게시글 제외

**진행 상태 탭** — 처리 현황 모니터링
- APPROVED → PROCESSING → RENDERED → UPLOADED

**갤러리 탭** — 완성된 영상 재생/다운로드

### 영상 생성 흐름

```
승인 → LLM 요약 (~30초) → TTS 생성 (~20초) → 영상 렌더링 (~1-2분) → 완료
```

### 로그 확인

```bash
docker compose logs -f ai_worker
docker compose logs -f crawler
```

---

## GPU 메모리 관리

RTX 3080 Ti (12GB VRAM)는 LLM과 TTS를 동시에 로드할 수 없어 순차 처리합니다.

### 처리 순서

```
LLM 로드 → 요약 생성 → LLM 언로드 → TTS 로드 → 음성 생성 → TTS 언로드 → FFmpeg 렌더링
```

각 단계 후 `torch.cuda.empty_cache()` + `gc.collect()` 필수.

### 코드에서 사용

```python
from ai_worker.gpu_manager import get_gpu_manager, ModelType

gpu_manager = get_gpu_manager()

with gpu_manager.managed_inference(ModelType.LLM, "summarizer"):
    summary = llm_model.generate(text)
    # 블록 종료 시 자동 메모리 해제

with gpu_manager.managed_inference(ModelType.TTS, "tts_engine"):
    audio = tts_model.synthesize(summary)
```

### 메모리 확인

```bash
# VRAM 현황
nvidia-smi

# AI 워커 GPU 로그
docker compose logs ai_worker | grep GPU
```

### 주요 제약사항

- LLM/TTS는 **4-bit 양자화 필수** (`load_in_4bit=True`)
- 영상 인코딩은 **h264_nvenc 필수** (`libx264` 사용 시 VRAM 차단)
- 동시 모델 로드 **절대 금지**

| GPU | VRAM | 권장 설정 |
|-----|------|-----------|
| RTX 3080 Ti | 12GB | 순차 처리, 4-bit 양자화 |
| RTX 3090/4090 | 24GB | 동시 로드 가능 |
