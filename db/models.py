import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, BigInteger, String, Text, Enum, JSON,
    ForeignKey, DateTime, UniqueConstraint,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class PostStatus(enum.Enum):
    COLLECTED = "COLLECTED"
    APPROVED = "APPROVED"
    PROCESSING = "PROCESSING"
    RENDERED = "RENDERED"
    UPLOADED = "UPLOADED"
    DECLINED = "DECLINED"


def _utcnow():
    return datetime.now(timezone.utc)


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("site_code", "origin_id", name="uq_site_origin"),
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
