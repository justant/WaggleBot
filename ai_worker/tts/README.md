# ai_worker/tts — TTS 모듈

텍스트를 음성으로 변환하는 모듈. Fish Speech 1.5 zero-shot 클로닝(메인)과
Edge-TTS(폴백)를 지원하며, 한국어 인터넷 텍스트 정규화·발음 교정·숫자 읽기 변환을 포함한다.

---

## 파일 구조

```
ai_worker/tts/
├── __init__.py      # 엔진 레지스트리 + FishSpeechTTS 어댑터
├── base.py          # BaseTTS 추상 클래스
├── edge_tts.py      # Edge-TTS 구현체
├── fish_client.py   # Fish Speech HTTP 클라이언트 + 텍스트 정규화
└── README.md        # (이 파일)
```

---

## 퍼블릭 API

### __init__.py

| 심볼 | 타입 | 용도 |
|------|------|------|
| `BaseTTS` | 클래스 (re-export) | TTS 엔진 추상 인터페이스 |
| `EdgeTTS` | 클래스 (re-export) | Edge-TTS 구현체 |
| `FishSpeechTTS` | 클래스 | Fish Speech BaseTTS 어댑터 |
| `TTS_ENGINES` | dict | `{"fish-speech": FishSpeechTTS, "edge-tts": EdgeTTS}` |
| `get_tts_engine(name)` | 함수 | 이름으로 엔진 인스턴스 반환 |

### fish_client.py

| 심볼 | 용도 |
|------|------|
| `synthesize()` | Fish Speech TTS 생성 (메인 API) |
| `_warmup_model()` | 모델 + 음성 클로닝 웜업 (3회) |
| `wait_for_fish_speech()` | 서버 기동 대기 + 웜업 |

---

## 엔진 비교

| 항목 | Fish Speech 1.5 | Edge-TTS |
|------|----------------|----------|
| 방식 | HTTP API (fish-speech 컨테이너) | Microsoft Edge 클라우드 |
| 음성 복제 | zero-shot 클로닝 (참조 WAV) | 사전 정의 voice_id |
| 감정 톤 | 미지원 (파라미터 무시) | 미지원 |
| 후처리 | 무음 단축 + 1.2배속 (FFmpeg) | 없음 |
| GPU 사용 | ~5GB VRAM | 없음 |
| 의존성 | httpx, FFmpeg | edge-tts 패키지 |

---

## base.py — BaseTTS 추상 클래스

```python
class BaseTTS(ABC):
    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice_id: str,
        output_path: Path,
        emotion: str = "",
    ) -> Path: ...
```

- 모든 TTS 엔진이 구현해야 할 인터페이스
- `emotion` 파라미터: 감정 톤 키. 미지원 엔진은 무시

---

## edge_tts.py — Edge-TTS 구현체

```python
class EdgeTTS(BaseTTS):
    def __init__(self, rate: str = "+0%") -> None: ...
    async def synthesize(self, text, voice_id, output_path, emotion="") -> Path: ...
```

- `edge_tts.Communicate(text, voice_id, rate)` → `.save()` 호출
- `emotion` 파라미터는 무시 (Edge-TTS 미지원)
- 간단한 래퍼 — 별도 전처리/후처리 없음

---

## fish_client.py — 상세 구조

### 의존성

```python
from config.settings import (
    FISH_SPEECH_URL,                  # Fish Speech 서버 URL
    FISH_SPEECH_TIMEOUT,              # HTTP 타임아웃 (초)
    FISH_SPEECH_TEMPERATURE,          # 생성 temperature
    FISH_SPEECH_REPETITION_PENALTY,   # 반복 패널티
    TTS_OUTPUT_FORMAT,                # 출력 포맷 (wav)
    VOICE_DEFAULT,                    # 기본 음성 키
    VOICE_PRESETS,                    # {키: 참조 WAV 파일명}
    VOICE_REFERENCE_TEXTS,            # {키: 참조 텍스트}
)
```

- **HTTP 클라이언트**: `httpx.AsyncClient`
- **동시성 제어**: `threading.Lock` (`_FISH_SPEECH_LOCK`) — 단일 GPU 모델 직렬화
- **선택적 라이브러리**: `soynlp` (반복 자모 정규화, 미설치 시 폴백)

### synthesize() — 메인 TTS 생성

```python
async def synthesize(
    text: str,
    scene_type: str = "img_text",    # 로깅용 씬 타입
    voice_key: str = VOICE_DEFAULT,  # VOICE_PRESETS 키
    output_path: Path | None = None, # None → /tmp 임시파일
    emotion: str = "",               # 감정 톤 (미지원, 무시)
) -> Path:
```

**실행 흐름:**

```
1. 첫 호출 시 자동 웜업 (_warmup_model)
2. _normalize_for_tts(text) — 텍스트 정규화
3. 빈 텍스트 가드 (자음만 등 → ValueError)
4. 참조 오디오 로드 (assets/voices/ → base64 인코딩)
5. _FISH_SPEECH_LOCK 획득 (직렬화)
6. HTTP POST /v1/tts (최대 3회 재시도)
   ├─ ReadTimeout → 즉시 재시도 (최대 2회)
   └─ 품질 검증 실패 → 재생성 (최대 2회)
7. 품질 검증 — 오디오 길이/문자수 비율 ≤ 0.35초/자
   ├─ 통과 → 채택
   ├─ 실패 → 재생성, 이전 최적 결과 보관
   └─ 최종 실패 → 이전 최적 결과 사용
8. _FISH_SPEECH_LOCK 해제
9. _post_process_audio() — 무음 단축 + 1.2배속
```

**재시도 전략:**

| 계층 | 재시도 | 조건 |
|------|--------|------|
| HTTP 통신 | 최대 3회 | `ReadTimeout` |
| 품질 검증 | 최대 3회 | `secs_per_char > 0.35` (중국어/일본어 혼입 의심) |

**품질 검증 알고리즘:**

```
audio_secs = (WAV바이트 - 44) / (44100 × 2)  (16bit mono)
secs_per_char = audio_secs / len(text)
임계값 = 0.35초/자 (한국어 발화 속도 ~3~6자/초 기준)

초과 시 → 중국어/일본어 혼입으로 판단 → 재생성
최종 실패 시 → 이전 시도 중 가장 낮은 ratio의 결과 사용
```

### _normalize_for_tts() — 텍스트 정규화 파이프라인

```
0. 화자 접두어 제거          "ㅇㅇ: 텍스트" → "텍스트"
1. 인터넷 축약어 치환        "남친" → "남자친구" (긴 키워드 우선)
1-1. 조사 자동 교정          "남자친구과" → "남자친구와" (받침 변경 대응)
1-2. soynlp 반복 정규화      "ㅋㅋㅋㅋ" → "ㅋ" (삭제 전 통일)
2. 자모 이모티콘 제거        반복 자모, 단독 자모, ^ 기호
3. 숫자 → 한국어 읽기        "3살" → "세 살", "100원" → "백 원"
3-1. 발음 교정 사전 적용     "댓글" → "대끌" (경음화)
4. 특수문자 정리             중국어 구두점, 물결표, 따옴표
5. 문장 완성                 마침표 없으면 추가 (프로소디 안정화)
```

### 숫자 읽기 시스템

| 함수 | 역할 | 예시 |
|------|------|------|
| `_sino_number()` | 한자어 수사 (4자리씩 만/억/조) | `12345` → `"만이천삼백사십오"` |
| `_native_number()` | 고유어 수사 (1~99) | `3` → `"세"`, `25` → `"스물다섯"` |
| `_convert_number_with_counter()` | 숫자+단위 조합 | `"3살"` → `"세 살"` |
| `_convert_standalone_number()` | 단독 숫자 | `"100"` → `"백"` |

**단위별 수사 분류:**

| 분류 | 단위 | 수사 |
|------|------|------|
| 고유어 (`_NATIVE_COUNTERS`) | 살, 세, 명, 개, 시, 잔, 번, 마리, 벌, 켤레 | 한, 두, 세, 네, 다섯... |
| 한자어 (`_SINO_COUNTERS`) | 년, 월, 일, 원, 호, 층, 도, 분, 초, km, kg 등 | 일, 이, 삼, 사, 오... |

### 발음 교정 시스템

Fish Speech 내장 G2P가 처리 못하는 한국어 음운 규칙을 사전 변환.

| 음운 규칙 | 예시 |
|----------|------|
| 경음화 | `댓글→대끌`, `맛집→마찝`, `있다→읻따` |
| 비음화 | `작년→장년`, `국민→궁민`, `합니다→함니다` |
| 유음화 | `관련→괄련`, `연락→열락` |
| 구개음화 | `같이→가치`, `굳이→구지` |
| ㅎ 약화/탈락 | `좋아→조아`, `않다→안타`, `많이→마니` |
| 겹받침 | `읽다→익따`, `삶→삼`, `젊은→절문` |

**사전 로드 우선순위:**
1. `assets/pronunciation_map.json` (외부 파일 — 내장 사전 위에 덮어씀)
2. `_PRONUNCIATION_MAP_BUILTIN` (내장 ~40항목)

### 조사 자동 교정 (`_fix_particles`)

축약어 치환 후 받침이 달라져 조사가 맞지 않는 경우 자동 교정.

| 조사 쌍 | 받침 있음 | 받침 없음 | ㄹ 받침 예외 |
|---------|----------|----------|-------------|
| 과/와 | 과 | 와 | — |
| 은/는 | 은 | 는 | — |
| 이/가 | 이 | 가 | — |
| 을/를 | 을 | 를 | — |
| 으로/로 | 으로 | 로 | ㄹ받침 → 로 |
| 아/야 | 아 | 야 | — |

### 축약어 사전

**로드 우선순위:**
1. `assets/slang_map.json` (외부 파일)
2. `_SLANG_MAP_BUILTIN` (내장 ~20항목)

**내장 사전 예시:**

| 축약어 | 변환 |
|--------|------|
| 남친 | 남자친구 |
| 베댓 | 베스트 댓글 |
| 갑분싸 | 갑자기 분위기 싸해짐 |
| ㄹㅇ | 진짜 |
| TMI | 티엠아이 |
| ㄷㄷ | (삭제) |

### 오디오 후처리 (`_post_process_audio`)

```
FFmpeg 필터 체인:
  1. atrim        — 프라이머 구간 제거 (현재 비활성화, _TTS_PRIMER="")
  2. silenceremove — 단어 사이 200ms 이상 무음 제거 (threshold=-50dB)
  3. atempo=1.2   — WSOLA 알고리즘 피치 보존 1.2배속

ffmpeg 미설치 시 조용히 건너뜀.
```

### 모델 웜업 (`_warmup_model`)

```
Fish Speech 모델 lazy-load 대응:
  1회: "안녕하세요." — 모델 로드 트리거 (중국어 출력 흡수)
  2회: "테스트 문장입니다." — voice cloning 안정화
  3회: "오늘도 좋은 하루 되세요." — 한국어 컨디셔닝 강화

_FISH_SPEECH_LOCK 내부에서 실행 (직렬화)
synthesize() 첫 호출 시 자동 실행 (_warmup_done 플래그)
```

### 서버 기동 대기 (`wait_for_fish_speech`)

```python
async def wait_for_fish_speech(retries: int = 10, delay: float = 5.0) -> bool:
```
- `GET {FISH_SPEECH_URL}/` 으로 연결 확인 (최대 retries회, delay초 간격)
- 연결 성공 후 `_warmup_model()` 호출
- `ai_worker/main.py` 시작 시 호출

### 동시성 제어

```
_FISH_SPEECH_LOCK (threading.Lock)
  ├─ Fish Speech 단일 GPU 모델 → 동시 요청 불가
  ├─ render_stage(스레드 풀) + llm_tts_stage(메인 루프) 동시 접근 방지
  ├─ run_in_executor로 블로킹 락 획득을 이벤트 루프 밖으로 위임
  └─ synthesize() / _warmup_model() 모두 이 락 사용
```

### 주요 유틸리티 함수

| 함수 | 역할 |
|------|------|
| `_load_slang_map()` | assets/slang_map.json 로드 (없으면 내장 사전) |
| `_load_pronunciation_map()` | assets/pronunciation_map.json 로드 (내장 + 외부 병합) |
| `_sino_number()` | 정수 → 한자어 수사 (만/억/조 처리) |
| `_native_number()` | 1~99 → 고유어 수사 (100 이상 한자어 폴백) |
| `_has_jongseong()` | 받침 유무 확인 (유니코드 연산) |
| `_has_rieul_jongseong()` | ㄹ 받침 여부 확인 |
| `_fix_particles()` | 받침 기반 조사 자동 교정 (6쌍) |
| `_convert_number_with_counter()` | 숫자+단위 → 한국어 수사+단위 |
| `_convert_standalone_number()` | 단독 숫자 → 한자어 수사 |
| `_normalize_for_tts()` | TTS 전처리 파이프라인 (5단계) |
| `_post_process_audio()` | FFmpeg 후처리 (무음 단축 + 1.2배속) |

### 상수

| 상수 | 값 | 용도 |
|------|-----|------|
| `VOICES_DIR` | `assets/voices/` | 참조 음성 WAV 디렉터리 |
| `_SLANG_MAP_PATH` | `assets/slang_map.json` | 축약어 사전 경로 |
| `_PRONUNCIATION_MAP_PATH` | `assets/pronunciation_map.json` | 발음 교정 사전 경로 |
| `_TTS_PRIMER` | `""` (비활성화) | 첫 음절 garbling 방지 프라이머 |
| `_TTS_PRIMER_TRIM_SECS` | `0.0` | 프라이머 트림 시간 |
| `_MAX_SECS_PER_CHAR` | `0.35` | 품질 검증 임계값 (초/자) |
| `_MAX_QUALITY_RETRIES` | `2` | 품질 검증 재시도 횟수 |
| `_MAX_TTS_RETRIES` | `2` | HTTP 재시도 횟수 |
| `_FISH_SPEECH_LOCK` | `threading.Lock` | 단일 GPU 직렬화 락 |
| `_warmup_done` | `bool` | 웜업 완료 플래그 |

---

## 호출 흐름도

### 파이프라인 Phase 5 (content_processor)

```
content_processor.process_content()
  → synthesize(text, scene_type, voice_key, output_path)
      ├─ _normalize_for_tts(text)
      │   ├─ 화자 접두어 제거
      │   ├─ _SLANG_MAP 치환
      │   ├─ _fix_particles() 조사 교정
      │   ├─ soynlp 반복 정규화
      │   ├─ 자모/이모티콘 제거
      │   ├─ 숫자 → 한국어 (_sino_number / _native_number)
      │   ├─ _PRONUNCIATION_MAP 발음 교정
      │   ├─ 특수문자 정리
      │   └─ 마침표 추가
      ├─ 참조 오디오 base64 인코딩
      ├─ _FISH_SPEECH_LOCK 획득
      ├─ POST /v1/tts (재시도 + 품질 검증)
      ├─ _FISH_SPEECH_LOCK 해제
      └─ _post_process_audio() (무음 단축 + 1.2배속)
```

### 레이아웃 렌더러 (layout.py)

```
render_layout_video_from_scenes()
  → _warmup_model()    — Fish Speech 모델 재웜업
  → _generate_tts_chunks()
      → _tts_chunk_async(text, idx, output_dir, ...)
          ├─ 사전 생성 오디오 있음 → shutil.copy2 (재사용)
          └─ 없음 → fish_synthesize(text, ...) (2회 재시도)
```

### 서버 시작

```
ai_worker/main.py
  → wait_for_fish_speech(retries=10, delay=5.0)
      ├─ GET {FISH_SPEECH_URL}/ 연결 확인
      └─ _warmup_model() (3회 TTS 호출)
```

---

## 외부 사용처

### 파이프라인 코어

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `ai_worker/processor.py` | `get_tts_engine` | TTS 엔진 인스턴스 조회 (cfg 기반) |
| `ai_worker/pipeline/content_processor.py` | `synthesize` | Phase 5 TTS 생성 |
| `ai_worker/main.py` | `wait_for_fish_speech` | 서버 기동 대기 + 웜업 |
| `ai_worker/renderer/layout.py` | `synthesize`, `_warmup_model` | 레이아웃 렌더러 TTS 청크 생성 |

### 테스트

| 파일 | import 대상 |
|------|-------------|
| `test/test_full_pipeline_e2e.py` | `_warmup_model`, `synthesize` |
| `test/test_pipeline_phases.py` | `synthesize` |
| `test/test_tts.py` | `EdgeTTS` |
| `test/test_fish_speech.py` | `_normalize_for_tts` |

---

## 설정 참조 (config/settings.py)

| 설정 | 기본값 | 용도 |
|------|--------|------|
| `FISH_SPEECH_URL` | `http://fish-speech:8080` | Fish Speech 서버 URL |
| `FISH_SPEECH_TIMEOUT` | `120` | HTTP 타임아웃 (초) |
| `FISH_SPEECH_TEMPERATURE` | `0.7` | 생성 temperature |
| `FISH_SPEECH_REPETITION_PENALTY` | `1.2` | 반복 패널티 |
| `TTS_OUTPUT_FORMAT` | `wav` | 출력 포맷 |
| `VOICE_DEFAULT` | `"default"` | 기본 음성 프리셋 키 |
| `VOICE_PRESETS` | `{키: WAV 파일명}` | 음성 프리셋 매핑 |
| `VOICE_REFERENCE_TEXTS` | `{키: 참조 텍스트}` | 음성 클로닝 참조 텍스트 |

---

## 에러 처리 전략

| 계층 | 전략 |
|------|------|
| 텍스트 정규화 후 빈 문자열 | `ValueError` raise → 호출자에서 해당 씬 스킵 |
| HTTP ReadTimeout | 즉시 재시도 (최대 3회), 최종 실패 시 raise |
| 품질 검증 실패 | 재생성 (최대 3회), 이전 최적 결과 보관/사용 |
| FFmpeg 후처리 실패 | warning 로그, 원본 유지 (파이프라인 계속) |
| ffmpeg 미설치 | 후처리 건너뜀 (조용히) |
| soynlp 미설치 | 반복 정규화 건너뜀 |
| 참조 오디오 없음 | 기본 음성 폴백 (zero-shot 클로닝 없이) |
| slang_map.json 로드 실패 | 내장 사전 사용 |
| pronunciation_map.json 로드 실패 | 내장 사전만 사용 |
| 웜업 실패 | warning 로그, 무시 (synthesize에서 재시도) |

---

## 에셋 디렉터리

| 경로 | 용도 |
|------|------|
| `assets/voices/` | 참조 음성 WAV 파일 (zero-shot 클로닝) |
| `assets/slang_map.json` | 인터넷 축약어 → 표준어 사전 |
| `assets/pronunciation_map.json` | 맞춤법 표기 → 발음형 교정 사전 |
