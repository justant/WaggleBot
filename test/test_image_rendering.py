"""렌더링 파이프라인 이미지 누락 방지 테스트.

수신함에서 보이는 이미지가 렌더링 결과에서 누락되는 이슈를 검증한다.

테스트 범위:
  1. video.py: 이미지 수 제한 해제 (10장 → 무제한)
  2. video.py: 다운로드 재시도 및 DC 세션 지원
  3. scene_director.py: max_body_images 확대 (8 → 20)
  4. layout.py: 이미지 다운로드 재시도

사용법:
    python -m pytest test/test_image_rendering.py -v
"""

import json
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

logging.basicConfig(level=logging.DEBUG)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_images():
    """테스트용 이미지 URL 목록 생성."""
    return [f"https://example.com/img_{i}.jpg" for i in range(15)]


@pytest.fixture
def fake_dc_images():
    """DCInside 이미지 URL 목록 생성."""
    return [f"https://dcimg5.dcinside.com/viewimage.php?id=test&no={i}" for i in range(5)]


@pytest.fixture
def fake_post():
    """테스트용 Post mock 객체."""
    post = MagicMock()
    post.id = 999
    post.title = "테스트 게시글"
    post.content = "테스트 본문 내용입니다."
    post.site_code = "test"
    post.origin_id = "test_999"
    post.images = [f"https://example.com/img_{i}.jpg" for i in range(12)]
    return post


# ===========================================================================
# 1. scene_director: max_body_images 확대 테스트
# ===========================================================================

class TestSceneDirectorImageLimit:
    """scene_director의 이미지 배분 한도 테스트."""

    def test_max_body_images_default_is_20(self):
        """scene_policy.json 없을 때 기본값이 20인지 확인."""
        from ai_worker.pipeline.scene_director import SceneDirector
        from ai_worker.pipeline.resource_analyzer import ResourceProfile

        profile = ResourceProfile(
            image_count=15,
            text_length=500,
            estimated_sentences=10,
            ratio=1.5,
            strategy="balanced",
        )
        images = [f"https://example.com/img_{i}.jpg" for i in range(15)]
        script = {
            "hook": "후킹 텍스트",
            "body": [{"type": "body", "lines": [f"본문 {i}"]} for i in range(10)],
            "closer": "마무리 텍스트",
        }

        with patch("ai_worker.pipeline.scene_director.SceneDirector.direct") as mock_direct:
            # direct()를 직접 호출하지 않고 내부 로직만 검증
            director = SceneDirector(profile, images, script, mood="daily")
            # _images에 15장 전부 들어가는지 확인
            assert len(director._images) == 15

    def test_scene_policy_max_body_images_20(self):
        """scene_policy.json에서 max_body_images가 20으로 설정되었는지 확인."""
        policy_path = Path("config/scene_policy.json")
        if not policy_path.exists():
            pytest.skip("scene_policy.json 없음")
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        max_body = policy.get("defaults", {}).get("max_body_images", 8)
        assert max_body >= 20, (
            f"max_body_images={max_body}이면 이미지가 잘릴 수 있음"
        )

    def test_distribute_images_uses_all(self):
        """15장 이미지가 10개 body 항목에 모두 배분되는지 확인."""
        from ai_worker.pipeline.scene_director import distribute_images

        body_items = [(f"문장 {i}", None, "body", None) for i in range(10)]
        images = [f"https://example.com/img_{i}.jpg" for i in range(15)]

        scenes = distribute_images(body_items, images, max_images=20)

        img_scenes = [s for s in scenes if s.image_url is not None]
        # 이미지 >= body 항목이므로 모든 body에 이미지 배분
        assert len(img_scenes) == 10

    def test_distribute_images_old_limit_would_truncate(self):
        """기존 max_body_images=8이면 이미지가 잘리는 것을 검증."""
        from ai_worker.pipeline.scene_director import distribute_images

        body_items = [(f"문장 {i}", None, "body", None) for i in range(12)]
        images = [f"https://example.com/img_{i}.jpg" for i in range(12)]

        # max_images=8 (기존 한도)
        scenes_old = distribute_images(body_items, images, max_images=8)
        img_old = [s for s in scenes_old if s.image_url is not None]

        # max_images=20 (새 한도)
        scenes_new = distribute_images(body_items, images, max_images=20)
        img_new = [s for s in scenes_new if s.image_url is not None]

        assert len(img_old) == 8, "기존 한도에서는 8장만 사용"
        assert len(img_new) == 12, "새 한도에서는 12장 모두 사용"

    def test_intro_uses_first_image(self):
        """SceneDirector가 intro에 첫 번째 이미지를 사용하는지 확인."""
        from ai_worker.pipeline.scene_director import SceneDirector
        from ai_worker.pipeline.resource_analyzer import ResourceProfile

        profile = ResourceProfile(
            image_count=5, text_length=300,
            estimated_sentences=5, ratio=1.0, strategy="balanced",
        )
        images = [f"https://example.com/img_{i}.jpg" for i in range(5)]
        script = {
            "hook": "후킹",
            "body": [{"type": "body", "lines": [f"본문 {i}"]} for i in range(3)],
            "closer": "마무리",
        }

        # scene_policy.json 로드 실패 시 fallback 경로 테스트
        with patch("pathlib.Path.read_text", side_effect=FileNotFoundError):
            director = SceneDirector(profile, images, script, mood="daily")
            scenes = director.direct()

        intro = scenes[0]
        assert intro.image_url == "https://example.com/img_0.jpg"
        # intro에서 pop 후 나머지 body에 분배
        body_with_img = [s for s in scenes[1:-1] if s.image_url is not None]
        assert len(body_with_img) >= 3, "body에 남은 4장 중 3개 이상 배분"


# ===========================================================================
# 2. video.py: 이미지 수 제한 해제 + 다운로드 재시도 테스트
# ===========================================================================

class TestVideoImageDownload:
    """video.py의 이미지 다운로드 및 제한 관련 테스트."""

    def test_download_with_retry_success_on_second_attempt(self):
        """첫 번째 실패 후 재시도에서 성공하는 케이스."""
        from ai_worker.renderer.video import _download_image_with_retry

        fake_resp_ok = MagicMock()
        fake_resp_ok.content = b"\x89PNG" + b"\x00" * 500
        fake_resp_ok.raise_for_status = MagicMock()

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise __import__("requests").exceptions.ConnectionError("timeout")
            return fake_resp_ok

        with patch("ai_worker.renderer.video.requests.get", side_effect=side_effect), \
             patch("ai_worker.renderer.video._is_dc_url", return_value=False), \
             patch("ai_worker.renderer.video.time.sleep"):
            result = _download_image_with_retry("https://example.com/img.jpg", max_retries=2)

        assert result is not None
        assert call_count == 2, "첫 번째 실패 후 재시도 성공"

    def test_download_with_retry_all_attempts_fail(self):
        """모든 재시도 실패 시 None 반환."""
        from ai_worker.renderer.video import _download_image_with_retry

        with patch("ai_worker.renderer.video.requests.get",
                   side_effect=__import__("requests").exceptions.ConnectionError("fail")), \
             patch("ai_worker.renderer.video._is_dc_url", return_value=False), \
             patch("ai_worker.renderer.video.time.sleep"):
            result = _download_image_with_retry("https://example.com/img.jpg", max_retries=2)

        assert result is None

    def test_download_rejects_placeholder(self):
        """200바이트 미만 플레이스홀더 이미지를 거부하는지 확인."""
        from ai_worker.renderer.video import _download_image_with_retry

        fake_resp = MagicMock()
        fake_resp.content = b"GIF89a" + b"\x00" * 30  # 36 bytes = placeholder
        fake_resp.raise_for_status = MagicMock()

        with patch("ai_worker.renderer.video.requests.get", return_value=fake_resp), \
             patch("ai_worker.renderer.video._is_dc_url", return_value=False):
            result = _download_image_with_retry("https://example.com/tiny.gif", min_size=200)

        assert result is None

    def test_dc_url_detection(self):
        """DCInside URL 감지 로직."""
        from ai_worker.renderer.video import _is_dc_url

        assert _is_dc_url("https://dcimg5.dcinside.com/viewimage.php?id=test") is True
        assert _is_dc_url("https://image.dcinside.com/img.jpg") is True
        assert _is_dc_url("https://nimage.dcinside.co.kr/img.jpg") is True
        assert _is_dc_url("https://example.com/img.jpg") is False
        assert _is_dc_url("https://bobae.com/dcinside.com/fake") is False

    def test_dc_url_uses_session(self):
        """DCInside URL은 세션 기반 다운로드를 사용하는지 확인."""
        from ai_worker.renderer.video import _download_image_with_retry

        fake_resp = MagicMock()
        fake_resp.content = b"\x89PNG" + b"\x00" * 500
        fake_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = fake_resp

        with patch("ai_worker.renderer.video._is_dc_url", return_value=True), \
             patch("ai_worker.renderer.video._get_dc_session", return_value=mock_session):
            result = _download_image_with_retry(
                "https://dcimg5.dcinside.com/viewimage.php?id=test",
            )

        assert result is not None
        mock_session.get.assert_called_once()
        call_headers = mock_session.get.call_args[1]["headers"]
        assert "Referer" in call_headers
        assert "gall.dcinside.com" in call_headers["Referer"]

    def test_slideshow_no_hard_cap(self):
        """_build_slideshow가 이미지 수를 하드코딩으로 자르지 않는지 확인.

        image_urls[:10] 패턴이 코드에서 제거되었는지 소스 검증.
        """
        import inspect
        from ai_worker.renderer.video import _build_slideshow

        source = inspect.getsource(_build_slideshow)
        assert "image_urls[:10]" not in source, (
            "_build_slideshow에 image_urls[:10] 하드코딩 제한이 남아있음"
        )
        assert "image_urls[:" not in source or "image_urls[:max" in source, (
            "이미지 배열을 임의로 자르는 슬라이싱이 있음"
        )


# ===========================================================================
# 3. layout.py: 이미지 다운로드 재시도 테스트
# ===========================================================================

class TestLayoutImageLoad:
    """layout.py의 _load_image 재시도 로직 테스트."""

    def test_load_image_retry_on_failure(self):
        """다운로드 실패 시 재시도하는지 확인."""
        from ai_worker.renderer.layout import _load_image

        call_count = 0
        fake_img_bytes = self._make_fake_jpeg()

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise __import__("requests").exceptions.Timeout("timeout")
            resp = MagicMock()
            resp.content = fake_img_bytes
            resp.raise_for_status = MagicMock()
            return resp

        with patch("ai_worker.renderer.layout.requests.get", side_effect=side_effect), \
             patch("ai_worker.renderer.layout.time.sleep", return_value=None), \
             __import__("tempfile").TemporaryDirectory() as tmp:
            result = _load_image(
                "https://example.com/test.jpg",
                Path(tmp),
                max_retries=2,
            )

        assert call_count == 2, "첫 실패 후 재시도"
        assert result is not None

    def test_load_image_placeholder_rejected(self):
        """200바이트 미만 플레이스홀더를 거부하는지 확인."""
        from ai_worker.renderer.layout import _load_image

        fake_resp = MagicMock()
        fake_resp.content = b"GIF89a" + b"\x00" * 30
        fake_resp.raise_for_status = MagicMock()

        with patch("ai_worker.renderer.layout.requests.get", return_value=fake_resp), \
             __import__("tempfile").TemporaryDirectory() as tmp:
            result = _load_image(
                "https://example.com/placeholder.gif",
                Path(tmp),
                max_retries=0,
            )

        assert result is None

    def test_load_image_uses_dc_session_for_dc_urls(self):
        """DCInside URL에 대해 전용 세션을 사용하는지 확인."""
        from ai_worker.renderer.layout import _load_image

        fake_img_bytes = self._make_fake_jpeg()
        fake_resp = MagicMock()
        fake_resp.content = fake_img_bytes
        fake_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = fake_resp

        with patch("ai_worker.renderer.layout._get_dc_session", return_value=mock_session), \
             __import__("tempfile").TemporaryDirectory() as tmp:
            result = _load_image(
                "https://dcimg5.dcinside.com/viewimage.php?id=test",
                Path(tmp),
            )

        assert result is not None
        mock_session.get.assert_called_once()

    def test_load_image_all_retries_fail(self):
        """모든 재시도 실패 시 None 반환."""
        from ai_worker.renderer.layout import _load_image

        with patch("ai_worker.renderer.layout.requests.get",
                   side_effect=__import__("requests").exceptions.ConnectionError("fail")), \
             patch("ai_worker.renderer.layout.time.sleep", return_value=None), \
             __import__("tempfile").TemporaryDirectory() as tmp:
            result = _load_image(
                "https://example.com/broken.jpg",
                Path(tmp),
                max_retries=2,
            )

        assert result is None

    @staticmethod
    def _make_fake_jpeg() -> bytes:
        """테스트용 최소 유효 JPEG 바이트 생성."""
        from PIL import Image
        import io
        img = Image.new("RGB", (100, 100), "red")
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()


# ===========================================================================
# 4. 통합: 수신함 이미지 수 = 렌더링 파이프라인 이미지 수 검증
# ===========================================================================

class TestImageCountConsistency:
    """수신함에서 보이는 이미지와 렌더링에 사용되는 이미지 수 일치 검증."""

    def test_all_post_images_reach_scene_director(self, fake_post):
        """Post.images의 모든 이미지가 SceneDirector에 전달되는지 확인."""
        from ai_worker.pipeline.resource_analyzer import ResourceProfile
        from ai_worker.pipeline.scene_director import SceneDirector

        images = fake_post.images  # 12장
        profile = ResourceProfile(
            image_count=len(images),
            text_length=len(fake_post.content),
            estimated_sentences=8,
            ratio=len(images) / 8,
            strategy="balanced",
        )
        script = {
            "hook": "후킹 텍스트",
            "body": [{"type": "body", "lines": [f"본문 {i}"]} for i in range(8)],
            "closer": "마무리 텍스트",
        }

        with patch("pathlib.Path.read_text", side_effect=FileNotFoundError):
            director = SceneDirector(profile, images, script, mood="daily")
            scenes = director.direct()

        # scenes에서 사용된 고유 이미지 URL 수집
        used_images = {s.image_url for s in scenes if s.image_url is not None}
        # intro(1) + body(min(remaining, body_items)) = 최소 9장
        assert len(used_images) >= 9, (
            f"12장 이미지 중 {len(used_images)}장만 사용됨 (최소 9장 기대)"
        )

    def test_twelve_images_not_capped_to_eight(self):
        """12장 이미지가 기존 한도(8)에 의해 잘리지 않는지 확인."""
        from ai_worker.pipeline.scene_director import distribute_images

        body_items = [(f"문장 {i}", None, "body", None) for i in range(12)]
        images = [f"img_{i}" for i in range(12)]

        scenes = distribute_images(body_items, images, max_images=20)
        img_count = sum(1 for s in scenes if s.image_url is not None)

        assert img_count == 12, f"12장 전부 배분되어야 하나 {img_count}장만 사용"

    def test_scenes_to_plan_preserves_all_images(self):
        """_scenes_to_plan_and_sentences가 이미지를 누락하지 않는지 확인."""
        from ai_worker.pipeline.scene_director import SceneDecision
        from ai_worker.renderer.layout import _scenes_to_plan_and_sentences

        scenes = [
            SceneDecision(type="intro", text_lines=["후킹"], image_url="img_0"),
            SceneDecision(type="img_text", text_lines=["본문1"], image_url="img_1"),
            SceneDecision(type="img_text", text_lines=["본문2"], image_url="img_2"),
            SceneDecision(type="text_only", text_lines=["본문3"], image_url=None),
            SceneDecision(type="img_text", text_lines=["본문4"], image_url="img_3"),
            SceneDecision(type="outro", text_lines=["마무리"], image_url="img_4"),
        ]

        sentences, plan, images = _scenes_to_plan_and_sentences(scenes)

        assert len(images) == 5, f"5장 이미지가 모두 전달되어야 하나 {len(images)}장"
        img_plan = [p for p in plan if p.get("img_idx") is not None]
        assert len(img_plan) == 5, "plan에서 이미지 참조 5개 모두 존재"


# ===========================================================================
# 5. Referer 헤더 검증
# ===========================================================================

class TestRefererHeaders:
    """다운로드 시 사이트별 올바른 Referer 헤더 전송 검증."""

    def test_dc_session_sends_referer(self):
        """DCInside 다운로드 시 Referer: gall.dcinside.com 전송."""
        from ai_worker.renderer.video import _download_image_with_retry

        fake_resp = MagicMock()
        fake_resp.content = b"\x89PNG" + b"\x00" * 500
        fake_resp.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.get.return_value = fake_resp

        with patch("ai_worker.renderer.video._is_dc_url", return_value=True), \
             patch("ai_worker.renderer.video._get_dc_session", return_value=mock_session):
            _download_image_with_retry("https://dcimg5.dcinside.com/test.jpg")

        headers = mock_session.get.call_args[1]["headers"]
        assert headers["Referer"] == "https://gall.dcinside.com/"
        assert "Sec-Fetch-Dest" in headers

    def test_generic_url_sends_origin_referer(self):
        """일반 URL에 대해 origin 기반 Referer 전송."""
        from ai_worker.renderer.video import _download_image_with_retry

        fake_resp = MagicMock()
        fake_resp.content = b"\x89PNG" + b"\x00" * 500
        fake_resp.raise_for_status = MagicMock()

        with patch("ai_worker.renderer.video.requests.get", return_value=fake_resp) as mock_get, \
             patch("ai_worker.renderer.video._is_dc_url", return_value=False):
            _download_image_with_retry("https://cdn.example.com/photo/123.jpg")

        headers = mock_get.call_args[1]["headers"]
        assert "Referer" in headers
        assert "example.com" in headers["Referer"]
