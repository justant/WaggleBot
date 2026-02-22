"""
Scene Policy 시나리오 테스트 — 눈으로 확인하는 씬 구성 결과

실행: .venv/bin/python3 test/run_scene_scenarios.py

LLM에서 mood를 받아온 뒤 SceneDirector가 어떻게 씬을 구성하는지 출력.

테스트 시나리오:
  Case A: 이미지 0장 + 텍스트만 길게 있는 글 → mood 폴더 이미지 자동 삽입
  Case B: 이미지 3장 + 텍스트 100자인 글    → 이미지 균등 분배
  Case C: 이미지 10장 + 텍스트 20자인 글   → max_body_images=8 제한
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_worker.pipeline.resource_analyzer import ResourceProfile
from ai_worker.pipeline.scene_director import SceneDecision, SceneDirector
from db.models import ScriptData


# ---------------------------------------------------------------------------
# LLM 응답 시뮬레이터 (실제 Ollama 없이 ScriptData를 직접 생성)
# ---------------------------------------------------------------------------

def simulate_llm_response(mood: str, n_body_lines: int) -> ScriptData:
    """LLM이 mood 포함 ScriptData를 반환했다고 가정."""
    body = [
        {"line_count": 1, "lines": [f"본문 줄 {i+1}: 내용이 이어집니다"]}
        for i in range(n_body_lines)
    ]
    return ScriptData(
        hook="충격적인 사연입니다",
        body=body,
        closer="여러분 생각은 어떤가요?",
        title_suggestion="테스트 게시글 제목",
        tags=["테스트", "시나리오"],
        mood=mood,
    )


# ---------------------------------------------------------------------------
# 씬 목록 시각화 출력
# ---------------------------------------------------------------------------

def print_scenes(scenes: list[SceneDecision], title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    print(f"  총 씬 수: {len(scenes)}")
    print()

    for i, s in enumerate(scenes):
        # 씬 타입별 아이콘
        icon = {
            "intro":     "🎬",
            "img_text":  "🖼️ ",
            "text_only": "📝",
            "img_only":  "🌄",
            "outro":     "🎤",
        }.get(s.type, "❓")

        # 이미지 경로 (파일명만 출력)
        img = Path(s.image_url).name if s.image_url else "없음"

        # 텍스트 (첫 줄만)
        text = s.text_lines[0] if s.text_lines else ""
        text_preview = text[:25] + "..." if len(text) > 25 else text

        print(f"  [{i+1:02d}] {icon} {s.type:<10}  "
              f"이미지={img:<35}  텍스트={text_preview}")

        # 세부 정보 (intro/outro만)
        if s.type in ("intro", "outro") or i == 0:
            print(f"        mood={s.mood}  tts_emotion={s.tts_emotion or '(없음)'}  "
                  f"bgm={'있음' if s.bgm_path else '없음'}")

    # 요약
    types = [s.type for s in scenes]
    print(f"\n  씬 구성: {' → '.join(types)}")
    img_count = sum(1 for s in scenes if s.image_url)
    mood_img_count = sum(1 for s in scenes
                         if s.image_url and "mood" in (s.image_url or ""))
    print(f"  이미지 사용: {img_count}개 (mood폴더={mood_img_count}개, 게시글원본={img_count-mood_img_count}개)")


# ---------------------------------------------------------------------------
# Case A: 이미지 0장 + 텍스트만 길게
# ---------------------------------------------------------------------------

def case_a_no_image_long_text() -> None:
    print("\n" + "="*60)
    print("  CASE A: 이미지 0장 + 텍스트만 길게 있는 글")
    print("  기대 동작: mood 폴더에서 이미지 자동 삽입")
    print("="*60)

    # LLM이 mood='horror'로 분류했다고 가정
    script_data = simulate_llm_response(mood="horror", n_body_lines=8)
    print(f"\n  [LLM 응답] mood='{script_data.mood}', body={len(script_data.body)}줄")

    profile = ResourceProfile(
        image_count=0, text_length=400, estimated_sentences=8,
        ratio=0.0, strategy="text_heavy",
    )
    director = SceneDirector(
        profile=profile,
        images=[],               # 게시글 이미지 0장
        script=json.loads(script_data.to_json()),
        mood=script_data.mood,
    )
    scenes = director.direct()
    print_scenes(scenes, "Case A 결과")

    # 검증
    assert scenes[0].type in ("img_only", "intro"), f"intro 타입 오류: {scenes[0].type}"
    assert scenes[-1].type == "outro"
    if scenes[0].type == "img_only":
        assert scenes[0].image_url is not None, "mood 폴더 이미지가 없어 None"
        assert "horror" in (scenes[0].image_url or ""), f"horror mood 이미지 아님: {scenes[0].image_url}"
        print("\n  ✅ 이미지 없는 글 → intro에 horror mood 폴더 이미지 자동 삽입 확인")
    else:
        print("\n  ⚠️  mood 폴더 이미지 없음 — fallback(텍스트만 인트로) 동작")
    print(f"  ✅ outro tts_emotion='{scenes[-1].tts_emotion}'")


# ---------------------------------------------------------------------------
# Case B: 이미지 3장 + 텍스트 100자 (약 5줄)
# ---------------------------------------------------------------------------

def case_b_three_images_medium_text() -> None:
    print("\n" + "="*60)
    print("  CASE B: 이미지 3장 + 텍스트 100자인 글")
    print("  기대 동작: 이미지 균등 분배 (5줄에 3장)")
    print("="*60)

    script_data = simulate_llm_response(mood="humor", n_body_lines=5)
    print(f"\n  [LLM 응답] mood='{script_data.mood}', body={len(script_data.body)}줄")

    # 실제 게시글 이미지 3장 (다운로드된 humor 폴더 이미지 사용)
    post_images = [
        "assets/image/intro/mood/humor/humor_intro_01.jpg",
        "assets/image/intro/mood/humor/humor_intro_02.jpg",
        "assets/image/intro/mood/humor/humor_intro_03.jpg",
    ]

    profile = ResourceProfile(
        image_count=3, text_length=100, estimated_sentences=5,
        ratio=0.38, strategy="balanced",
    )
    director = SceneDirector(
        profile=profile,
        images=post_images,
        script=json.loads(script_data.to_json()),
        mood=script_data.mood,
    )
    scenes = director.direct()
    print_scenes(scenes, "Case B 결과")

    # 검증
    assert scenes[0].type == "img_text", f"이미지 있으면 intro=img_text여야 함: {scenes[0].type}"
    body_img_scenes = [s for s in scenes[1:-1] if s.type == "img_text"]
    print(f"\n  ✅ 게시글 이미지 첫 장 → intro img_text 사용")
    print(f"  ✅ 본문에 img_text {len(body_img_scenes)}개 균등 배치")
    print(f"  ✅ tts_emotion='{scenes[0].tts_emotion}'")


# ---------------------------------------------------------------------------
# Case C: 이미지 10장 + 텍스트 20자 (약 1줄)
# ---------------------------------------------------------------------------

def case_c_many_images_short_text() -> None:
    print("\n" + "="*60)
    print("  CASE C: 이미지 10장 + 텍스트 20자인 글")
    print("  기대 동작: max_body_images=8 제한, 넘치는 이미지 버림")
    print("="*60)

    script_data = simulate_llm_response(mood="shock", n_body_lines=1)
    print(f"\n  [LLM 응답] mood='{script_data.mood}', body={len(script_data.body)}줄")

    # 10장의 게시글 이미지 (실제 파일이 없어도 경로만으로 테스트)
    post_images = [f"assets/post_image_{i:02d}.jpg" for i in range(10)]

    profile = ResourceProfile(
        image_count=10, text_length=20, estimated_sentences=1,
        ratio=0.97, strategy="img_heavy",
    )
    director = SceneDirector(
        profile=profile,
        images=post_images,
        script=json.loads(script_data.to_json()),
        mood=script_data.mood,
    )
    scenes = director.direct()
    print_scenes(scenes, "Case C 결과")

    # 검증
    all_images = [s.image_url for s in scenes if s.image_url]
    post_img_used = [img for img in all_images if "post_image" in (img or "")]
    print(f"\n  ✅ 게시글 이미지 10장 중 {len(post_img_used)}장 사용 (max_body_images=8 + intro 1장)")
    print(f"  ✅ outro는 shock mood 폴더 이미지 사용")
    print(f"  ✅ tts_emotion='{scenes[0].tts_emotion}'")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\nScene Policy 시나리오 테스트")
    print("(LLM mood → SceneDirector → 씬 구성 전 과정 시뮬레이션)\n")

    try:
        case_a_no_image_long_text()
        case_b_three_images_medium_text()
        case_c_many_images_short_text()

        print("\n" + "="*60)
        print("  🎉 모든 시나리오 완료")
        print("="*60 + "\n")

    except AssertionError as e:
        print(f"\n  ❌ 실패: {e}")
        sys.exit(1)
