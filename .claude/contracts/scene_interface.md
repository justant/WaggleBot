# Scene Interface Contract v2.0

> **변경 이력**: v2.0 — 2026-02-22, scene_policy_spec 구현에 따른 필드 추가

## 개요
Agent A(scene_director.py)가 생성 → Agent B(layout_renderer.py)가 소비

## SceneDirector.direct() 반환 타입
`list[SceneDecision]`

## SceneDecision 필드 정의

| 필드 | 타입 | 설명 | 필수 | 변경 |
|---|---|---|---|---|
| `type` | `SceneType` | `"intro"`, `"img_text"`, `"text_only"`, `"img_only"`, `"outro"` 중 하나 | ✅ | **`img_only` 추가** |
| `text_lines` | `list` | 화면에 표시할 텍스트 줄 목록 (str 또는 `{"text": str, "audio": str|None}`) | ✅ | - |
| `image_url` | `str \| None` | 사용할 이미지 경로 (img_text, img_only, outro에서 사용) | ❌ | - |
| `text_only_stack` | `int` | text_only 씬의 실제 스택 줄 수 (기본 1) | ❌ | - |
| `emotion_tag` | `str` | Fish Speech 레거시 감정 태그 (기본 `""`) | ❌ | - |
| `voice_override` | `str \| None` | 댓글 씬 voice override | ❌ | - |
| `mood` | `str` | 9가지 감정 키 (`touching`, `humor`, `anger`, `sadness`, `horror`, `info`, `controversy`, `daily`, `shock`) | ✅ | **신규** |
| `tts_emotion` | `str` | TTS 감정 톤 키 (scene_policy.json에서 조회, 기본 `""`) | ✅ | **신규** |
| `bgm_path` | `str \| None` | 선택된 BGM 파일 경로 (폴더에서 랜덤 선택 결과, 없으면 None) | ❌ | **신규** |

## SceneType 리터럴
```python
SceneType = Literal["intro", "img_text", "text_only", "img_only", "outro"]
```

## layout_key 매핑
| SceneType | layout.json 키 |
|---|---|
| `intro` | `intro` (텍스트만 — 기존 베이스 프레임) |
| `img_text` | `img_text` |
| `text_only` | `text_only` |
| `img_only` | `img_only` (신규 추가) |
| `outro` | `outro` |

## 불변 조건
- `text_lines` 각 줄 ≤ layout.json max_chars
- `mood`는 반드시 9가지 키 중 하나 (미인식 시 `"daily"` fallback)
- `bgm_path`는 intro 씬에만 설정됨 (전체 영상에 1개 BGM). 나머지 씬은 `None`.
- `tts_emotion`이 빈 문자열이면 TTS 엔진이 기본 톤으로 처리

## 변경 절차
1. 변경 필요 Agent → Team Lead: "Scene 필드 X 변경 필요"
2. Team Lead → CEO: Proposal 작성 및 승인 요청
3. CEO 승인 → Team Lead가 이 계약 문서 업데이트
4. Team Lead → 영향받는 Agent에게 변경 통보
