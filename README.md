# WaggleBot

> 커뮤니티 인기 게시글을 자동으로 수집하여 유튜브 쇼츠 영상으로 변환하는 AI 파이프라인

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)
[![GPU](https://img.shields.io/badge/GPU-NVIDIA%20RTX%203080%20Ti-76B900.svg)](https://www.nvidia.com/)

---

## 프로젝트 개요

커뮤니티 게시글을 크롤링하고, LLM으로 쇼츠 대본을 생성한 뒤, TTS와 FFmpeg로 9:16 영상을 자동 생성하는 완전 자동화 시스템.

### 주요 기능

- **자동 크롤링**: 네이트판 등 커뮤니티 인기 게시글 수집 + 인기도 스코어링
- **AI 대본**: Ollama LLM 기반 쇼츠 특화 3막 구조 대본 (hook/body/closer)
- **TTS**: Edge-TTS, Kokoro-82M, GPT-SoVITS 지원
- **영상 렌더링**: FFmpeg + NVENC GPU 가속, ASS 동적 자막, 장면 전환, 썸네일 자동 생성
- **대시보드**: Streamlit 기반 수신함/편집실/갤러리/분석 탭
- **자동 업로드**: YouTube Shorts + YouTube Analytics 성과 추적

### 시스템 플로우

```
크롤링 → 스코어링 → 수신함 검수 → 편집실(대본수정/TTS미리듣기)
→ AI워커(LLM → TTS → 렌더링 → 썸네일) → 갤러리 → YouTube 업로드 → 성과 분석
```

### 기술 스택

| 분류 | 기술 |
|------|------|
| 언어 | Python 3.12 |
| LLM | Ollama (`qwen2.5:14b` / `qwen2.5:1.5b`) |
| TTS | Edge-TTS, Kokoro-82M, GPT-SoVITS |
| DB | MariaDB 11.x + SQLAlchemy ORM |
| 영상 | FFmpeg (h264_nvenc / libx264 폴백) |
| 웹 | Streamlit Dashboard |
| 인프라 | Docker Compose (GPU / No-GPU 분리) |

---

## 설치 가이드

두 가지 환경을 지원합니다:

| 환경 | 가이드 |
|------|--------|
| **RTX 3080 Ti (NVIDIA GPU)** | [arch/env/ENV_GPU.md](arch/env/ENV_GPU.md) |
| **갤럭시북5 프로 (CPU 추론)** | [arch/env/ENV_NOGPU.md](arch/env/ENV_NOGPU.md) |

아래는 **NVIDIA GPU 환경** 기준 빠른 설치입니다.

### 1. WSL2 + Ubuntu 설치

```powershell
# PowerShell (관리자 권한)
wsl --install -d Ubuntu-22.04
```

### 2. Docker + NVIDIA Container Toolkit

```bash
# Docker 설치
sudo apt-get update && sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER && newgrp docker

# NVIDIA Container Toolkit (자동)
bash scripts/setup_docker_gpu.sh
```

### 3. Ollama 설치 및 설정

```bash
curl -fsSL https://ollama.com/install.sh | sh

sudo systemctl edit ollama.service
# [Service]
# Environment="OLLAMA_HOST=0.0.0.0"

sudo systemctl daemon-reload && sudo systemctl restart ollama
ollama pull qwen2.5:14b
```

### 4. 프로젝트 설정 및 실행

```bash
git clone https://github.com/justant/WaggleBot.git
cd WaggleBot

# .env 최소 설정
nano .env
# DB_ROOT_PASSWORD=...
# DB_PASSWORD=...
# HF_TOKEN=hf_...
# OLLAMA_MODEL=qwen2.5:14b

# 실행
docker compose up -d
docker compose ps
```

대시보드: **http://localhost:8501**

---

## 사용법

### 대시보드 탭 구성

| 탭 | 역할 |
|----|------|
| **수신함** | 크롤링된 게시글 스코어 기반 추천 → 배치 승인/거절 |
| **편집실** | AI 대본 미리보기/수정, TTS 미리듣기, 제목·태그 편집 |
| **갤러리** | 저해상도 프리뷰 → 고화질 렌더링 → 업로드 스케줄링 |
| **분석** | YouTube 성과(조회수/유지율), 생산성 지표, AI 인사이트 |
| **설정** | 파이프라인 설정, 자동 승인 임계값, 자막 스타일 프리셋 |

### 영상 생성 흐름

```
수신함 승인 → 편집실(대본 수정) → LLM 요약(~30초) → TTS 생성(~20초)
→ 영상 렌더링(~1-2분) → 썸네일 생성 → 갤러리 확인 → 업로드
```

---

## 프로젝트 구조

```
WaggleBot/
├── CLAUDE.md                          # AI 개발 규칙 (코딩 컨벤션, 제약사항)
├── docker-compose.yml                 # GPU 환경
├── docker-compose.galaxybook.yml      # No-GPU 환경
├── arch/
│   ├── dev_spec.md                    # 1차 개발 명세
│   ├── next_spec.md                   # 2차 개발 명세
│   └── env/
│       ├── ENV_GPU.md                 # NVIDIA GPU 환경 상세 가이드
│       └── ENV_NOGPU.md              # CPU 환경 상세 가이드
├── crawlers/
│   ├── base.py                        # BaseCrawler (스코어링 포함)
│   ├── nate_pann.py / nate_tok.py
│   ├── plugin_manager.py
│   └── ADDING_CRAWLER.md
├── db/
│   ├── models.py                      # Post/Comment/Content + PostStatus
│   └── session.py
├── ai_worker/
│   ├── main.py                        # 폴링 메인 루프
│   ├── processor.py                   # asyncio 파이프라인 오케스트레이터
│   ├── llm.py                         # 쇼츠 대본 생성 (hook/body/closer JSON)
│   ├── video.py                       # FFmpeg 렌더러 (xfade, BGM 믹싱)
│   ├── subtitle.py                    # ASS 동적 자막
│   ├── thumbnail.py                   # 썸네일 자동 생성
│   ├── gpu_manager.py
│   └── tts/                           # edge_tts, kokoro, gptsovits
├── uploaders/
│   ├── base.py                        # BaseUploader
│   ├── youtube.py
│   └── uploader.py
├── analytics/
│   └── collector.py                   # YouTube Analytics 수집
├── assets/
│   ├── backgrounds/                   # 9:16 배경 영상
│   ├── fonts/
│   └── bgm/                           # funny/ serious/ shocking/ heartwarming/
├── config/
│   └── settings.py
├── monitoring/
│   ├── alerting.py
│   └── daemon.py
├── dashboard.py
├── scheduler.py
└── main.py                            # 크롤러 진입점
```

---

## 로그 확인

```bash
# 항상 --tail 옵션 사용 (토큰/메모리 낭비 방지)
docker compose logs --tail 50 ai_worker
docker compose logs --tail 50 crawler

# GPU 사용 현황
nvidia-smi
```
