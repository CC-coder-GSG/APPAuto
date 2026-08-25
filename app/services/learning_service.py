from __future__ import annotations

import json
import mimetypes
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session, joinedload

from app.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentQuestion,
    AssessmentStatus,
    AssessmentSubmission,
    LearningMaterial,
    LearningTopic,
    QuestionType,
    SubmissionStatus,
    User,
)
from app.services.audit_service import audit
from app.services.sse_service import sse_publish
from app.utils.office_convert import CONVERTIBLE_EXTS, convert_to_pdf
from app.utils.time_utils import local_now

PROJECT_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_ROOT = PROJECT_ROOT / "uploads" / "learning"
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}
# 可在浏览器内联预览（无需转换）的类型
INLINE_PREVIEW_EXTS = {".pdf", ".txt", ".md", ".markdown", ".csv", ".log"}

OBJECTIVE_TYPES = {QuestionType.SINGLE, QuestionType.MULTI, QuestionType.JUDGE, QuestionType.BLANK}


def _norm(value) -> str:
    return str(value if value is not None else "").strip().lower()


class LearningService:
    def __init__(self, db: Session):
        self.db = db

    # ============================ 知识 ============================
    def create_topic(self, *, title: str, description: str | None, actor: User) -> dict:
        title = (title or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="主题标题不能为空")
        topic = LearningTopic(title=title, description=(description or "").strip() or None, created_by_id=actor.id)
        self.db.add(topic)
        self.db.commit()
        self.db.refresh(topic)
        audit(self.db, action="learning.create_topic", target_type="learning_topic", actor_id=actor.id, target_id=str(topic.id), detail=title)
        return {"id": topic.id, "message": "主题已创建"}

    def list_topics(self) -> list[dict]:
        topics = (
            self.db.query(LearningTopic)
            .options(joinedload(LearningTopic.created_by), joinedload(LearningTopic.assessments))
            .order_by(LearningTopic.created_at.desc())
            .all()
        )
        result = []
        for t in topics:
            material_count = self.db.query(LearningMaterial).filter(LearningMaterial.topic_id == t.id).count()
            latest = max(t.assessments, key=lambda a: a.round_no, default=None) if t.assessments else None
            result.append(
                {
                    "id": t.id,
                    "title": t.title,
                    "description": t.description,
                    "created_by_id": t.created_by_id,
                    "created_by_name": t.created_by.shown_name if t.created_by else None,
                    "created_at": t.created_at.isoformat(),
                    "material_count": material_count,
                    "assessment_count": len(t.assessments),
                    "latest_assessment": {
                        "id": latest.id,
                        "round_no": latest.round_no,
                        "status": latest.status.value,
                    }
                    if latest
                    else None,
                }
            )
        return result

    def get_topic(self, topic_id: int) -> dict:
        topic = (
            self.db.query(LearningTopic)
            .options(joinedload(LearningTopic.created_by))
            .filter(LearningTopic.id == topic_id)
            .first()
        )
        if not topic:
            raise HTTPException(status_code=404, detail="主题不存在")
        materials = (
            self.db.query(LearningMaterial)
            .filter(LearningMaterial.topic_id == topic_id)
            .order_by(LearningMaterial.id.desc())
            .all()
        )
        assessments = (
            self.db.query(Assessment)
            .options(joinedload(Assessment.author))
            .filter(Assessment.topic_id == topic_id)
            .order_by(Assessment.round_no.desc())
            .all()
        )
        return {
            "id": topic.id,
            "title": topic.title,
            "description": topic.description,
            "created_by_name": topic.created_by.shown_name if topic.created_by else None,
            "created_at": topic.created_at.isoformat(),
            "materials": [self._material_view(m) for m in materials],
            "assessments": [
                {
                    "id": a.id,
                    "round_no": a.round_no,
                    "status": a.status.value,
                    "author_name": a.author.shown_name if a.author else None,
                    "author_id": a.author_id,
                    "total_score": a.total_score,
                    "question_count": self.db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == a.id).count(),
                    "published_at": a.published_at.isoformat() if a.published_at else None,
                    "created_at": a.created_at.isoformat(),
                }
                for a in assessments
            ],
        }

    def delete_topic(self, topic_id: int, actor: User) -> dict:
        topic = self.db.query(LearningTopic).filter(LearningTopic.id == topic_id).first()
        if not topic:
            raise HTTPException(status_code=404, detail="主题不存在")
        if actor.role.value != "admin" and topic.created_by_id != actor.id:
            raise HTTPException(status_code=403, detail="仅主题创建人或管理员可删除")

        # 先删物理文件（资料原件 + LibreOffice 转出的预览 PDF），
        # 再删主题（ORM cascade 连带删除资料/考核/题目/作答/批改记录）。
        materials = self.db.query(LearningMaterial).filter(LearningMaterial.topic_id == topic_id).all()
        for m in materials:
            for rel in (m.file_path, m.preview_pdf_path):
                if not rel:
                    continue
                p = PROJECT_ROOT / rel
                if p.exists():
                    try:
                        p.unlink()
                    except Exception:
                        pass

        title = topic.title
        self.db.delete(topic)
        self.db.commit()
        audit(self.db, action="learning.delete_topic", target_type="learning_topic", actor_id=actor.id, target_id=str(topic_id), detail=title)
        return {"message": "主题已删除"}

    def _material_view(self, m: LearningMaterial) -> dict:
        ext = (m.file_ext or "").lower()
        previewable = bool(m.preview_pdf_path) or ext in INLINE_PREVIEW_EXTS or bool(m.is_image)
        preview_kind = (
            "image" if m.is_image else
            "pdf" if (ext == ".pdf" or m.preview_pdf_path) else
            "text" if ext in {".txt", ".md", ".markdown", ".csv", ".log"} else
            "none"
        )
        return {
            "id": m.id,
            "original_name": m.original_name,
            "file_ext": m.file_ext,
            "file_type": m.file_type,
            "file_size": m.file_size,
            "is_image": m.is_image,
            "previewable": previewable,
            "preview_kind": preview_kind,
            "created_at": m.created_at.isoformat(),
        }

    def save_material(self, *, topic_id: int, upload_file: UploadFile, actor: User) -> dict:
        topic = self.db.query(LearningTopic).filter(LearningTopic.id == topic_id).first()
        if not topic:
            raise HTTPException(status_code=404, detail="主题不存在")
        now = local_now()
        folder = UPLOAD_ROOT / now.strftime("%Y") / now.strftime("%m")
        folder.mkdir(parents=True, exist_ok=True)
        suffix = Path(upload_file.filename or "").suffix.lower()
        stored_name = f"{uuid.uuid4().hex}{suffix}"
        full_path = folder / stored_name

        content = upload_file.file.read()
        with open(full_path, "wb") as f:
            f.write(content)

        guessed_type = upload_file.content_type or mimetypes.guess_type(upload_file.filename or "")[0] or "application/octet-stream"
        is_image = suffix in IMAGE_EXTS or guessed_type.startswith("image/")
        rel_path = str(full_path.relative_to(PROJECT_ROOT)).replace("\\", "/")

        # PPT/Word 等：尝试生成 PDF 预览（LibreOffice 缺失/失败时静默降级为仅下载）
        preview_rel = None
        if suffix in CONVERTIBLE_EXTS:
            pdf_path = convert_to_pdf(full_path, folder)
            if pdf_path is not None:
                preview_rel = str(Path(pdf_path).relative_to(PROJECT_ROOT)).replace("\\", "/")

        mat = LearningMaterial(
            topic_id=topic_id,
            original_name=upload_file.filename or stored_name,
            stored_name=stored_name,
            file_path=rel_path,
            file_type=guessed_type,
            file_ext=suffix,
            file_size=len(content),
            is_image=is_image,
            preview_pdf_path=preview_rel,
            uploaded_by_id=actor.id,
        )
        self.db.add(mat)
        self.db.commit()
        self.db.refresh(mat)
        audit(self.db, action="learning.upload_material", target_type="learning_topic", actor_id=actor.id, target_id=str(topic_id), detail=mat.original_name)
        return {"id": mat.id, "message": "资料上传成功", "preview_kind": self._material_view(mat)["preview_kind"]}

    def get_material(self, material_id: int) -> LearningMaterial:
        row = self.db.query(LearningMaterial).filter(LearningMaterial.id == material_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="资料不存在")
        return row

    def resolve_file(self, material: LearningMaterial, *, prefer_preview: bool) -> tuple[Path, str, str]:
        """返回 (绝对路径, 下载文件名, mime)。prefer_preview 时优先返回转换出的 PDF。"""
        if prefer_preview and material.preview_pdf_path:
            full = PROJECT_ROOT / material.preview_pdf_path
            name = Path(material.original_name).stem + ".pdf"
            return full, name, "application/pdf"
        full = PROJECT_ROOT / material.file_path
        return full, material.original_name, (material.file_type or "application/octet-stream")

    def delete_material(self, material_id: int, actor: User) -> dict:
        row = self.get_material(material_id)
        if actor.role.value != "admin" and row.uploaded_by_id != actor.id:
            raise HTTPException(status_code=403, detail="无权限删除该资料")
        for rel in (row.file_path, row.preview_pdf_path):
            if not rel:
                continue
            p = PROJECT_ROOT / rel
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        tid = row.topic_id
        name = row.original_name
        self.db.delete(row)
        self.db.commit()
        audit(self.db, action="learning.delete_material", target_type="learning_topic", actor_id=actor.id, target_id=str(tid), detail=name)
        return {"message": "资料已删除"}

    # ============================ 考核 ============================
    def _get_assessment(self, assessment_id: int) -> Assessment:
        a = self.db.query(Assessment).filter(Assessment.id == assessment_id).first()
        if not a:
            raise HTTPException(status_code=404, detail="考核不存在")
        return a

    def _ensure_author(self, assessment: Assessment, actor: User) -> None:
        if actor.role.value != "admin" and assessment.author_id != actor.id:
            raise HTTPException(status_code=403, detail="仅出题人或管理员可执行该操作")

    def create_assessment(self, *, topic_id: int, actor: User) -> dict:
        topic = self.db.query(LearningTopic).filter(LearningTopic.id == topic_id).first()
        if not topic:
            raise HTTPException(status_code=404, detail="主题不存在")
        # 同主题不允许同时存在未公示（进行中）的考核，避免并行多轮
        active = (
            self.db.query(Assessment)
            .filter(Assessment.topic_id == topic_id, Assessment.status != AssessmentStatus.PUBLISHED)
            .first()
        )
        if active:
            raise HTTPException(status_code=400, detail="该主题已有进行中的考核，请先完成并公示后再开启新一轮")
        max_round = self.db.query(Assessment).filter(Assessment.topic_id == topic_id).count()
        a = Assessment(topic_id=topic_id, round_no=max_round + 1, author_id=actor.id, status=AssessmentStatus.DRAFTING)
        self.db.add(a)
        self.db.commit()
        self.db.refresh(a)
        audit(self.db, action="learning.create_assessment", target_type="assessment", actor_id=actor.id, target_id=str(a.id), detail=f"topic={topic_id},round={a.round_no}")
        return {"id": a.id, "round_no": a.round_no, "message": "考核已创建，请录入题目"}

    def upsert_questions(self, *, assessment_id: int, questions: list[dict], actor: User) -> dict:
        a = self._get_assessment(assessment_id)
        self._ensure_author(a, actor)
        if a.status != AssessmentStatus.DRAFTING:
            raise HTTPException(status_code=400, detail="考核已发布，无法再修改题目")

        # 全量替换
        self.db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment_id).delete()
        total = 0
        for idx, q in enumerate(questions):
            try:
                qtype = QuestionType(q.get("type"))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"第 {idx + 1} 题题型非法")
            prompt = (q.get("prompt") or "").strip()
            if not prompt:
                raise HTTPException(status_code=400, detail=f"第 {idx + 1} 题题干不能为空")
            try:
                score = int(q.get("score") or 0)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"第 {idx + 1} 题分值非法")
            if score <= 0:
                raise HTTPException(status_code=400, detail=f"第 {idx + 1} 题分值需为正整数")
            self.db.add(
                AssessmentQuestion(
                    assessment_id=assessment_id,
                    type=qtype,
                    prompt=prompt,
                    options_json=json.dumps(q.get("options") or [], ensure_ascii=False) if q.get("options") is not None else None,
                    answer_json=json.dumps(q.get("answer"), ensure_ascii=False) if q.get("answer") is not None else None,
                    score=score,
                    sort_order=idx,
                )
            )
            total += score
        a.total_score = total
        self.db.commit()
        audit(self.db, action="learning.upsert_questions", target_type="assessment", actor_id=actor.id, target_id=str(assessment_id), detail=f"count={len(questions)},total={total}")
        return {"message": "题目已保存", "question_count": len(questions), "total_score": total}

    def get_assessment_for_edit(self, assessment_id: int, actor: User) -> dict:
        a = self._get_assessment(assessment_id)
        self._ensure_author(a, actor)
        qs = self.db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment_id).order_by(AssessmentQuestion.sort_order).all()
        return {
            "id": a.id,
            "topic_id": a.topic_id,
            "round_no": a.round_no,
            "status": a.status.value,
            "total_score": a.total_score,
            "questions": [self._question_view(q, include_answer=True) for q in qs],
        }

    def _question_view(self, q: AssessmentQuestion, *, include_answer: bool) -> dict:
        view = {
            "id": q.id,
            "type": q.type.value,
            "prompt": q.prompt,
            "options": json.loads(q.options_json) if q.options_json else [],
            "score": q.score,
            "sort_order": q.sort_order,
        }
        if include_answer:
            view["answer"] = json.loads(q.answer_json) if q.answer_json else None
        return view

    def publish_assessment(self, *, assessment_id: int, actor: User) -> dict:
        a = self._get_assessment(assessment_id)
        self._ensure_author(a, actor)
        if a.status != AssessmentStatus.DRAFTING:
            raise HTTPException(status_code=400, detail="该考核已发布")
        q_count = self.db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment_id).count()
        if q_count == 0:
            raise HTTPException(status_code=400, detail="请先录入至少一道题目")
        a.status = AssessmentStatus.ANSWERING
        self.db.commit()
        topic = self.db.query(LearningTopic).filter(LearningTopic.id == a.topic_id).first()
        sse_publish(
            "learning_assessment_published",
            {"assessment_id": a.id, "topic_id": a.topic_id, "topic_title": topic.title if topic else "", "author_id": a.author_id},
            channels=["global"],
        )
        audit(self.db, action="learning.publish_assessment", target_type="assessment", actor_id=actor.id, target_id=str(a.id), detail=f"questions={q_count}")
        notice = (
            "### 📚 新考核已发布，请作答\n"
            f"> 主题：**{topic.title if topic else ''}**（第 {a.round_no} 轮）\n"
            f"> 出题人：@{actor.shown_name}\n"
            f"> 共 {q_count} 题、{a.total_score} 分，请到「学习中心」完成答题。"
        )
        return {"message": "考核已发布，已通知成员答题", "notice": notice}

    def get_paper(self, assessment_id: int, actor: User) -> dict:
        a = self._get_assessment(assessment_id)
        if a.status == AssessmentStatus.DRAFTING:
            raise HTTPException(status_code=400, detail="考核尚未发布")
        if a.author_id == actor.id:
            raise HTTPException(status_code=403, detail="出题人无需作答自己出的考核")
        existing = (
            self.db.query(AssessmentSubmission)
            .filter(AssessmentSubmission.assessment_id == assessment_id, AssessmentSubmission.user_id == actor.id)
            .first()
        )
        qs = self.db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment_id).order_by(AssessmentQuestion.sort_order).all()
        return {
            "id": a.id,
            "topic_id": a.topic_id,
            "round_no": a.round_no,
            "status": a.status.value,
            "total_score": a.total_score,
            "already_submitted": existing is not None,
            "my_submission_status": existing.status.value if existing else None,
            # 答题视图绝不下发预设答案
            "questions": [self._question_view(q, include_answer=False) for q in qs],
        }

    def submit_answers(self, *, assessment_id: int, answers: dict, actor: User) -> dict:
        a = self._get_assessment(assessment_id)
        if a.status != AssessmentStatus.ANSWERING:
            raise HTTPException(status_code=400, detail="该考核当前不可作答")
        if a.author_id == actor.id:
            raise HTTPException(status_code=403, detail="出题人无需作答自己出的考核")
        if self.db.query(AssessmentSubmission).filter(
            AssessmentSubmission.assessment_id == assessment_id, AssessmentSubmission.user_id == actor.id
        ).first():
            raise HTTPException(status_code=400, detail="您已提交过本次考核，不能重复作答")

        qs = self.db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment_id).all()
        submission = AssessmentSubmission(assessment_id=assessment_id, user_id=actor.id, status=SubmissionStatus.SUBMITTED)
        self.db.add(submission)
        self.db.flush()

        objective_score = 0
        has_short = False
        # answers: {str(question_id): response}
        norm_answers = {str(k): v for k, v in (answers or {}).items()}
        for q in qs:
            response = norm_answers.get(str(q.id))
            earned, auto = self._grade_objective(q, response)
            if not auto:
                has_short = True
            objective_score += earned
            self.db.add(
                AssessmentAnswer(
                    submission_id=submission.id,
                    question_id=q.id,
                    response_json=json.dumps(response, ensure_ascii=False) if response is not None else None,
                    auto_graded=auto,
                    score=earned,
                )
            )

        submission.objective_score = objective_score
        if has_short:
            submission.status = SubmissionStatus.SUBMITTED
            submission.total_score = objective_score  # 暂计客观分，待简答批改后重算
        else:
            submission.status = SubmissionStatus.GRADED
            submission.subjective_score = 0
            submission.total_score = objective_score
            submission.graded_at = local_now()
        self.db.commit()
        audit(self.db, action="learning.submit", target_type="assessment", actor_id=actor.id, target_id=str(assessment_id), detail=f"objective={objective_score},has_short={has_short}")
        return {
            "message": "作答已提交" + ("，简答题将由出题人批改" if has_short else "，已自动判分"),
            "objective_score": objective_score,
            "pending_manual_grading": has_short,
        }

    def _grade_objective(self, q: AssessmentQuestion, response) -> tuple[int, bool]:
        """返回 (得分, 是否自动判分)。简答返回 (0, False)。"""
        answer = json.loads(q.answer_json) if q.answer_json else None
        if q.type in (QuestionType.SINGLE, QuestionType.JUDGE):
            correct = _norm(answer)
            got = _norm(response)
            return (q.score if got and got == correct else 0, True)
        if q.type == QuestionType.MULTI:
            correct_set = {_norm(x) for x in (answer or [])}
            got_set = {_norm(x) for x in response} if isinstance(response, list) else set()
            return (q.score if correct_set and got_set == correct_set else 0, True)
        if q.type == QuestionType.BLANK:
            acceptable = {_norm(x) for x in (answer or [])}
            got = _norm(response)
            return (q.score if got and got in acceptable else 0, True)
        # SHORT
        return (0, False)

    def grading_queue(self, assessment_id: int, actor: User) -> dict:
        a = self._get_assessment(assessment_id)
        self._ensure_author(a, actor)
        # 所有提交里待人工判分（简答）的作答
        rows = (
            self.db.query(AssessmentAnswer)
            .join(AssessmentSubmission, AssessmentAnswer.submission_id == AssessmentSubmission.id)
            .join(AssessmentQuestion, AssessmentAnswer.question_id == AssessmentQuestion.id)
            .options(joinedload(AssessmentAnswer.submission).joinedload(AssessmentSubmission.user), joinedload(AssessmentAnswer.question))
            .filter(AssessmentSubmission.assessment_id == assessment_id, AssessmentAnswer.auto_graded.is_(False))
            .all()
        )
        items = []
        for r in rows:
            q = r.question
            items.append(
                {
                    "answer_id": r.id,
                    "submission_id": r.submission_id,
                    "user_name": r.submission.user.shown_name if r.submission and r.submission.user else "未知",
                    "question_prompt": q.prompt if q else "",
                    "max_score": q.score if q else 0,
                    "reference_answer": (json.loads(q.answer_json) if q and q.answer_json else None),
                    "response": json.loads(r.response_json) if r.response_json else None,
                    "current_score": r.score,
                    "graded": r.grader_comment is not None or r.score > 0,
                }
            )
        pending = sum(1 for r in rows if not (r.grader_comment is not None or r.score > 0))
        return {"assessment_id": assessment_id, "status": a.status.value, "items": items, "pending_count": pending, "total_short": len(rows)}

    def grade_short_answer(self, *, answer_id: int, score: int, comment: str | None, actor: User) -> dict:
        ans = (
            self.db.query(AssessmentAnswer)
            .options(joinedload(AssessmentAnswer.submission), joinedload(AssessmentAnswer.question))
            .filter(AssessmentAnswer.id == answer_id)
            .first()
        )
        if not ans:
            raise HTTPException(status_code=404, detail="作答不存在")
        a = self._get_assessment(ans.submission.assessment_id)
        self._ensure_author(a, actor)
        if ans.auto_graded:
            raise HTTPException(status_code=400, detail="客观题由系统判分，无需人工批改")
        max_score = ans.question.score if ans.question else 0
        try:
            score = int(score)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="分值非法")
        if score < 0 or score > max_score:
            raise HTTPException(status_code=400, detail=f"分值需在 0~{max_score} 之间")
        ans.score = score
        ans.grader_comment = (comment or "").strip() or None

        # 重算该 submission 总分；若简答全部已批改则标记 graded
        submission = ans.submission
        short_answers = [x for x in submission.answers if not x.auto_graded]
        all_graded = all((x.grader_comment is not None or x.score > 0) or x.id == ans.id for x in short_answers)
        subjective = sum(x.score for x in short_answers)
        submission.subjective_score = subjective
        submission.total_score = submission.objective_score + subjective
        if all_graded:
            submission.status = SubmissionStatus.GRADED
            submission.graded_at = local_now()
        self.db.commit()
        audit(self.db, action="learning.grade_short", target_type="assessment", actor_id=actor.id, target_id=str(a.id), detail=f"answer={answer_id},score={score}")
        return {"message": "已赋分", "submission_total": submission.total_score, "submission_status": submission.status.value}

    def finalize(self, *, assessment_id: int, actor: User) -> dict:
        a = self._get_assessment(assessment_id)
        self._ensure_author(a, actor)
        if a.status == AssessmentStatus.PUBLISHED:
            raise HTTPException(status_code=400, detail="该考核已公示")
        if a.status == AssessmentStatus.DRAFTING:
            raise HTTPException(status_code=400, detail="考核尚未发布答题")
        # 所有已提交卷子的简答必须批改完毕
        ungraded = (
            self.db.query(AssessmentAnswer)
            .join(AssessmentSubmission, AssessmentAnswer.submission_id == AssessmentSubmission.id)
            .filter(
                AssessmentSubmission.assessment_id == assessment_id,
                AssessmentAnswer.auto_graded.is_(False),
                AssessmentAnswer.grader_comment.is_(None),
                AssessmentAnswer.score == 0,
            )
            .count()
        )
        if ungraded > 0:
            raise HTTPException(status_code=400, detail=f"还有 {ungraded} 道简答未批改（若确为 0 分请填写批注后再公示）")

        a.status = AssessmentStatus.PUBLISHED
        a.published_at = local_now()
        self.db.commit()
        topic = self.db.query(LearningTopic).filter(LearningTopic.id == a.topic_id).first()
        sse_publish(
            "learning_assessment_published_results",
            {"assessment_id": a.id, "topic_id": a.topic_id, "topic_title": topic.title if topic else ""},
            channels=["global"],
        )
        audit(self.db, action="learning.finalize", target_type="assessment", actor_id=actor.id, target_id=str(a.id), detail="published")
        notice = (
            "### 🏆 考核成绩已公示\n"
            f"> 主题：**{topic.title if topic else ''}**（第 {a.round_no} 轮）\n"
            "> 请到「学习中心」查看排行榜与逐题解析。"
        )
        return {"message": "成绩已生成并公示", "notice": notice}

    def results(self, assessment_id: int, actor: User) -> dict:
        a = self._get_assessment(assessment_id)
        is_author = a.author_id == actor.id or actor.role.value == "admin"
        if a.status != AssessmentStatus.PUBLISHED and not is_author:
            raise HTTPException(status_code=403, detail="成绩尚未公示")

        qs = self.db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment_id).order_by(AssessmentQuestion.sort_order).all()
        q_by_id = {q.id: q for q in qs}
        submissions = (
            self.db.query(AssessmentSubmission)
            .options(joinedload(AssessmentSubmission.user), joinedload(AssessmentSubmission.answers))
            .filter(AssessmentSubmission.assessment_id == assessment_id)
            .all()
        )
        submitted_user_ids = {s.user_id for s in submissions}

        people = []
        for s in submissions:
            answer_by_q = {ans.question_id: ans for ans in s.answers}
            people.append(
                {
                    "user_id": s.user_id,
                    "user_name": s.user.shown_name if s.user else "未知",
                    "status": s.status.value,
                    "objective_score": s.objective_score,
                    "subjective_score": s.subjective_score,
                    "total_score": s.total_score,
                    "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
                    "answers": [
                        {
                            "question_id": q.id,
                            "response": (json.loads(answer_by_q[q.id].response_json) if (q.id in answer_by_q and answer_by_q[q.id].response_json) else None),
                            "score": answer_by_q[q.id].score if q.id in answer_by_q else 0,
                            "auto_graded": answer_by_q[q.id].auto_graded if q.id in answer_by_q else True,
                            "grader_comment": answer_by_q[q.id].grader_comment if q.id in answer_by_q else None,
                        }
                        for q in qs
                    ],
                }
            )
        people.sort(key=lambda p: p["total_score"], reverse=True)

        # 未参与（缺考）的团队成员（排除出题人）
        absent = []
        team = self.db.query(User).filter(User.is_team_member.is_(True)).all()
        for u in team:
            if u.id == a.author_id or u.id in submitted_user_ids:
                continue
            absent.append({"user_id": u.id, "user_name": u.shown_name})

        return {
            "id": a.id,
            "topic_id": a.topic_id,
            "round_no": a.round_no,
            "status": a.status.value,
            "author_name": (self.db.query(User).filter(User.id == a.author_id).first().shown_name if a.author_id else None),
            "total_score": a.total_score,
            "questions": [self._question_view(q, include_answer=True) for q in qs],
            "people": people,
            "absent": absent,
        }
