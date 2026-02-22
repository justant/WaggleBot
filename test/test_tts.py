"""Edge-TTS 단독 테스트 + 다중 화자 테스트.

실행: python test/test_tts.py

사전 조건:
  - 인터넷 연결 (Edge-TTS는 Microsoft 클라우드 사용)
  - edge-tts 패키지 설치: pip install edge-tts
"""
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_worker.tts.edge_tts import EdgeTTS
from ai_worker.pipeline.scene_director import SceneDecision

# 프로덕션과 동일한 속도 (config/settings.py TTS_RATE 기본값)
TTS_RATE = "+25%"

OUTPUT_DIR = Path(__file__).parent / "test_tts_output"
OUTPUT_DIR.mkdir(exist_ok=True)


# ──────────────────────────────────────────────────────────────────────────────
# 테스트 1: 기존 단일 목소리 (회귀 방지)
# ──────────────────────────────────────────────────────────────────────────────

async def test_single_voice() -> None:
    """기존 단일 목소리 테스트 — 회귀 방지용."""
    print("\n" + "=" * 60)
    print("테스트 1: 단일 목소리 (ko-KR-SunHiNeural)")
    print("=" * 60)

    engine = EdgeTTS(rate=TTS_RATE)
    voice_id = "ko-KR-SunHiNeural"

    test_cases = [
        ("intro",     "이거 진짜 충격적인 사건인데, 들어봐."),
        ("img_text",  "그 날 오후 세 시, 편의점 앞에서 벌어진 일이야."),
        ("text_only", "근데 알고보니 그 사람이 바로 옆집 아저씨였던 거지."),
        ("outro",     "어때, 신기하지? 좋아요 눌러주면 더 가져올게."),
    ]

    results: list[bool] = []
    for scene_type, text in test_cases:
        print(f"\n  [{scene_type}] {text[:30]}...")
        output_path = OUTPUT_DIR / f"1_single_{scene_type}.wav"
        try:
            path = await engine.synthesize(text=text, voice_id=voice_id, output_path=output_path)
            size_kb = path.stat().st_size // 1024
            print(f"    → 생성 완료: {path} ({size_kb}KB)")
            assert path.stat().st_size > 0, "파일 크기가 0입니다"
            results.append(True)
        except Exception as e:
            print(f"    → 실패: {e}")
            results.append(False)

    passed = sum(results)
    print(f"\n  결과: {passed}/{len(results)} 통과")
    return passed == len(results)


# ──────────────────────────────────────────────────────────────────────────────
# 테스트 2: 다중 화자 시뮬레이션
# ──────────────────────────────────────────────────────────────────────────────

async def test_multi_voice() -> None:
    """씬 타입별 서로 다른 목소리로 오디오 생성 검증."""
    print("\n" + "=" * 60)
    print("테스트 2: 다중 화자 시뮬레이션")
    print("=" * 60)

    engine = EdgeTTS(rate=TTS_RATE)

    # 씬 타입 → (목소리, 텍스트)
    scenes = [
        ("intro",   "ko-KR-SunHiNeural",   "오늘 온라인 커뮤니티에서 화제가 된 글이야."),
        ("body",    "ko-KR-SunHiNeural",   "한 유저가 황당한 상황을 올렸는데..."),
        ("comment", "ko-KR-InJoonNeural",  "댓글1: 이거 실화냐?"),
        ("comment", "ko-KR-HyunsuNeural",  "댓글2: 나도 비슷한 경험 있음 ㅋㅋ"),
        ("outro",   "ko-KR-SunHiNeural",   "어때, 공감되지?"),
    ]

    generated: dict[str, Path] = {}
    results: list[bool] = []

    for idx, (scene_type, voice_id, text) in enumerate(scenes):
        label = f"{scene_type}_{idx}"
        print(f"\n  [{label}] voice={voice_id}")
        print(f"    text: {text}")
        output_path = OUTPUT_DIR / f"2_multi_{label}.wav"
        try:
            path = await engine.synthesize(text=text, voice_id=voice_id, output_path=output_path)
            size_kb = path.stat().st_size // 1024
            print(f"    → 생성 완료: {path} ({size_kb}KB)")
            assert path.stat().st_size > 0, "파일 크기가 0입니다"
            generated[label] = path
            results.append(True)
        except Exception as e:
            print(f"    → 실패: {e}")
            results.append(False)

    # 파일 크기 비교 — 서로 다른 목소리/텍스트면 크기가 다를 가능성이 높음
    if len(generated) >= 2:
        print("\n  파일 크기 비교:")
        for label, path in generated.items():
            size = path.stat().st_size
            print(f"    {label}: {size} bytes")

    passed = sum(results)
    print(f"\n  결과: {passed}/{len(results)} 통과")
    return passed == len(results)


# ──────────────────────────────────────────────────────────────────────────────
# 테스트 3: SceneDecision voice_override 필드 검증
# ──────────────────────────────────────────────────────────────────────────────

def test_scene_decision_voice_override() -> bool:
    """SceneDecision.voice_override 필드 설정 및 조회 검증."""
    print("\n" + "=" * 60)
    print("테스트 3: SceneDecision voice_override 통합 테스트")
    print("=" * 60)

    results: list[bool] = []

    # 케이스 A: 기본값은 None
    scene_default = SceneDecision(
        type="text_only",
        text_lines=["기본 씬입니다."],
        image_url=None,
    )
    ok_a = scene_default.voice_override is None
    print(f"\n  [A] voice_override 기본값=None: {'통과' if ok_a else '실패'}")
    results.append(ok_a)

    # 케이스 B: comment 씬에 InJoon 목소리 지정
    scene_comment_1 = SceneDecision(
        type="text_only",
        text_lines=["이거 실화냐?"],
        image_url=None,
        voice_override="ko-KR-InJoonNeural",
    )
    ok_b = scene_comment_1.voice_override == "ko-KR-InJoonNeural"
    print(f"  [B] voice_override=InJoonNeural: {'통과' if ok_b else '실패'}")
    results.append(ok_b)

    # 케이스 C: comment 씬에 Hyunsu 목소리 지정
    scene_comment_2 = SceneDecision(
        type="text_only",
        text_lines=["나도 비슷한 경험 있음 ㅋㅋ"],
        image_url=None,
        voice_override="ko-KR-HyunsuNeural",
    )
    ok_c = scene_comment_2.voice_override == "ko-KR-HyunsuNeural"
    print(f"  [C] voice_override=HyunsuNeural: {'통과' if ok_c else '실패'}")
    results.append(ok_c)

    # 케이스 D: 서로 다른 씬의 voice_override가 구별되는지 확인
    ok_d = scene_comment_1.voice_override != scene_comment_2.voice_override
    print(f"  [D] 두 댓글 씬 목소리가 서로 다름: {'통과' if ok_d else '실패'}")
    results.append(ok_d)

    passed = sum(results)
    print(f"\n  결과: {passed}/{len(results)} 통과")
    return passed == len(results)


# ──────────────────────────────────────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────────────────────────────────────

async def main() -> None:
    print("WaggleBot TTS 테스트 시작")
    print("(Edge-TTS: 인터넷 연결 필요)\n")

    ok1 = await test_single_voice()
    ok2 = await test_multi_voice()
    ok3 = test_scene_decision_voice_override()

    print("\n" + "=" * 60)
    print("전체 결과 요약")
    print("=" * 60)
    print(f"  테스트 1 (단일 목소리):          {'PASS' if ok1 else 'FAIL'}")
    print(f"  테스트 2 (다중 화자 시뮬레이션): {'PASS' if ok2 else 'FAIL'}")
    print(f"  테스트 3 (voice_override 필드):  {'PASS' if ok3 else 'FAIL'}")

    all_passed = ok1 and ok2 and ok3
    print(f"\n  {'모든 테스트 통과!' if all_passed else '일부 테스트 실패.'}")
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    asyncio.run(main())
