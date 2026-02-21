import enum
import json as _json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, Float, Index, Integer, BigInteger, String, Text, Enum, JSON,
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
    # A/B 테스트 — 활성 테스트가 없으면 모두 NULL
    variant_group = Column(String(64), nullable=True)   # A/B 테스트 그룹 ID
    variant_label = Column(String(32), nullable=True)    # "A" 또는 "B"
    variant_config = Column(JSON, nullable=True)         # 해당 variant의 설정값

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

        try:
            return ScriptData.from_json(self.summary_text)
        except (_json.JSONDecodeError, KeyError, TypeError):
            # 레거시 평문 대본 — hook에 전체 텍스트 담아 반환
            return ScriptData(
                hook=self.summary_text[:15],
                body=[{"line_count": 1, "lines": [self.summary_text]}],
                closer="",
                title_suggestion="",
                tags=[],
                mood="funny",
            )


class LLMLog(Base):
    """LLM 호출 이력 — 프롬프트 튜닝용 상세 로그.

    call_type:
        'generate_script' — llm.generate_script() (ScriptData 생성)
        'chunk'           — llm_chunker.chunk_with_llm() (청킹 전용)
    """
    __tablename__ = "llm_logs"
    __table_args__ = (
        Index("ix_llm_logs_post_id",    "post_id"),
        Index("ix_llm_logs_call_type",  "call_type"),
        Index("ix_llm_logs_created_at", "created_at"),
    )

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    post_id = Column(
        BigInteger,
        ForeignKey("posts.id", ondelete="SET NULL"),
        nullable=True,
    )

    # 호출 메타
    call_type   = Column(String(32),  nullable=False)   # generate_script | chunk
    model_name  = Column(String(64),  nullable=True)
    strategy    = Column(String(32),  nullable=True)     # img_heavy|balanced|text_heavy
    image_count    = Column(Integer,  nullable=False, default=0, server_default="0")
    content_length = Column(Integer,  nullable=False, default=0, server_default="0")

    # 프롬프트 / 응답 (TEXT: ~64KB — 운영 후 필요 시 MEDIUMTEXT 마이그레이션)
    prompt_text  = Column(Text, nullable=True)
    raw_response = Column(Text, nullable=True)
    parsed_result = Column(JSON, nullable=True)   # validate_and_fix 후 최종 dict

    # 결과
    success       = Column(Boolean, nullable=False, default=True, server_default="1")
    error_message = Column(Text,    nullable=True)
    duration_ms   = Column(Integer, nullable=True)   # 밀리초

    created_at = Column(DateTime, nullable=False, default=_utcnow)


# ---------------------------------------------------------------------------
# ScriptData — 구조화 대본 (ai_worker/llm/client.py에서 이동)
# ---------------------------------------------------------------------------

@dataclass
class ScriptData:
    """구조화된 쇼츠 대본 데이터.

    ai_worker/llm.py에서 생성하고 Content.summary_text에 JSON으로 저장.

    body 포맷 (v2): [{"line_count": int, "lines": list[str]}, ...]
      - lines 요소는 각 21자 이내 (렌더러 슬롯 한 줄에 대응)
      - 하위 호환: from_json에서 str 항목 → dict 자동 변환
    """
    hook: str
    body: list[dict]
    closer: str
    title_suggestion: str
    tags: list[str]
    mood: str = "funny"

    def to_plain_text(self) -> str:
        texts = [self.hook]
        for item in self.body:
            if isinstance(item, dict):
                texts.append(" ".join(item.get("lines", [])))
            else:
                texts.append(str(item))
        texts.append(self.closer)
        return " ".join(texts)

    def to_json(self) -> str:
        return _json.dumps(
            {
                "hook": self.hook,
                "body": self.body,
                "closer": self.closer,
                "title_suggestion": self.title_suggestion,
                "tags": self.tags,
                "mood": self.mood,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "ScriptData":
        d = _json.loads(raw)
        body_raw = d.get("body", [])
        # 하위 호환: 기존 str 항목 → dict 변환
        body: list[dict] = []
        for item in body_raw:
            if isinstance(item, str):
                body.append({"line_count": 1, "lines": [item]})
            else:
                body.append(item)
        return cls(
            hook=d["hook"],
            body=body,
            closer=d["closer"],
            title_suggestion=d["title_suggestion"],
            tags=d["tags"],
            mood=d.get("mood", "funny"),
        )
