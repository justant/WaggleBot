# 프로젝트: WaggleBot, 커뮤니티 쇼츠 팩토리 (단일 노드 / RTX 3080 Ti)

## 1. 프로젝트 개요 (Project Overview)
이 프로젝트는 인기 커뮤니티 게시글을 크롤링하고, 로컬 LLM을 사용하여 요약하며, 이를 오디오(TTS)로 변환한 뒤, 쇼츠 영상(9:16 비율)으로 생성하여 유튜브 등의 플랫폼에 자동 업로드하는 파이프라인입니다.

**하드웨어 환경:**
- **단일 노드:** NVIDIA RTX 3080 Ti (12GB VRAM) Windows PC. (WSL을 사용하여 Ubuntu 환경)
- **역할:** 크롤링, 데이터베이스 관리, 웹 대시보드 구동, AI 추론(LLM/TTS), 영상 렌더링, 업로드 등 모든 작업을 수행.
- **개발 환경:** Docker Compose (GPU 지원), Python 3.12.

---

## 2. 시스템 아키텍처 (System Architecture)

### 핵심 구성 요소 및 데이터 흐름
1.  **스케줄러 (Scheduler):** 1시간 간격으로 실행되는 Cron 기반 트리거.
2.  **수집기 (Crawler):** 커뮤니티 사이트에서 텍스트 및 **이미지(URL)** 수집 -> DB 저장.
3.  **관리자 (Manager/Dashboard):** Streamlit UI. 수집된 글 검수 및 상태 변경 (`COLLECTED` -> `APPROVED`).
4.  **AI 워커 (AI Worker):**
  - **Trigger:** **DB Polling 방식** (10초 주기, `APPROVED` 상태 감지). (Redis 사용 안 함)
  - **Process:** LLM 요약 -> TTS 생성 -> FFmpeg 렌더링.
  - **Storage:** 결과물은 로컬 공유 볼륨(`/app/media`)에 저장.

5.  **업로더 (Uploader):** 유튜브 등 각 플랫폼 API를 연동한 업로드 모듈.

### 인프라
- **Database:** MariaDB (11.x).
- **Storage:** Docker Volume (`./media`)을 통해 컨테이너 간 파일 공유.
### 데이터베이스 (MariaDB)
- **Table: Posts** (게시글 원본 데이터, 이미지)
- **Table: Comments** (베스트 댓글, 이미지 정보)
- **Table: Contents** (처리 완료된 영상 경로 및 메타데이터)
---

## 3. 상세 기능 요구사항 (Functional Requirements)

### [모듈 1] 크롤러 (확장성 고려 구조)
- **구조:** `BaseCrawler` 추상 클래스를 구현하여 확장성 확보.
  - `fetch_posts()`: 게시글 목록 가져오기
  - `parse_content()`: 세부 내용 파싱
  - `save_to_db()`: DB 저장
- **대상 :** 네이트판 (https://pann.nate.com/) - '톡톡' 베스트 및 '판' 베스트 섹션.
- **항목:** 제목, 본문, **이미지 리스트(JSON)**, 조회수, 추천수, 베스트 댓글.
- **로직:** - 중복 수집 시 통계/댓글만 업데이트 (Upsert).
  - 이미지가 포함된 게시글은 `has_image=True` 플래그 처리 (영상 제작 시 활용).
- **Upsert(등록/수정) 로직:**
  - **기존 글:** 이미 DB에 존재할 경우, 통계(추천/조회수)와 댓글 정보만 업데이트.
  - **신규 글:** 새로운 레코드를 생성하고 상태를 `COLLECTED`로 설정.
- **확장성:** 추후 Reddit, Blind 등 100개 이상의 사이트 추가를 고려한 설계 필수.

### [모듈 2] 관리자 대시보드 (Streamlit)
- **목적:** 수집된 콘텐츠를 운영자가 직접 확인하고 승인/거절.
- **UI 레이아웃:**
  - **Tab 1 (수신함):** `COLLECTED` 상태의 게시글 목록. 제목, 베스트 댓글 미리보기 제공.
    - `[승인(Accept)]` 버튼: 상태를 `APPROVED`로 변경 (AI 작업 큐로 이동).
    - `[거절(Decline)]` 버튼: 상태를 `DECLINED`로 변경.
  - **Tab 2 (진행 상태):** `PROCESSING` -> `RENDERED` -> `UPLOADED` 진행 상황 모니터링.
  - **Tab 3 (갤러리):** 최종 생성된 영상 재생 및 확인.
- **상태 관리 흐름:**
  - `COLLECTED` -> `APPROVED` -> `PROCESSING` -> `RENDERED` -> `UPLOADED`
- **Inbox:** 수집된 글 리스트 확인 (제목, 댓글, **이미지 유무** 표시).
- **Action:** [승인] 버튼 클릭 시 상태를 `APPROVED`로 변경.
- **Preview:** `RENDERED` 상태인 글의 최종 영상을 대시보드에서 재생 (공유 볼륨 활용).

### [모듈 3] AI 프로세싱 (RTX 3080 Ti 전용)
- **요약 (LLM):**
  - **모델:** `EEVE-Korean-10.8B` 또는 `Llama-3.1-8B-Instruct-Ko`.
  - **제약 사항:** TTS/OCR 등과 동시 실행을 위해 **4-bit 양자화(Quantization)** 필수 사용 (VRAM 최적화).
  - **작업:** 본문과 베스트 댓글을 포함하여 쇼츠 대본 스타일(200자 내외)로 요약.
- **음성 합성 (TTS):**
  - **모델:** 설정(`config`)에 따라 선택 가능 (`Kokoro-82M`, `GPT-SoVITS`, `Edge-TTS`).
  - **출력:** 요약된 스크립트와 싱크가 맞는 `.wav` 파일 생성.

### [모듈 4] 영상 생성 (FFmpeg 가속)
- **자산 (Assets):** `assets/backgrounds/` 폴더에 있는 9:16 비율의 배경 영상 사용.
- **로직:**
1.  **이미지 처리 (우선):** 만약 `images` 데이터가 존재하면, 해당 이미지들을 활용하여 **슬라이드 쇼(Ken Burns 효과 등)** 영상을 생성한다.
  2.  **배경 처리 (대체):** 이미지가 없다면, 준비된 배경 영상을 TTS 길이에 맞춰 반복(Loop)하거나 자름(Crop).
  3.  **자막 합성:** 요약된 텍스트를 화면 중앙에 배치 (가독성 높은 폰트 사용, 예: 맑은고딕/나눔고딕).
  4.  **오디오:** TTS 음성 + 배경음악(BGM) 믹싱 (BGM 볼륨 줄이기 적용).
  5.  **인코딩:** RTX 3080 Ti 가속을 위해 반드시 **`h264_nvenc` 코덱** 사용.


### [모듈 5] 업로더 (확장성 고려)
- **구조:** `BaseUploader` 추상 클래스 구현.
- **대상 (POC):** YouTube Shorts.
- **메타데이터:**
  - **제목:** 커뮤니티 원본 제목 활용.
  - **설명:** 요약 내용 + 원본 링크 + 해시태그.
  - **공개 설정:** 테스트 중에는 '비공개(Private)' 또는 '일부 공개(Unlisted)'.
- **확장성:** 추후 TikTok, Instagram Reels 등 10개 이상의 플랫폼 지원 예정.
---

## 4. 데이터베이스 스키마 (Updated)

MariaDB [wagglebot]> DESC posts;
+------------+----------------------------------------------------------------------------------+------+-----+---------------------+-------------------------------+
| Field      | Type                                                                             | Null | Key | Default             | Extra                         |
+------------+----------------------------------------------------------------------------------+------+-----+---------------------+-------------------------------+
| id         | bigint(20)                                                                       | NO   | PRI | NULL                | auto_increment                |
| site_code  | varchar(32)                                                                      | NO   | MUL | NULL                |                               |
| origin_id  | varchar(64)                                                                      | NO   | UNI | NULL                |                               |
| title      | varchar(512)                                                                     | NO   |     | NULL                |                               |
| content    | text                                                                             | YES  |     | NULL                |                               |
| images     | longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin                               | YES  |     | NULL                | CHECK (json_valid(`images`))  |
| stats      | longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin                               | YES  |     | NULL                | CHECK (json_valid(`stats`))   |
| status     | enum('COLLECTED','APPROVED','PROCESSING','RENDERED','UPLOADED','DECLINED')       | NO   | MUL | COLLECTED           |                               |
| created_at | datetime                                                                         | NO   |     | current_timestamp() |                               |
| updated_at | datetime                                                                         | NO   |     | current_timestamp() | on update current_timestamp() |
+------------+----------------------------------------------------------------------------------+------+-----+---------------------+-------------------------------+

MariaDB [wagglebot]> DESC comments;
+--------------+--------------+------+-----+---------+----------------+
| Field        | Type         | Null | Key | Default | Extra          |
+--------------+--------------+------+-----+---------+----------------+
| id           | bigint(20)   | NO   | PRI | NULL    | auto_increment |
| post_id      | bigint(20)   | NO   | MUL | NULL    |                |
| author       | varchar(128) | NO   |     | NULL    |                |
| content      | text         | NO   |     | NULL    |                |
| content_hash | varchar(64)  | NO   |     | NULL    |                |
| likes        | int(11)      | NO   |     | 0       |                |
+--------------+--------------+------+-----+---------+----------------+

MariaDB [wagglebot]> DESC contents;
+--------------+----------------------------------------------------+------+-----+---------------------+------------------------------------+
| Field        | Type                                               | Null | Key | Default             | Extra                              |
+--------------+----------------------------------------------------+------+-----+---------------------+------------------------------------+
| id           | bigint(20)                                         | NO   | PRI | NULL                | auto_increment                     |
| post_id      | bigint(20)                                         | NO   | UNI | NULL                |                                    |
| summary_text | text                                               | YES  |     | NULL                |                                    |
| audio_path   | varchar(255)                                       | YES  |     | NULL                |                                    |
| video_path   | varchar(255)                                       | YES  |     | NULL                |                                    |
| upload_meta  | longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin | YES  |     | NULL                | CHECK (json_valid(`upload_meta`))  |
| created_at   | datetime                                           | NO   |     | current_timestamp() |                                    |
+--------------+----------------------------------------------------+------+-----+---------------------+------------------------------------+