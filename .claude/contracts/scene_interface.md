# Scene Interface Contract v1.0

## 개요
Agent A(scene_director.py)가 생성 → Agent B(layout_renderer.py)가 소비

## SceneDirector.direct() 반환 타입
list[Scene]

## Scene 필드 정의

| 필드 | 타입 | 설명 | 필수 |
|---|---|---|---|
| scene_type | str | "text", "image", "text_image" 중 하나 | ✅ |
| text_lines | list[str] | 화면에 표시할 텍스트 줄 목록 | ✅ |
| image_path | Path \| None | 사용할 이미지 경로 | ❌ |
| duration_sec | float | 씬 표시 시간 (초) | ✅ |
| tts_text | str | TTS로 읽을 전체 텍스트 | ✅ |
| layout_key | str | layout.json 내 레이아웃 키 | ✅ |
| transition | str | "fade", "cut" (기본: "cut") | ❌ |

## 불변 조건
- text_lines 각 줄 ≤ layout.json max_chars
- duration_sec > 0
- tts_text 빈 문자열 → 해당 씬 TTS 없음

## 변경 절차
1. 변경 필요 Agent → Team Lead: "Scene 필드 X 변경 필요"
2. Team Lead → CEO: Proposal 작성 및 승인 요청
3. CEO 승인 → Team Lead가 이 계약 문서 업데이트
4. Team Lead → 영향받는 Agent에게 변경 통보
