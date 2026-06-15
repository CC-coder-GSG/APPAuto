from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.integrations.wecom import send_markdown
from app.models import User
from app.services.learning_service import LearningService
from app.services.permission_service import ensure_tab_access

router = APIRouter(prefix="/learning", tags=["learning"])


class TopicCreatePayload(BaseModel):
    title: str
    description: Optional[str] = None


class QuestionPayload(BaseModel):
    type: str
    prompt: str
    options: Optional[list[Any]] = None
    answer: Optional[Any] = None
    score: int = 0


class QuestionsUpsertPayload(BaseModel):
    questions: list[QuestionPayload] = Field(default_factory=list)


class SubmitPayload(BaseModel):
    # {question_id: response}
    answers: dict[str, Any] = Field(default_factory=dict)


class GradePayload(BaseModel):
    score: int
    comment: Optional[str] = None


# ---------------------------- 知识 ----------------------------
@router.post("/topics")
def create_topic(payload: TopicCreatePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "learning")
    return LearningService(db).create_topic(title=payload.title, description=payload.description, actor=current_user)


@router.get("/topics")
def list_topics(_: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return LearningService(db).list_topics()


@router.get("/topics/{topic_id}")
def get_topic(topic_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return LearningService(db).get_topic(topic_id)


@router.post("/topics/{topic_id}/materials")
def upload_material(topic_id: int, file: UploadFile, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "learning")
    return LearningService(db).save_material(topic_id=topic_id, upload_file=file, actor=current_user)


@router.delete("/materials/{material_id}")
def delete_material(material_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "learning")
    return LearningService(db).delete_material(material_id, current_user)


@router.get("/materials/{material_id}/download")
def download_material(material_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc = LearningService(db)
    mat = svc.get_material(material_id)
    full, name, mime = svc.resolve_file(mat, prefer_preview=False)
    if not Path(full).exists():
        raise HTTPException(status_code=404, detail="文件不存在或已被清理")
    return FileResponse(str(full), filename=name, media_type=mime)


@router.get("/materials/{material_id}/preview")
def preview_material(material_id: int, _: User = Depends(get_current_user), db: Session = Depends(get_db)):
    svc = LearningService(db)
    mat = svc.get_material(material_id)
    full, name, mime = svc.resolve_file(mat, prefer_preview=True)
    if not Path(full).exists():
        raise HTTPException(status_code=404, detail="预览不可用，请下载查看")
    # 内联预览：不带 attachment 下载头
    return FileResponse(str(full), media_type=mime)


# ---------------------------- 考核 ----------------------------
@router.post("/topics/{topic_id}/assessments")
def create_assessment(topic_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "learning")
    return LearningService(db).create_assessment(topic_id=topic_id, actor=current_user)


@router.put("/assessments/{assessment_id}/questions")
def upsert_questions(assessment_id: int, payload: QuestionsUpsertPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "learning")
    questions = [q.model_dump() for q in payload.questions]
    return LearningService(db).upsert_questions(assessment_id=assessment_id, questions=questions, actor=current_user)


@router.get("/assessments/{assessment_id}/edit")
def get_assessment_for_edit(assessment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "learning")
    return LearningService(db).get_assessment_for_edit(assessment_id, current_user)


@router.post("/assessments/{assessment_id}/publish")
async def publish_assessment(assessment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "learning")
    result = LearningService(db).publish_assessment(assessment_id=assessment_id, actor=current_user)
    if result.get("notice"):
        await send_markdown(result["notice"])
    return {"message": result["message"]}


@router.get("/assessments/{assessment_id}/paper")
def get_paper(assessment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return LearningService(db).get_paper(assessment_id, current_user)


@router.post("/assessments/{assessment_id}/submit")
def submit_answers(assessment_id: int, payload: SubmitPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return LearningService(db).submit_answers(assessment_id=assessment_id, answers=payload.answers, actor=current_user)


@router.get("/assessments/{assessment_id}/grading-queue")
def grading_queue(assessment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "learning")
    return LearningService(db).grading_queue(assessment_id, current_user)


@router.post("/answers/{answer_id}/grade")
def grade_short_answer(answer_id: int, payload: GradePayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "learning")
    return LearningService(db).grade_short_answer(answer_id=answer_id, score=payload.score, comment=payload.comment, actor=current_user)


@router.post("/assessments/{assessment_id}/finalize")
async def finalize_assessment(assessment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_tab_access(current_user, "learning")
    result = LearningService(db).finalize(assessment_id=assessment_id, actor=current_user)
    if result.get("notice"):
        await send_markdown(result["notice"])
    return {"message": result["message"]}


@router.get("/assessments/{assessment_id}/results")
def assessment_results(assessment_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return LearningService(db).results(assessment_id, current_user)
