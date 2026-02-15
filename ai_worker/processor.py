import logging
from pathlib import Path

from sqlalchemy.orm import Session

from ai_worker.llm import summarize
from ai_worker.tts import get_tts_engine
from ai_worker.video import render_video
from config.settings import MEDIA_DIR, load_pipeline_config
from db.models import Content, Post, PostStatus

logger = logging.getLogger(__name__)


async def process(post: Post, session: Session) -> None:
    cfg = load_pipeline_config()

    post.status = PostStatus.PROCESSING
    session.commit()
    logger.info("처리 시작: post_id=%d title=%s", post.id, post.title[:40])

    # 1. LLM 요약
    best_comments = sorted(post.comments, key=lambda c: c.likes, reverse=True)[:5]
    comment_texts = [f"{c.author}: {c.content[:100]}" for c in best_comments]

    summary_text = summarize(
        title=post.title,
        body=post.content or "",
        comments=comment_texts,
        model=cfg.get("llm_model"),
    )

    # 2. TTS 생성 (async)
    tts_engine = get_tts_engine(cfg["tts_engine"])
    voice_id = cfg["tts_voice"]
    audio_dir = MEDIA_DIR / "audio"
    audio_path = audio_dir / f"post_{post.id}.mp3"
    await tts_engine.synthesize(summary_text, voice_id, audio_path)

    # 3. 영상 생성
    video_path = render_video(post, audio_path, summary_text, cfg)

    # 4. Content 레코드 생성
    content = session.query(Content).filter(Content.post_id == post.id).first()
    if content is None:
        content = Content(post_id=post.id)
        session.add(content)

    content.summary_text = summary_text
    content.audio_path = str(audio_path)
    content.video_path = str(video_path)

    post.status = PostStatus.RENDERED
    session.commit()
    logger.info("처리 완료: post_id=%d → RENDERED", post.id)
