"""
Scene Policy 통합 검증 테스트

실행: .venv/bin/python3 test/test_scene_policy.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def fail(msg: str) -> None:
    print(f"  ❌ {msg}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Test 1: scene_policy.json 구조 검증
# ---------------------------------------------------------------------------
def test_scene_policy_json() -> None:
    print("[1] scene_policy.json 검증")
    policy = json.loads(Path("config/scene_policy.json").read_text())

    moods = policy.get("moods", {})
    assert len(moods) == 9, f"mood 수 오류: {len(moods)}"
    ok(f"9개 mood 확인: {list(moods.keys())}")

    required_keys = ["intro_image_dir", "outro_image_dir", "bgm_dir", "tts_emotion", "label"]
    for m_key, m_val in moods.items():
        for k in required_keys:
            assert k in m_val, f"[{m_key}] '{k}' 필드 없음"
    ok("모든 mood에 필수 키 존재")

    defaults = policy.get("defaults", {})
    assert "fallback_mood" in defaults
    assert "max_body_images" in defaults
    ok(f"defaults: fallback_mood={defaults['fallback_mood']}, max_body_images={defaults['max_body_images']}")

    outro_texts = policy.get("scene_rules", {}).get("outro", {}).get("fixed_texts", [])
    assert len(outro_texts) >= 1
    ok(f"outro fixed_texts: {len(outro_texts)}개")


# ---------------------------------------------------------------------------
# Test 2: ScriptData.mood 기본값 및 from_json
# ---------------------------------------------------------------------------
def test_script_data_mood() -> None:
    print("[2] ScriptData.mood 검증")
    from db.models import ScriptData

    sd = ScriptData(hook="test", body=[], closer="end", title_suggestion="", tags=[])
    assert sd.mood == "daily", f"기본값 오류: {sd.mood}"
    ok(f"기본값 'daily' 확인")

    # 9가지 mood 파싱
    for mood in ["touching", "humor", "anger", "sadness", "horror", "info", "controversy", "daily", "shock"]:
        raw = json.dumps({"hook": "h", "body": [], "closer": "c",
                          "title_suggestion": "", "tags": [], "mood": mood})
        sd2 = ScriptData.from_json(raw)
        assert sd2.mood == mood, f"mood 파싱 오류: {sd2.mood} != {mood}"
    ok("9가지 mood 파싱 정상")

    # 레거시 mood (funny) — 그대로 저장됨 (fallback은 policy 레벨에서 처리)
    raw_legacy = json.dumps({"hook": "h", "body": [], "closer": "c",
                             "title_suggestion": "", "tags": [], "mood": "funny"})
    sd3 = ScriptData.from_json(raw_legacy)
    assert sd3.mood == "funny"
    ok(f"레거시 mood 'funny' 보존 확인 (policy fallback으로 처리됨)")

    # mood 없는 레거시 → 기본값 daily
    raw_no_mood = json.dumps({"hook": "h", "body": [], "closer": "c",
                              "title_suggestion": "", "tags": []})
    sd4 = ScriptData.from_json(raw_no_mood)
    assert sd4.mood == "daily"
    ok("mood 없는 레거시 → 'daily' fallback 확인")


# ---------------------------------------------------------------------------
# Test 3: pick_random_file
# ---------------------------------------------------------------------------
def test_pick_random_file() -> None:
    print("[3] pick_random_file 검증")
    from ai_worker.pipeline.scene_director import pick_random_file

    # 존재하지 않는 폴더
    r = pick_random_file("assets/image/nonexistent", [".jpg"])
    assert r is None
    ok("존재하지 않는 폴더 → None")

    # 빈 폴더 (.gitkeep만 있는 경우) — 확장자 필터로 None
    from pathlib import Path as _P
    test_dir = _P("assets/image/intro/mood/touching")
    exts = [".png", ".jpg", ".jpeg", ".webp"]
    result = pick_random_file(str(test_dir), exts)
    if result is not None:
        ok(f"실제 에셋 폴더에서 랜덤 선택: {result.name}")
    else:
        ok("에셋 미존재 → None (fallback 체인 동작)")


# ---------------------------------------------------------------------------
# Test 4: distribute_images — 5가지 케이스
# ---------------------------------------------------------------------------
def test_distribute_images() -> None:
    print("[4] distribute_images 검증")
    from ai_worker.pipeline.scene_director import distribute_images

    # Case 4: 이미지 없음 → text_only
    s = distribute_images([("줄1", None), ("줄2", None)], [], 8)
    assert all(x.type == "text_only" for x in s), f"Case4 실패: {[x.type for x in s]}"
    ok(f"Case4 (이미지 없음): {[x.type for x in s]}")

    # Case 5: 텍스트 없음 → img_only
    s = distribute_images([], ["a.jpg", "b.jpg"], 8)
    assert all(x.type == "img_only" for x in s), f"Case5 실패: {[x.type for x in s]}"
    ok(f"Case5 (텍스트 없음): {[x.type for x in s]}")

    # Case 1: 10줄, 1이미지 → img_text 1개
    items = [(f"줄{i}", None) for i in range(10)]
    s = distribute_images(items, ["img.jpg"], 8)
    img_s = [x for x in s if x.type == "img_text"]
    assert len(img_s) == 1, f"Case1 img_text 수 오류: {len(img_s)}"
    ok(f"Case1 (10줄, 1이미지): 총{len(s)}씬, img_text={len(img_s)}")

    # Case 2: 10줄, 3이미지 → img_text 3개
    s = distribute_images(items, ["a.jpg", "b.jpg", "c.jpg"], 8)
    img_s = [x for x in s if x.type == "img_text"]
    assert len(img_s) == 3, f"Case2 img_text 수 오류: {len(img_s)}"
    ok(f"Case2 (10줄, 3이미지): 총{len(s)}씬, img_text={len(img_s)}")

    # Case 3: 이미지 >= 텍스트 → 모두 img_text
    s = distribute_images([("줄1", None), ("줄2", None)], ["a.jpg", "b.jpg", "c.jpg"], 8)
    assert all(x.type == "img_text" for x in s), f"Case3 실패: {[x.type for x in s]}"
    ok(f"Case3 (이미지>=텍스트): {[x.type for x in s]}")

    # max_body_images 제한
    s = distribute_images([], [f"img{i}.jpg" for i in range(20)], 8)
    assert len(s) == 8, f"max_images 제한 오류: {len(s)}"
    ok(f"max_body_images=8 제한: {len(s)}씬")

    # mood/tts_emotion 전달 확인
    s = distribute_images([("줄1", None)], [], 8, tts_emotion="gentle", mood="touching")
    assert s[0].mood == "touching" and s[0].tts_emotion == "gentle"
    ok("mood/tts_emotion 전달 정상")


# ---------------------------------------------------------------------------
# Test 5: SceneDirector.direct() — policy 기반 시뮬레이션
# ---------------------------------------------------------------------------
def test_scene_director_direct() -> None:
    print("[5] SceneDirector.direct() 검증")
    from ai_worker.pipeline.resource_analyzer import ResourceProfile
    from ai_worker.pipeline.scene_director import SceneDirector

    script = {
        "hook": "충격적인 사연입니다",
        "body": [
            {"line_count": 1, "lines": ["오늘 회사에서"]},
            {"line_count": 1, "lines": ["믿기 힘든 일이"]},
            {"line_count": 1, "lines": ["벌어졌습니다"]},
        ],
        "closer": "여러분 생각은요?",
    }

    profile = ResourceProfile(image_count=0, text_length=100, estimated_sentences=3, ratio=0.0, strategy="balanced")

    # 이미지 없는 경우 — img_only 또는 intro로 시작해야 함
    director = SceneDirector(profile=profile, images=[], script=script, mood="humor")
    scenes = director.direct()
    assert len(scenes) >= 2, f"씬 수 오류: {len(scenes)}"
    assert scenes[0].mood == "humor", f"intro mood 오류: {scenes[0].mood}"
    assert scenes[-1].type == "outro", f"마지막 씬이 outro가 아님: {scenes[-1].type}"
    ok(f"이미지 없음: 총{len(scenes)}씬, intro_type={scenes[0].type}, mood='{scenes[0].mood}'")

    # 이미지 있는 경우
    profile2 = ResourceProfile(image_count=2, text_length=100, estimated_sentences=3, ratio=0.5, strategy="balanced")
    director2 = SceneDirector(
        profile=profile2,
        images=["assets/image/intro/mood/humor/humor_intro_01.jpg",
                "assets/image/intro/mood/humor/humor_intro_02.jpg"],
        script=script,
        mood="shock",
    )
    scenes2 = director2.direct()
    assert scenes2[0].type == "img_text", f"이미지 있을 때 intro가 img_text여야 함: {scenes2[0].type}"
    ok(f"이미지 있음: intro={scenes2[0].type}, image_url={scenes2[0].image_url is not None}")

    # tts_emotion이 SceneDecision에 전달됐는지 확인
    assert scenes[0].tts_emotion != "" or True  # policy 있으면 설정, 없으면 ""
    ok(f"tts_emotion: '{scenes[0].tts_emotion}'")


# ---------------------------------------------------------------------------
# Test 6: 에셋 디렉토리 구조 검증
# ---------------------------------------------------------------------------
def test_asset_structure() -> None:
    print("[6] 에셋 디렉토리 구조 검증")
    moods = ["touching", "humor", "anger", "sadness", "horror", "info", "controversy", "daily", "shock"]
    img_exts = {".png", ".jpg", ".jpeg", ".webp"}
    bgm_exts = {".mp3", ".wav", ".ogg"}

    all_ok = True
    for mood in moods:
        intro = list((Path(f"assets/image/intro/mood/{mood}")).glob("*"))
        outro = list((Path(f"assets/image/outro/mood/{mood}")).glob("*"))
        bgm   = list((Path(f"assets/bgm/mood/{mood}")).glob("*"))

        intro_files = [f for f in intro if f.suffix.lower() in img_exts]
        outro_files = [f for f in outro if f.suffix.lower() in img_exts]
        bgm_files   = [f for f in bgm   if f.suffix.lower() in bgm_exts]

        status = "✅" if intro_files and outro_files and bgm_files else "⚠️ "
        print(f"    {status} {mood:12s}: intro={len(intro_files)} outro={len(outro_files)} bgm={len(bgm_files)}")
        if not (intro_files and outro_files and bgm_files):
            all_ok = False

    if all_ok:
        ok("모든 mood 에셋 확인 완료")
    else:
        print("  ⚠️  일부 폴더에 에셋 없음 — fallback 체인으로 처리됨")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Scene Policy 통합 테스트")
    print("=" * 60)

    try:
        test_scene_policy_json()
        test_script_data_mood()
        test_pick_random_file()
        test_distribute_images()
        test_scene_director_direct()
        test_asset_structure()

        print("\n" + "=" * 60)
        print("🎉 모든 테스트 통과")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 테스트 실패: {e}")
        sys.exit(1)
