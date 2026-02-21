"""HD 렌더 큐 & 워커 스레드."""

import logging
import queue as _queue
import shutil
import threading

from db.models import Post, PostStatus, Content, ScriptData
from db.session import SessionLocal

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HD 렌더 큐 & 상태 추적 (프로세스 레벨 — 재런 간 유지)
# ---------------------------------------------------------------------------
# 큐 대기 중 또는 렌더 중인 post_id 집합 (UI 버튼 상태 판별용)
hd_render_pending: set[int] = set()
# post_id → 에러 메시지 (렌더 실패 시)
hd_render_errors: dict[int, str] = {}
# FIFO 렌더 요청 큐 — 워커 스레드가 순서대로 소비
_hd_render_queue: _queue.Queue[int] = _queue.Queue()
_hd_worker_lock = threading.Lock()
_hd_worker_started = False


def _run_hd_render(post_id: int) -> None:
    """HD 렌더 실행 (워커 스레드 내부에서 호출).

    SD 렌더링과 동일한 layout_renderer 파이프라인을 사용하되
    출력 파일명을 _FHD.mp4로 지정한다. GPU(_resolve_codec) 자동 선택.
    """
    try:
        from ai_worker.renderer.layout import render_layout_video_from_scenes
        from ai_worker.pipeline.resource_analyzer import analyze_resources
        from ai_worker.pipeline.scene_director import SceneDirector
        from ai_worker.pipeline.text_validator import validate_and_fix
        from config.settings import MEDIA_DIR as _MEDIA_DIR

        with SessionLocal() as _s:
            _post = _s.get(Post, post_id)
            _content = _s.query(Content).filter_by(post_id=post_id).first()
            _preview_path = (
                _MEDIA_DIR / _content.video_path if _content.video_path else None
            )

            # ScriptData 재구성
            _script = ScriptData.from_json(_content.summary_text)
            _script_dict = validate_and_fix({
                "hook": _script.hook,
                "body": list(_script.body),
                "closer": _script.closer,
            })

            # SD 렌더와 동일한 씬 배분
            _images = _post.images if isinstance(_post.images, list) else []
            _profile = analyze_resources(_post, _images)
            _scenes = SceneDirector(_profile, _images, _script_dict).direct()

            # FHD 출력 경로 명시
            _fhd_path = (
                _MEDIA_DIR / "video" / _post.site_code
                / f"post_{_post.origin_id}_FHD.mp4"
            )
            _fhd_path.parent.mkdir(parents=True, exist_ok=True)

            _tts_cache = _MEDIA_DIR / "tmp" / "tts_scene_cache" / str(post_id)
            _tts_cache_arg = _tts_cache if (_tts_cache / "durations.json").exists() else None
            if _tts_cache_arg:
                log.info("TTS 캐시 재사용: post_id=%d", post_id)
            _video = render_layout_video_from_scenes(
                _post, _scenes, output_path=_fhd_path, tts_audio_cache=_tts_cache_arg
            )
            _content.video_path = str(_video.relative_to(_MEDIA_DIR))
            _post.status = PostStatus.RENDERED
            _s.commit()

        # 렌더 완료 후 TTS 캐시 삭제
        shutil.rmtree(_tts_cache, ignore_errors=True)

        # SD 프리뷰 삭제 (_SD.mp4 vs _FHD.mp4로 항상 다른 파일)
        if _preview_path and _preview_path.exists():
            _preview_path.unlink()
            log.info("SD 프리뷰 삭제: %s", _preview_path)
        log.info("HD 렌더링 완료: post_id=%d", post_id)
    except Exception as _e:
        log.exception("HD 렌더링 실패: post_id=%d", post_id)
        hd_render_errors[post_id] = str(_e)
    finally:
        hd_render_pending.discard(post_id)


def _hd_render_worker() -> None:
    """HD 렌더 큐를 순서대로 소비하는 영구 워커 스레드."""
    while True:
        post_id = _hd_render_queue.get()
        try:
            log.info("HD 렌더 워커 시작: post_id=%d (대기 중=%d)", post_id, _hd_render_queue.qsize())
            _run_hd_render(post_id)
        finally:
            _hd_render_queue.task_done()


def enqueue_hd_render(post_id: int) -> None:
    """HD 렌더 요청을 큐에 추가. 워커 스레드가 없으면 생성."""
    global _hd_worker_started
    if post_id in hd_render_pending:
        log.warning("HD 렌더 요청 중복 무시: post_id=%d", post_id)
        return
    hd_render_pending.add(post_id)
    _hd_render_queue.put(post_id)
    with _hd_worker_lock:
        if not _hd_worker_started:
            _hd_worker_started = True
            threading.Thread(
                target=_hd_render_worker, daemon=True, name="hd-render-worker"
            ).start()
            log.info("HD 렌더 워커 스레드 시작")
