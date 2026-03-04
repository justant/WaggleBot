# ai_worker/scene — 씬 배분 모듈

대본(ScriptData) → 씬 목록(list[SceneDecision]) 변환을 담당.
리소스 분석, 씬 배분, 감정 태그 매핑, 텍스트 검증, 씬 구성 전략을 수행한다.
파이프라인 Phase 1, 3, 4, 4.5를 실행.

---

## 파일 구조

```
ai_worker/scene/
├── __init__.py       # 패키지 마커 (4줄)
├── analyzer.py       # 리소스 분석 — ResourceProfile (60줄)
├── director.py       # SceneDirector — 씬 배분/감정 태그 (573줄)
├── validator.py      # 텍스트 검증 — max_chars/금칙어 (137줄)
├── strategy.py       # SceneMix 데이터클래스 (20줄)
└── settings.yaml     # 도메인별 설정
```

---

## 모듈 상세

### analyzer.py (60줄) — Phase 1

리소스 프로파일링. 이미지:텍스트 비율을 분석하여 씬 구성 전략 결정.

| 함수/클래스 | 역할 |
|------------|------|
| `ResourceProfile` | 데이터클래스 — image_count, text_length, ratio |
| `analyze_resources(post, script)` | 이미지/텍스트 비율 계산 → ResourceProfile |

### director.py (573줄) — Phase 4, 4.5

씬 배분의 핵심 모듈. 대본 블록을 씬 타입별로 변환하고, 비디오 모드를 할당.

**핵심 데이터클래스:**

```python
@dataclass
class SceneDecision:
    type: str                          # "intro" | "img_text" | "text_only" | "img_only" | "outro"
    text_lines: list                   # [str | {"text": str, "audio": str|None}]
    image_url: str | None
    block_type: str = "body"           # "body" | "comment"
    author: str | None = None          # 댓글 작성자
    voice_override: str | None = None
    emotion: str = ""
    bgm_path: str | None = None
    # 비디오 관련 (Phase 4.5~7에서 설정)
    video_mode: str | None = None      # "t2v" | "i2v"
    video_prompt: str | None = None
    video_clip_path: str | None = None
    video_init_image: str | None = None
    video_generation_failed: bool = False
```

**핵심 함수:**

| 함수 | 역할 |
|------|------|
| `SceneDirector.build_scenes(script, images, mood)` | 대본 → 씬 목록 변환 (Phase 4) |
| `distribute_images(body_items, images, max_images)` | 본문 블록 + 이미지 → 씬 배분 |
| `assign_video_modes(scenes, post_id)` | I2V/T2V 모드 할당 (Phase 4.5) |

**씬 배분 전략** (`config/scene_policy.json` 참조):
- mood별 이미지 사용 비율 조정
- 댓글 블록(`block_type="comment"`) → text_only 우선
- intro/outro 자동 생성 (Hook/Closer 텍스트)

**video_mode 할당 규칙 (Phase 4.5):**

| 씬 타입 | 할당 |
|---------|------|
| `text_only` | 항상 `"t2v"` |
| `img_text`, `img_only` | `evaluate_image()` ≥ 0.6 → `"i2v"`, 미만 → `"t2v"` |
| `intro`, `outro` | 항상 `"t2v"` |

### validator.py (137줄) — Phase 3

씬 텍스트 검증 및 교정.

| 함수 | 역할 |
|------|------|
| `validate_and_fix(script)` | 글자수 초과 자동 절삭, 금칙어 필터링 |
| `_check_max_chars(text, limit)` | 줄당 최대 글자수 검증 |

### strategy.py (20줄)

향후 LLM 기반 씬 구성 전략을 위한 데이터클래스.

```python
@dataclass
class SceneMix:
    intro_style: str
    body_ratio: float      # img_text : text_only 비율
    outro_style: str
```

### settings.yaml

씬 배분 관련 설정. `get_domain_setting('scene', ...)` 으로 접근.

---

## 의존성

```
config/scene_policy.json         → mood별 씬 정책
config/video_styles.json         → mood별 비주얼 스타일
db/models.py                     → ScriptData (대본 입력)
ai_worker/video/image_filter.py  → I2V 적합성 평가 (Phase 4.5)
```

---

## 외부 사용처

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `ai_worker/pipeline/content_processor.py` | `SceneDirector`, `assign_video_modes`, `analyze_resources`, `validate_and_fix` | Phase 1, 3, 4, 4.5 |
| `ai_worker/renderer/layout.py` | `SceneDecision` | Phase 8 렌더링 입력 |
| `test/test_script_pipeline_fix.py` | `SceneDecision`, `distribute_images` | 단위 테스트 |
