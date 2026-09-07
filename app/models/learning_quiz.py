from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class LearningContentKind(str, Enum):
    MATERIAL = "material"
    COURSE = "course"


class LearningQuizStatus(str, Enum):
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"


class LearningQuestionType(str, Enum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"


class FillGradingMode(str, Enum):
    CONTAINS = "contains"
    EXACT = "exact"
    NORMALIZED = "normalized"
    MATCH_ANY = "match_any"
    MATCH_COUNT = "match_count"


class LearningContent(Base):
    __tablename__ = "learning_contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    kind: Mapped[LearningContentKind] = mapped_column(
        SAEnum(LearningContentKind, name="learning_content_kind"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resource_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    creator = relationship("User", foreign_keys=[creator_id])


class LearningQuiz(Base):
    __tablename__ = "learning_quizzes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[LearningQuizStatus] = mapped_column(
        SAEnum(LearningQuizStatus, name="learning_quiz_status"),
        default=LearningQuizStatus.DRAFT,
        nullable=False,
        index=True,
    )
    results_released: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    show_answers: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    creator = relationship("User", foreign_keys=[creator_id])
    questions = relationship(
        "LearningQuestion",
        back_populates="quiz",
        cascade="all, delete-orphan",
        order_by="LearningQuestion.position",
    )
    submissions = relationship(
        "LearningSubmission", back_populates="quiz", cascade="all, delete-orphan"
    )


class LearningQuestion(Base):
    __tablename__ = "learning_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("learning_quizzes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    question_type: Mapped[LearningQuestionType] = mapped_column(
        SAEnum(LearningQuestionType, name="learning_question_type"), nullable=False
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    options_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    correct_answers_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fill_grading_mode: Mapped[Optional[FillGradingMode]] = mapped_column(
        SAEnum(FillGradingMode, name="learning_fill_grading_mode"), nullable=True
    )
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    quiz = relationship("LearningQuiz", back_populates="questions")
    answers = relationship("LearningAnswer", back_populates="question")


class LearningSubmission(Base):
    __tablename__ = "learning_submissions"
    __table_args__ = (
        UniqueConstraint("quiz_id", "user_id", "is_preview", name="uq_quiz_user_preview"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    quiz_id: Mapped[int] = mapped_column(
        ForeignKey("learning_quizzes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    is_preview: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="submitted", nullable=False)
    auto_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    graded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    quiz = relationship("LearningQuiz", back_populates="submissions")
    user = relationship("User", foreign_keys=[user_id])
    answers = relationship(
        "LearningAnswer", back_populates="submission", cascade="all, delete-orphan"
    )


class LearningAnswer(Base):
    __tablename__ = "learning_answers"
    __table_args__ = (
        UniqueConstraint("submission_id", "question_id", name="uq_submission_question"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("learning_submissions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("learning_questions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    answer_json: Mapped[str] = mapped_column(Text, nullable=False, default="null")
    is_correct: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    awarded_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reviewer_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    submission = relationship("LearningSubmission", back_populates="answers")
    question = relationship("LearningQuestion", back_populates="answers")


__all__ = [
    "FillGradingMode",
    "LearningAnswer",
    "LearningContent",
    "LearningContentKind",
    "LearningQuestion",
    "LearningQuestionType",
    "LearningQuiz",
    "LearningQuizStatus",
    "LearningSubmission",
]
