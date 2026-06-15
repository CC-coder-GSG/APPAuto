from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum as SAEnum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import AssessmentStatus, QuestionType, SubmissionStatus
from app.utils.time_utils import local_now


class LearningTopic(Base):
    """学习主题：知识资料的容器，并对应一份或多份（多轮）考核。"""

    __tablename__ = "learning_topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    created_by = relationship("User", foreign_keys=[created_by_id])
    materials = relationship("LearningMaterial", back_populates="topic", cascade="all, delete-orphan")
    assessments = relationship("Assessment", back_populates="topic", cascade="all, delete-orphan")


class LearningMaterial(Base):
    """知识资料附件（沿用 feedback 附件字段范式）。"""

    __tablename__ = "learning_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=False, index=True)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    file_ext: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    file_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_image: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # LibreOffice 把 PPT/Word 转成的 PDF 预览产物（相对路径）；为空表示无在线预览，前端回退下载。
    preview_pdf_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)

    topic = relationship("LearningTopic", back_populates="materials")
    uploader = relationship("User", foreign_keys=[uploaded_by_id])


class Assessment(Base):
    """一份考核 = 一个学习周期（一轮）。同主题可有多轮，历史按轮保留。"""

    __tablename__ = "assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("learning_topics.id", ondelete="CASCADE"), nullable=False, index=True)
    round_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    author_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    status: Mapped[AssessmentStatus] = mapped_column(SAEnum(AssessmentStatus), default=AssessmentStatus.DRAFTING, nullable=False, index=True)
    total_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, onupdate=local_now, nullable=False)

    topic = relationship("LearningTopic", back_populates="assessments")
    author = relationship("User", foreign_keys=[author_id])
    questions = relationship(
        "AssessmentQuestion",
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="AssessmentQuestion.sort_order",
    )
    submissions = relationship("AssessmentSubmission", back_populates="assessment", cascade="all, delete-orphan")


class AssessmentQuestion(Base):
    __tablename__ = "assessment_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    type: Mapped[QuestionType] = mapped_column(SAEnum(QuestionType), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    # 选项（选择题）：JSON 数组字符串，如 [{"key":"A","text":"..."}, ...]
    options_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # 预设答案：JSON。单选/判断=字符串；多选=数组；填空=可接受答案数组；简答=参考答案字符串
    answer_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    assessment = relationship("Assessment", back_populates="questions")


class AssessmentSubmission(Base):
    __tablename__ = "assessment_submissions"
    __table_args__ = (UniqueConstraint("assessment_id", "user_id", name="uq_assessment_submission_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    status: Mapped[SubmissionStatus] = mapped_column(SAEnum(SubmissionStatus), default=SubmissionStatus.SUBMITTED, nullable=False, index=True)
    objective_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    subjective_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=local_now, nullable=False)
    graded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    assessment = relationship("Assessment", back_populates="submissions")
    user = relationship("User", foreign_keys=[user_id])
    answers = relationship("AssessmentAnswer", back_populates="submission", cascade="all, delete-orphan")


class AssessmentAnswer(Base):
    __tablename__ = "assessment_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("assessment_submissions.id", ondelete="CASCADE"), nullable=False, index=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("assessment_questions.id", ondelete="CASCADE"), nullable=False, index=True)
    # 作答内容：JSON。单选/判断=字符串；多选=数组；填空=数组（按空位）；简答=字符串
    response_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    auto_graded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    score: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    grader_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    submission = relationship("AssessmentSubmission", back_populates="answers")
    question = relationship("AssessmentQuestion")


__all__ = [
    "LearningTopic",
    "LearningMaterial",
    "Assessment",
    "AssessmentQuestion",
    "AssessmentSubmission",
    "AssessmentAnswer",
]
