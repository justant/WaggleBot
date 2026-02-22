# WaggleBot

> 커뮤니티 인기 게시글을 자동으로 수집하여 유튜브 쇼츠 영상으로 변환하는 AI 파이프라인

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://www.docker.com/)
[![GPU](https://img.shields.io/badge/GPU-NVIDIA%20RTX%203080%20Ti-76B900.svg)](https://www.nvidia.com/)

---

## 프로젝트 개요

커뮤니티 게시글을 크롤링하고, LLM으로 쇼츠 대본을 생성한 뒤, TTS와 FFmpeg로 9:16 영상을 자동 생성하는 완전 자동화 시스템.

### 주요 기능

- **자동 크롤링**: 네이트판·뽐뿌·DC인사이드·FM코리아 인기 게시글 수집 + 시간감쇠 인기도 스코어링
- **AI 대본**: Ollama LLM 기반 쇼츠 특화 3막 구조 대본 (hook/body/closer)
- **TTS**: Fish Speech 1.5 (zero-shot 음성 클로닝, 감정 태그 지원)
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
| LLM | Ollama (`qwen2.5:7b` / `qwen2.5:1.5b`) |
| TTS | Fish Speech 1.5 (zero-shot 클로닝, `fishaudio/fish-speech:v1.5.1`) |
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
ollama pull qwen2.5:7b
```

### 4. Fish Speech 모델 다운로드

```bash
# HuggingFace CLI 설치 (없는 경우)
pip install huggingface_hub

# 모델 다운로드 (~4GB)
bash scripts/download_fish_speech.sh
```

다운로드 후 구조 확인:
```
checkpoints/fish-speech-1.5/
├── model.pth
├── firefly-gan-vq-fsq-8x1024-21hz-generator.pth
└── (기타 config 파일)
```

### 참조 오디오 준비

Fish Speech는 **zero-shot 음성 클로닝** 방식입니다.
원하는 목소리의 WAV 파일을 준비하세요.

```
assets/voices/
└── korean_man_default.wav   ← 10~30초, 16kHz 이상, 깨끗한 음성
```

참조 텍스트를 `config/settings.py`의 `VOICE_REFERENCE_TEXTS`에 등록:
```python
VOICE_REFERENCE_TEXTS = {
    "default": "WAV 파일에서 실제로 말한 내용을 입력하세요.",
}
```

### 5. 프로젝트 설정 및 실행

```bash
git clone https://github.com/justant/WaggleBot.git
cd WaggleBot

# .env 최소 설정
nano .env
# DB_ROOT_PASSWORD=...
# DB_PASSWORD=...
# HF_TOKEN=hf_...
# OLLAMA_MODEL=qwen2.5:7b

# 실행
docker compose up -d
docker compose ps
```

대시보드: **http://localhost:8501**

### 6. DB 스키마 초기화 (최초 1회)

```bash
docker compose exec dashboard python -c "from db.session import init_db; init_db(); print('DB 초기화 완료')"
```

생성되는 테이블: `posts`, `comments`, `contents`

### 7. DB 마이그레이션 (스키마 변경 시)

신규 마이그레이션이 추가된 경우 아래 명령을 실행합니다.
이미 적용된 마이그레이션은 자동으로 건너뜁니다.

```bash
docker compose exec dashboard python -m db.migrations.runner
```

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
수신함 승인 → 편집실(대본 수정) → LLM 요약(~30초) → Fish Speech TTS(~10초)
→ 영상 렌더링(~1-2분) → 썸네일 생성 → 갤러리 확인 → 업로드
```

### TTS 음성 변경

`config/settings.py`의 `VOICE_PRESETS`에 WAV 파일 추가 후 등록:
```python
VOICE_PRESETS = {
    "default":  "korean_man_default.wav",
    "female":   "korean_female.wav",    # 추가 예시
}
VOICE_REFERENCE_TEXTS = {
    "default": "기존 참조 텍스트",
    "female":  "female 파일에서 실제 말한 내용",
}
```

---

## 프로젝트 구조

```
WaggleBot/
├── CLAUDE.md                          # AI 개발 규칙 (코딩 컨벤션, 제약사항)
├── docker-compose.yml                 # GPU 환경 (fish-speech 포함)
├── docker-compose.galaxybook.yml      # No-GPU 환경 (fish-speech 제외, TTS 무음)
├── arch/
│   ├── 4. llm_optimization.md         # LLM 최적화 5-Phase 파이프라인 (미실행)
│   ├── done/                          # 완료된 명세 (1~3, 5번)
│   └── env/
│       ├── ENV_GPU.md
│       └── ENV_NOGPU.md
├── crawlers/
│   ├── __init__.py                    # 크롤러 자동 등록 (explicit imports)
│   ├── base.py                        # BaseCrawler (공통 헬퍼 + retry + 스코어링)
│   ├── nate_pann.py                   # 네이트판
│   ├── bobaedream.py                  # 뽐뿌
│   ├── dcinside.py                    # DC인사이드
│   ├── fmkorea.py                     # FM코리아
│   ├── plugin_manager.py              # CrawlerRegistry (등록/조회)
│   └── ADDING_CRAWLER.md             # 신규 크롤러 추가 가이드
├── db/
│   ├── models.py                      # Post/Comment/Content/ScriptData + PostStatus
│   ├── session.py
│   └── migrations/
│       ├── runner.py                  # 통합 마이그레이션 러너 (schema_migrations 추적)
│       ├── 001_images_contents.sql
│       ├── 002_add_llm_logs.sql
│       └── 003_add_variant_fields.sql
├── ai_worker/
│   ├── main.py                        # 폴링 메인 루프 + Fish Speech 헬스체크
│   ├── processor.py                   # asyncio 파이프라인 오케스트레이터
│   ├── gpu_manager.py
│   ├── pipeline/                      # 5-Phase 콘텐츠 파이프라인
│   │   ├── content_processor.py       # Phase 1~5 통합 진입점
│   │   ├── resource_analyzer.py       # Phase 1: 이미지:텍스트 비율 분석
│   │   ├── llm_chunker.py             # Phase 2: LLM 의미 단위 청킹
│   │   ├── text_validator.py          # Phase 3: max_chars 검증 + 한국어 분할
│   │   └── scene_director.py          # Phase 4: 씬 배분 + 감정 태그 자동 할당
│   ├── llm/                           # LLM 호출 / 로깅
│   │   ├── client.py                  # 쇼츠 대본 생성 + call_ollama_raw()
│   │   └── logger.py                  # LLM 호출 이력 DB 저장
│   ├── tts/                           # TTS 엔진
│   │   ├── fish_client.py             # Fish Speech 1.5 HTTP 클라이언트
│   │   └── base.py / edge_tts.py / kokoro.py / gptsovits.py  # 레거시 엔진
│   └── renderer/                      # 영상 / 이미지 렌더링
│       ├── layout.py                  # FFmpeg 렌더러 (Figma 기반)
│       ├── video.py                   # 레거시 렌더러 (프리뷰용)
│       ├── subtitle.py                # ASS 동적 자막
│       └── thumbnail.py               # 썸네일 자동 생성
├── uploaders/
│   ├── base.py                        # BaseUploader + UploaderRegistry
│   ├── youtube.py                     # YouTube Shorts 업로더
│   ├── uploader.py
│   └── ADDING_UPLOADER.md            # 신규 업로더 추가 가이드
├── analytics/
│   ├── collector.py                   # YouTube Analytics 수집
│   └── feedback.py                    # 성과 기반 LLM 피드백 루프
├── assets/
│   ├── backgrounds/                   # 9:16 배경 영상
│   ├── fonts/
│   ├── bgm/                           # funny/ serious/ shocking/ heartwarming/
│   └── voices/                        # Fish Speech 참조 오디오 (WAV)
├── checkpoints/
│   └── fish-speech-1.5/               # Fish Speech 모델 파일
├── config/
│   ├── settings.py                    # 전역 설정 허브 (도메인별 모듈 re-export)
│   ├── crawler.py                     # 크롤러 설정 (USER_AGENTS, REQUEST_HEADERS 등)
│   ├── monitoring.py                  # 모니터링/알림 임계값
│   └── layout.json                    # 렌더러 레이아웃 제약 (Single Source of Truth)
├── scripts/
│   ├── setup_docker_gpu.sh
│   └── download_fish_speech.sh        # 모델 다운로드
├── test/
│   └── test_tts.py                    # Fish Speech 단독 테스트
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
docker compose logs --tail 50 fish-speech
docker compose logs --tail 50 crawler

# GPU 사용 현황
nvidia-smi
```

---

## 환경별 TTS 동작

| 환경 | Fish Speech | TTS 동작 |
|------|-------------|---------|
| GPU PC (`docker-compose.yml`) | 컨테이너 포함 | 정상 클로닝 생성 |
| 갤럭시북 (`docker-compose.galaxybook.yml`) | 제외 | 연결 실패 → 씬별 무음 처리 |

갤럭시북 환경에서 TTS 없이도 영상 렌더링은 정상 완료됩니다 (해당 씬 audio=None → 무음).
