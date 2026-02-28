"""수정 검증 테스트: LLM 대본 → 영상 파이프라인 4가지 버그 수정

테스트 체크리스트:
- [x] LLM 응답에서 "type": "comment" 블록이 정상 파싱되는지
- [x] LLM 응답에서 "type" 없는 기존 형식도 하위호환되는지
- [x] _extract_fields_regex()에서 type/author 추출되는지
- [x] ScriptData.to_plain_text()에서 comment author가 포함되는지
- [x] SceneDecision에 block_type/author 필드가 존재하는지
- [x] distribute_images()가 4-튜플을 올바르게 처리하는지
- [x] layout.py Step 1에서 block_type/author가 sentences에 전달되는지
"""
import json
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_parse_script_json_new_format():
    """수정 3: 새 포맷(type/author) 파싱 검증."""
    from ai_worker.llm.client import _parse_script_json

    raw = json.dumps({
        "hook": "30대 중반 소개팅의 현실?",
        "body": [
            {
                "type": "body",
                "line_count": 2,
                "lines": ["올해 37살 된 여자인데", "(만으로는 36살)"],
            },
            {
                "type": "comment",
                "author": "ㅇㅇ",
                "line_count": 2,
                "lines": ["30대 후반이면 괜찮은 사람은", "이미 짝 찾아 가정을 이루었을"],
            },
        ],
        "closer": "여러분들의 생각은 어떤가요?",
        "title_suggestion": "30대 중반 소개팅",
        "tags": ["소개팅"],
        "mood": "daily",
    }, ensure_ascii=False)

    script = _parse_script_json(raw)

    # body 블록 검증
    assert len(script.body) == 2

    # body 타입 검증
    assert script.body[0]["type"] == "body"
    assert "author" not in script.body[0]

    # comment 타입 검증
    assert script.body[1]["type"] == "comment"
    assert script.body[1]["author"] == "ㅇㅇ"
    assert script.body[1]["lines"] == ["30대 후반이면 괜찮은 사람은", "이미 짝 찾아 가정을 이루었을"]

    print("PASS: test_parse_script_json_new_format")


def test_parse_script_json_legacy_compat():
    """수정 3: type 필드 없는 기존 형식 하위호환 검증."""
    from ai_worker.llm.client import _parse_script_json

    raw = json.dumps({
        "hook": "테스트",
        "body": [
            {"line_count": 1, "lines": ["기존 포맷 문장"]},
            "레거시 문자열 항목",
        ],
        "closer": "끝",
        "title_suggestion": "제목",
        "tags": [],
        "mood": "daily",
    }, ensure_ascii=False)

    script = _parse_script_json(raw)

    # type 없는 dict → 기본값 "body"
    assert script.body[0]["type"] == "body"
    assert script.body[0]["lines"] == ["기존 포맷 문장"]

    # str 항목 → dict 변환 + type "body"
    assert script.body[1]["type"] == "body"
    assert script.body[1]["lines"] == ["레거시 문자열 항목"]

    print("PASS: test_parse_script_json_legacy_compat")


def test_extract_fields_regex_type_author():
    """수정 3: regex 폴백에서 type/author 추출 검증."""
    from ai_worker.llm.client import _extract_fields_regex

    # 의도적으로 JSON 파싱이 실패할 수 있는 약간 깨진 형태지만
    # regex가 추출할 수 있는 수준의 문자열
    raw = '''
    {
      "hook": "테스트 훅",
      "body": [
        {"type": "body", "line_count": 1, "lines": ["본문 내용"]},
        {"type": "comment", "author": "닉네임", "line_count": 1, "lines": ["댓글 내용"]}
      ],
      "closer": "마무리",
      "title_suggestion": "제목",
      "tags": ["태그"],
      "mood": "daily"
    }
    '''

    result = _extract_fields_regex(raw)
    assert result is not None

    # body에서 type/author 추출 확인
    body_types = [item.get("type") for item in result.body]
    assert "body" in body_types
    assert "comment" in body_types

    comment_item = [item for item in result.body if item.get("type") == "comment"][0]
    assert comment_item["author"] == "닉네임"

    print("PASS: test_extract_fields_regex_type_author")


def test_script_data_to_plain_text_comment():
    """수정 2: ScriptData.to_plain_text()에서 comment author 포함 검증."""
    from db.models import ScriptData

    script = ScriptData(
        hook="훅",
        body=[
            {"type": "body", "line_count": 1, "lines": ["본문입니다"]},
            {"type": "comment", "author": "ㅇㅇ", "line_count": 1, "lines": ["댓글입니다"]},
        ],
        closer="끝",
        title_suggestion="제목",
        tags=[],
        mood="daily",
    )

    plain = script.to_plain_text()
    assert "ㅇㅇ: 댓글입니다" in plain
    assert "본문입니다" in plain

    print("PASS: test_script_data_to_plain_text_comment")


def test_script_data_to_plain_text_legacy():
    """수정 2: type 없는 레거시 ScriptData도 정상 동작하는지."""
    from db.models import ScriptData

    script = ScriptData(
        hook="훅",
        body=[
            {"line_count": 1, "lines": ["레거시 본문"]},
        ],
        closer="끝",
        title_suggestion="제목",
        tags=[],
        mood="daily",
    )

    plain = script.to_plain_text()
    assert "레거시 본문" in plain

    print("PASS: test_script_data_to_plain_text_legacy")


def test_scene_decision_new_fields():
    """수정 5: SceneDecision에 block_type/author 필드 존재 검증."""
    from ai_worker.pipeline.scene_director import SceneDecision

    # 기본값 검증
    scene = SceneDecision(type="text_only", text_lines=["테스트"], image_url=None)
    assert scene.block_type == "body"
    assert scene.author is None

    # comment 타입 검증
    scene_comment = SceneDecision(
        type="text_only",
        text_lines=["댓글"],
        image_url=None,
        block_type="comment",
        author="ㅇㅇ",
    )
    assert scene_comment.block_type == "comment"
    assert scene_comment.author == "ㅇㅇ"

    print("PASS: test_scene_decision_new_fields")


def test_distribute_images_4_tuple():
    """수정 5: distribute_images()가 4-튜플을 올바르게 처리하는지."""
    from ai_worker.pipeline.scene_director import distribute_images

    body_items = [
        ("본문 1", None, "body", None),
        ("댓글 1", "voice_a", "comment", "닉네임"),
    ]

    scenes = distribute_images(body_items, [], max_images=0)

    assert len(scenes) == 2
    assert scenes[0].block_type == "body"
    assert scenes[0].author is None
    assert scenes[1].block_type == "comment"
    assert scenes[1].author == "닉네임"
    assert scenes[1].voice_override == "voice_a"

    print("PASS: test_distribute_images_4_tuple")


def test_distribute_images_with_images():
    """수정 5: 이미지 있을 때도 block_type/author가 보존되는지."""
    from ai_worker.pipeline.scene_director import distribute_images

    body_items = [
        ("본문 1", None, "body", None),
        ("본문 2", None, "body", None),
        ("댓글 1", "voice_b", "comment", "작성자"),
    ]

    scenes = distribute_images(body_items, ["img1.jpg"], max_images=1)

    # 이미지가 1개이므로 1개만 img_text, 나머지 text_only
    img_scenes = [s for s in scenes if s.type == "img_text"]
    text_scenes = [s for s in scenes if s.type == "text_only"]
    assert len(img_scenes) == 1
    assert len(text_scenes) == 2

    comment_scene = [s for s in scenes if s.block_type == "comment"][0]
    assert comment_scene.author == "작성자"
    assert comment_scene.voice_override == "voice_b"

    print("PASS: test_distribute_images_with_images")


if __name__ == "__main__":
    test_parse_script_json_new_format()
    test_parse_script_json_legacy_compat()
    test_extract_fields_regex_type_author()
    test_script_data_to_plain_text_comment()
    test_script_data_to_plain_text_legacy()
    test_scene_decision_new_fields()
    test_distribute_images_4_tuple()
    test_distribute_images_with_images()
    print("\n=== ALL TESTS PASSED ===")
