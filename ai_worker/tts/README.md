# ai_worker/tts — TTS 모듈

텍스트를 음성으로 변환하는 모듈. Fish Speech 1.5 zero-shot 클로닝을 사용하며,
한국어 인터넷 텍스트 정규화, 발음 교정, 숫자 읽기 변환을 포함한다.

---

## 파일 구조

```
ai_worker/tts/
├── __init__.py        # 퍼블릭 API re-export (2줄)
├── fish_client.py     # Fish Speech HTTP 클라이언트 (329줄)
├── normalizer.py      # TTS 텍스트 정규화 파이프라인 (245줄)
├── number_reader.py   # 숫자 → 한국어 읽기 변환 (97줄)
└── settings.yaml      # 도메인별 설정
```

---

## 퍼블릭 API

`__init__.py`에서 re-export:

```python
from ai_worker.tts.fish_client import synthesize, wait_for_fish_speech, _warmup_model  # noqa: F401
from ai_worker.tts.normalizer import normalize_for_tts  # noqa: F401
```

---

## 모듈 상세

### fish_client.py (329줄)

Fish Speech HTTP API 클라이언트. TTS 생성, 품질 검증, 오디오 후처리를 수행.

**의존성:**

```python
from config.settings import (
    FISH_SPEECH_URL, FISH_SPEECH_TIMEOUT,
    FISH_SPEECH_TEMPERATURE, FISH_SPEECH_REPETITION_PENALTY,
    TTS_OUTPUT_FORMAT, VOICE_DEFAULT, VOICE_PRESETS, VOICE_REFERENCE_TEXTS,
)
from ai_worker.tts.normalizer import normalize_for_tts
```

**퍼블릭 함수:**

| 함수 | 역할 |
|------|------|
| `synthesize(text, scene_type, voice_key, output_path, emotion)` | Fish Speech TTS 생성 (메인 API) |
| `wait_for_fish_speech(retries, delay)` | 서버 기동 대기 + 웜업 |
| `_warmup_model()` | 모델 + 음성 클로닝 웜업 (3회) |

#### synthesize() 실행 흐름

```
1. 첫 호출 시 자동 웜업 (_warmup_model)
2. normalize_for_tts(text) — 텍스트 정규화
3. 빈 텍스트 가드 (자음만 등 → ValueError)
4. 참조 오디오 로드 (assets/voices/ → base64 인코딩)
5. _FISH_SPEECH_LOCK 획득 (직렬화)
6. HTTP POST /v1/tts (최대 3회 재시도)
   ├─ ReadTimeout → 즉시 재시도
   └─ 품질 검증 실패 → 재생성
7. 품질 검증 — 오디오 길이/문자수 비율 ≤ 0.35초/자
8. _FISH_SPEECH_LOCK 해제
9. _post_process_audio() — 무음 단축 + 1.2배속
```

#### 재시도 전략

| 계층 | 재시도 | 조건 |
|------|--------|------|
| HTTP 통신 | 최대 3회 | `ReadTimeout` |
| 품질 검증 | 최대 3회 | `secs_per_char > 0.35` |

#### 품질 검증 알고리즘

```
audio_secs = (WAV바이트 - 44) / (44100 × 2)
secs_per_char = audio_secs / len(text)
임계값 = 0.35초/자

초과 → 중국어/일본어 혼입 판단 → 재생성
최종 실패 → 이전 시도 중 최저 ratio 결과 사용
```

#### 오디오 후처리 (`_post_process_audio`)

```
FFmpeg 필터 체인:
  1. silenceremove — 200ms 이상 무음 제거 (threshold=-50dB)
  2. atempo=1.2   — WSOLA 피치 보존 1.2배속
```

#### 동시성 제어

```
_FISH_SPEECH_LOCK (threading.Lock)
  ├─ Fish Speech 단일 GPU 모델 → 동시 요청 불가
  ├─ run_in_executor로 블로킹 락 획득
  └─ synthesize() / _warmup_model() 모두 사용
```

#### 모델 웜업 (`_warmup_model`)

```
3회 순차 TTS 호출:
  1회: "안녕하세요." — 모델 로드 트리거
  2회: "테스트 문장입니다." — voice cloning 안정화
  3회: "오늘도 좋은 하루 되세요." — 한국어 컨디셔닝
```

#### 상수

| 상수 | 값 | 용도 |
|------|-----|------|
| `VOICES_DIR` | `assets/voices/` | 참조 음성 WAV 디렉터리 |
| `_MAX_SECS_PER_CHAR` | `0.35` | 품질 검증 임계값 |
| `_MAX_QUALITY_RETRIES` | `2` | 품질 검증 재시도 횟수 |
| `_MAX_TTS_RETRIES` | `2` | HTTP 재시도 횟수 |
| `_FISH_SPEECH_LOCK` | `threading.Lock` | 단일 GPU 직렬화 락 |

---

### normalizer.py (245줄)

TTS 전처리 파이프라인. fish_client.py에서 분리된 텍스트 정규화 모듈.

**퍼블릭 함수:**

| 함수 | 역할 |
|------|------|
| `normalize_for_tts(text)` | TTS 전처리 파이프라인 (5단계) |
| `fix_particles(text)` | 받침 기반 조사 자동 교정 |
| `load_slang_map()` | 인터넷 축약어 사전 로드 |
| `load_pronunciation_map()` | 발음 교정 사전 로드 |

#### 정규화 파이프라인 (5단계)

```
0. 화자 접두어 제거          "ㅇㅇ: 텍스트" → "텍스트"
1. 인터넷 축약어 치환        "남친" → "남자친구" (긴 키워드 우선)
1-1. 조사 자동 교정          "남자친구과" → "남자친구와"
1-2. soynlp 반복 정규화      "ㅋㅋㅋㅋ" → "ㅋ"
2. 자모 이모티콘 제거        반복 자모, 단독 자모, ^ 기호
3. 숫자 → 한국어 읽기        "3살" → "세 살", "100원" → "백 원"
3-1. 발음 교정 사전 적용     "댓글" → "대끌" (경음화)
4. 특수문자 정리             중국어 구두점, 물결표, 따옴표
5. 문장 완성                 마침표 없으면 추가
```

#### 조사 자동 교정 (`fix_particles`)

축약어 치환 후 받침 변경에 따른 조사 교정.

| 조사 쌍 | 받침 있음 | 받침 없음 | ㄹ 예외 |
|---------|----------|----------|--------|
| 과/와 | 과 | 와 | — |
| 은/는 | 은 | 는 | — |
| 이/가 | 이 | 가 | — |
| 을/를 | 을 | 를 | — |
| 으로/로 | 으로 | 로 | ㄹ→로 |

#### 발음 교정 시스템

Fish Speech G2P가 처리 못하는 한국어 음운 규칙을 사전 변환.

| 음운 규칙 | 예시 |
|----------|------|
| 경음화 | `댓글→대끌`, `맛집→마찝` |
| 비음화 | `작년→장년`, `국민→궁민` |
| 유음화 | `관련→괄련`, `연락→열락` |
| 구개음화 | `같이→가치`, `굳이→구지` |
| ㅎ 약화/탈락 | `좋아→조아`, `않다→안타` |
| 겹받침 | `읽다→익따`, `삶→삼` |

**사전 우선순위:**
1. `assets/pronunciation_map.json` (외부 파일, 내장 위에 덮어씀)
2. `_PRONUNCIATION_MAP_BUILTIN` (내장 ~40항목)

#### 축약어 사전

**로드 우선순위:**
1. `assets/slang_map.json` (외부 파일)
2. `_SLANG_MAP_BUILTIN` (내장 ~20항목)

---

### number_reader.py (97줄)

숫자를 한국어 수사로 변환. fish_client.py에서 분리된 숫자 읽기 모듈.

**퍼블릭 함수:**

| 함수 | 역할 | 예시 |
|------|------|------|
| `sino_number(n)` | 한자어 수사 (4자리씩 만/억/조) | `12345` → `"만이천삼백사십오"` |
| `native_number(n)` | 고유어 수사 (1~99) | `3` → `"세"`, `25` → `"스물다섯"` |
| `convert_number_with_counter(match)` | 숫자+단위 조합 | `"3살"` → `"세 살"` |
| `convert_standalone_number(match)` | 단독 숫자 | `"100"` → `"백"` |

**단위별 수사 분류:**

| 분류 | 단위 예시 | 수사 체계 |
|------|----------|----------|
| 고유어 (`_NATIVE_COUNTERS`) | 살, 세, 명, 개, 시, 잔, 번, 마리 | 한, 두, 세, 네, 다섯... |
| 한자어 (`_SINO_COUNTERS`) | 년, 월, 일, 원, 호, 층, 도, 분, 초, km | 일, 이, 삼, 사, 오... |

---

## 호출 흐름도

### 파이프라인 Phase 5

```
content_processor.process_content()
  → synthesize(text, scene_type, voice_key, output_path)
      ├─ normalize_for_tts(text)         # normalizer.py
      │   ├─ 축약어 치환 (load_slang_map)
      │   ├─ fix_particles() 조사 교정
      │   ├─ 숫자 변환 (number_reader.py)
      │   │   ├─ convert_number_with_counter()
      │   │   └─ convert_standalone_number()
      │   ├─ 발음 교정 (load_pronunciation_map)
      │   └─ 특수문자 정리 + 마침표
      ├─ 참조 오디오 base64 인코딩
      ├─ _FISH_SPEECH_LOCK 획득
      ├─ POST /v1/tts (재시도 + 품질 검증)
      ├─ _FISH_SPEECH_LOCK 해제
      └─ _post_process_audio() (무음 단축 + 1.2배속)
```

### 레이아웃 렌더러 (layout.py)

```
render_layout_video_from_scenes()
  → _generate_tts_chunks()
      → _tts_chunk_async(text, idx, output_dir, ...)
          ├─ 사전 생성 오디오 있음 → shutil.copy2
          └─ 없음 → synthesize(text, ...)
```

---

## 외부 사용처

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `ai_worker/pipeline/content_processor.py` | `synthesize` | Phase 5 TTS 생성 |
| `ai_worker/core/main.py` | `wait_for_fish_speech` | 서버 기동 대기 |
| `ai_worker/renderer/layout.py` | `synthesize`, `_warmup_model` | 렌더러 TTS 청크 생성 |
| `test/test_full_pipeline_e2e.py` | `_warmup_model`, `synthesize` | E2E 테스트 |
| `test/test_fish_speech.py` | `normalize_for_tts` | 정규화 테스트 |

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

---

## 에셋 디렉터리

| 경로 | 용도 |
|------|------|
| `assets/voices/` | 참조 음성 WAV 파일 (zero-shot 클로닝) |
| `assets/slang_map.json` | 인터넷 축약어 → 표준어 사전 |
| `assets/pronunciation_map.json` | 맞춤법 표기 → 발음형 교정 사전 |

---

## 에러 처리 전략

| 계층 | 전략 |
|------|------|
| 정규화 후 빈 문자열 | `ValueError` → 호출자에서 씬 스킵 |
| HTTP ReadTimeout | 즉시 재시도 (최대 3회), 최종 실패 시 raise |
| 품질 검증 실패 | 재생성 (최대 3회), 이전 최적 결과 사용 |
| FFmpeg 후처리 실패 | warning, 원본 유지 |
| soynlp 미설치 | 반복 정규화 건너뜀 |
| 참조 오디오 없음 | 기본 음성 폴백 |
| 사전 로드 실패 | 내장 사전 사용 |
