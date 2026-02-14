# 프로젝트: WaggleBot, 커뮤니티 쇼츠 팩토리 (단일 노드 / RTX 3080 Ti)

## 1. 프로젝트 개요 (Project Overview)
이 프로젝트는 인기 커뮤니티 게시글을 크롤링하고, 로컬 LLM을 사용하여 요약하며, 이를 오디오(TTS)로 변환한 뒤, 쇼츠 영상(9:16 비율)으로 생성하여 유튜브 등의 플랫폼에 자동 업로드하는 파이프라인입니다.

**하드웨어 환경:**
- **단일 노드 (Single Node):** NVIDIA RTX 3080 Ti (12GB VRAM)가 장착된 Windows PC.
- **역할:** 크롤링, 데이터베이스 관리, 웹 대시보드 구동, AI 추론(LLM/TTS), 영상 렌더링, 업로드 등 모든 작업을 수행.
- **개발 환경:** Python 3.10+ (WSL2 또는 Windows Native 환경, GPU 사용 필수).

---

## 2. 시스템 아키텍처 (System Architecture)

### 핵심 구성 요소
1.  **스케줄러 (Scheduler):** 1시간 간격으로 실행되는 Cron 기반 트리거.
2.  **수집기 (Crawler):** 커뮤니티 사이트에서 데이터를 가져오는 모듈.
3.  **관리자 (Manager/Dashboard):** 수집된 데이터를 사람이 검수(Accept/Decline)하는 Streamlit 기반 UI.
4.  **AI 엔진 (AI Engine):** RTX 3080 Ti를 활용한 로컬 LLM(요약) 및 TTS(음성 생성).
5.  **영상 엔진 (Video Engine):** FFmpeg와 NVENC 하드웨어 가속을 활용한 영상 합성.
6.  **업로더 (Uploader):** 각 플랫폼 API를 연동한 업로드 모듈.

### 데이터베이스 (SQLite 또는 PostgreSQL)
- **Table: Posts** (게시글 원본 데이터, 통계)
- **Table: Comments** (베스트 댓글 정보)
- **Table: Contents** (처리 완료된 영상 경로 및 메타데이터)

---

## 3. 상세 기능 요구사항 (Functional Requirements)

### [모듈 1] 스케줄러 및 트리거
- **실행:** `apscheduler` 또는 시스템 스케줄러를 통해 매시간 정각 실행.
- **로직:** 신규 게시글 수집 트리거. 만약 이전 배치가 아직 처리 중이라면, 시스템 부하에 따라 대기하거나 스킵하는 로직 포함.

### [모듈 2] 크롤러 (확장성 고려 구조)
- **구조:** `BaseCrawler` 추상 클래스를 구현하여 확장성 확보.
    - `fetch_posts()`: 게시글 목록 가져오기
    - `parse_content()`: 세부 내용 파싱
    - `save_to_db()`: DB 저장
- **대상 (POC):** 네이트판 (https://pann.nate.com/) - '톡톡' 베스트 및 '판' 베스트 섹션.
- **수집 항목:** 제목, 본문(텍스트/이미지), 조회수, 추천수, 댓글 수, 베스트 댓글(작성자, 내용, 추천수).
- **Upsert(등록/수정) 로직:**
    - **기존 글:** 이미 DB에 존재할 경우, 통계(추천/조회수)와 댓글 정보만 업데이트.
    - **신규 글:** 새로운 레코드를 생성하고 상태를 `COLLECTED`로 설정.
- **확장성:** 추후 Reddit, Blind 등 100개 이상의 사이트 추가를 고려한 설계 필수.

### [모듈 3] 관리자 대시보드 (Streamlit)
- **목적:** 수집된 콘텐츠를 운영자가 직접 확인하고 승인/거절.
- **UI 레이아웃:**
    - **Tab 1 (수신함):** `COLLECTED` 상태의 게시글 목록. 제목, 베스트 댓글 미리보기 제공.
        - `[승인(Accept)]` 버튼: 상태를 `APPROVED`로 변경 (AI 작업 큐로 이동).
        - `[거절(Decline)]` 버튼: 상태를 `DECLINED`로 변경.
    - **Tab 2 (진행 상태):** `PROCESSING` -> `RENDERED` -> `UPLOADED` 진행 상황 모니터링.
    - **Tab 3 (갤러리):** 최종 생성된 영상 재생 및 확인.
- **상태 관리 흐름:**
    - `COLLECTED` -> `APPROVED` -> `PROCESSING` -> `RENDERED` -> `UPLOADED`

### [모듈 4] AI 프로세싱 (RTX 3080 Ti 전용)
- **요약 (LLM):**
    - **모델:** `EEVE-Korean-10.8B` 또는 `Llama-3.1-8B-Instruct-Ko`.
    - **제약 사항:** TTS/OCR 등과 동시 실행을 위해 **4-bit 양자화(Quantization)** 필수 사용 (VRAM 최적화).
    - **작업:** 본문과 베스트 댓글을 포함하여 쇼츠 대본 스타일(200자 내외)로 요약.
- **음성 합성 (TTS):**
    - **모델:** 설정(`config`)에 따라 선택 가능 (`Kokoro-82M`, `GPT-SoVITS`, `Edge-TTS`).
    - **출력:** 요약된 스크립트와 싱크가 맞는 `.wav` 파일 생성.

### [모듈 5] 영상 생성 (FFmpeg 가속)
- **자산 (Assets):** `assets/backgrounds/` 폴더에 있는 9:16 비율의 배경 영상 사용.
- **로직:**
    1.  **배경 처리:** TTS 오디오 길이에 맞춰 배경 영상을 반복(Loop)하거나 자름(Crop).
    2.  **자막 합성:** 요약된 텍스트를 화면 중앙에 배치 (가독성 높은 폰트 사용, 예: 맑은고딕/나눔고딕).
    3.  **인코딩:** RTX 3080 Ti 가속을 위해 반드시 **`h264_nvenc` 코덱** 사용.
    4.  **오디오:** TTS 음성 + 배경음악(BGM) 믹싱 (BGM 볼륨 줄이기 적용).

### [모듈 6] 업로더 (확장성 고려)
- **구조:** `BaseUploader` 추상 클래스 구현.
- **대상 (POC):** YouTube Shorts.
- **메타데이터:**
    - **제목:** 커뮤니티 원본 제목 활용.
    - **설명:** 요약 내용 + 원본 링크 + 해시태그.
    - **공개 설정:** 테스트 중에는 '비공개(Private)' 또는 '일부 공개(Unlisted)'.
- **확장성:** 추후 TikTok, Instagram Reels 등 10개 이상의 플랫폼 지원 예정.

---

## 4. 데이터베이스 스키마 (Database Schema)

### `posts` (게시글)
| 컬럼명 | 타입 | 설명 |
| :--- | :--- | :--- |
| `id` | Integer | PK |
| `site_code` | String | 예: 'NATE', 'REDDIT' |
| `origin_id` | String | 원본 사이트의 게시글 ID (중복 방지) |
| `title` | String | 제목 |
| `content` | Text | 본문 내용 |
| `stats` | JSON | `{"views": 0, "likes": 0}` |
| `status` | String | `COLLECTED`, `APPROVED` 등 |
| `created_at` | DateTime | 수집 일시 |

### `comments` (댓글)
| 컬럼명 | 타입 | 설명 |
| :--- | :--- | :--- |
| `id` | Integer | PK |
| `post_id` | Integer | FK (posts 테이블 참조) |
| `author` | String | 작성자 |
| `content` | Text | 댓글 내용 |
| `likes` | Integer | 추천수 |

---

## 5. 기술 스택 권장 사항
- **언어:** Python 3.10 이상
- **웹 프레임워크 (관리자):** Streamlit
- **데이터베이스:** SQLite (POC용) 또는 PostgreSQL
- **작업 큐:** Celery + Redis (AI 작업 순차 처리를 위해 권장)
- **영상 처리:** `moviepy` (내부적으로 `ffmpeg` 사용)
- **AI/ML:** `torch`, `transformers`, `bitsandbytes` (양자화용), `scikit-learn`

---

## 6. Claude 구현 요청 단계 (Implementation Steps)
1.  **환경 설정 (Setup):** 프로젝트 폴더 구조 생성 및 `requirements.txt` 작성.
2.  **크롤러 구현 (Crawler):** `NatePannCrawler` 클래스 및 DB 저장 로직 구현.
3.  **관리자 페이지 (Admin):** Streamlit을 이용해 수집된 글을 확인하고 승인하는 UI 구축.
4.  **AI 파이프라인 (AI Pipeline):** LLM 요약 및 TTS 생성 스크립트 작성 (3080 Ti 활용).
5.  **영상 파이프라인 (Video Pipeline):** NVENC를 활용한 FFmpeg 렌더링 스크립트 작성.
6.  **통합 (Integration):** [승인] 버튼 클릭 시 AI 및 영상 생성 작업이 트리거되도록 연결.
