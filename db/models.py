import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Float, Index, Integer, BigInteger, String, Text, Enum, JSON,
    ForeignKey, DateTime, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class PostStatus(enum.Enum):
    COLLECTED = "COLLECTED"
    EDITING = "EDITING"               # 수신함 승인 후 편집실 대기
    APPROVED = "APPROVED"             # 편집실 확정 후 AI 워커 대기
    PROCESSING = "PROCESSING"
    PREVIEW_RENDERED = "PREVIEW_RENDERED"  # 저화질 프리뷰 완료 (고화질 렌더링 대기)
    RENDERED = "RENDERED"             # 고화질 렌더링 완료 (업로드 가능)
    UPLOADED = "UPLOADED"
    DECLINED = "DECLINED"
    FAILED = "FAILED"


def _utcnow():
    return datetime.now(timezone.utc)


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("site_code", "origin_id", name="uq_site_origin"),
        Index("ix_posts_engagement_score", "engagement_score"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    site_code = Column(String(32), nullable=False, index=True)
    origin_id = Column(String(64), nullable=False)
    title = Column(String(512), nullable=False)
    content = Column(Text, nullable=True)
    images = Column(JSON, nullable=True)
    stats = Column(JSON, nullable=True)
    status = Column(
        Enum(PostStatus), nullable=False, default=PostStatus.COLLECTED,
    )
    engagement_score = Column(Float, nullable=False, default=0.0, server_default="0.0")
    retry_count = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Post {self.site_code}:{self.origin_id} '{self.title[:30]}'>"


class Comment(Base):
    __tablename__ = "comments"
    __table_args__ = (
        UniqueConstraint("post_id", "author", "content_hash", name="uq_post_comment"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    post_id = Column(BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False, index=True)
    author = Column(String(128), nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    likes = Column(Integer, nullable=False, default=0)

    post = relationship("Post", back_populates="comments")

    def __repr__(self):
        return f"<Comment by {self.author} on post_id={self.post_id}>"


class Content(Base):
    __tablename__ = "contents"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    post_id = Column(
        BigInteger, ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    summary_text = Column(Text, nullable=True)
    audio_path = Column(String(255), nullable=True)
    video_path = Column(String(255), nullable=True)
    upload_meta = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    post = relationship("Post", backref="content_item")

    def get_script(self) -> "ScriptData | None":
        """
        summary_text에서 ScriptData 반환.

        - JSON 형식(v2): ScriptData 파싱 반환
        - 평문(레거시 v1): ScriptData(hook=전체텍스트, body=[], ...) 로 래핑 반환
        - None/빈값: None 반환
        """
        if not self.summary_text:
            return None

        import json as _json
        from ai_worker.llm import ScriptData

        try:
            return ScriptData.from_json(self.summary_text)
        except (_json.JSONDecodeError, KeyError, TypeError):
            # 레거시 평문 대본 — hook에 전체 텍스트 담아 반환
            return ScriptData(
                hook=self.summary_text[:15],
                body=[self.summary_text],
                closer="",
                title_suggestion="",
                tags=[],
                mood="funny",
            )
