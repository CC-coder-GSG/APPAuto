import json
import re
import unicodedata
from datetime import datetime
from typing import Annotated, Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.api.deps import get_db
from app.models import (
    FillGradingMode,
    LearningAnswer,
    LearningContent,
    LearningContentKind,
    LearningQuestion,
    LearningQuiz,
    LearningSubmission,
    LearningQuestionType as QuestionType,
    LearningQuizStatus as QuizStatus,
    User,
)


class ContentPayload(BaseModel):
    kind: LearningContentKind
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    body: Optional[str] = None
    resource_url: Optional[str] = Field(default=None, max_length=1000)
    published: bool = False

    @model_validator(mode="after")
    def validate_resource_url(self):
        if self.resource_url and not re.match(r"^https?://", self.resource_url, re.IGNORECASE):
            raise ValueError("外部资源链接必须以 http:// 或 https:// 开头")
        return self


class QuestionPayload(BaseModel):
    question_type: QuestionType
    prompt: str = Field(min_length=1)
    score: float = Field(default=1, gt=0, le=10000)
    options: list[str] = Field(default_factory=list)
    correct_answers: list[str] = Field(default_factory=list)
    fill_grading_mode: Optional[FillGradingMode] = None
    match_count: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_question(self):
        if self.question_type in (QuestionType.SINGLE_CHOICE, QuestionType.MULTIPLE_CHOICE):
            clean_options = [x.strip() for x in self.options if x.strip()]
            self.correct_answers = list(dict.fromkeys(x.strip() for x in self.correct_answers if x.strip()))
            if len(clean_options) < 2 or len(set(clean_options)) != len(clean_options):
                raise ValueError("选择题至少需要两个不重复的选项")
            if not self.correct_answers or any(x not in clean_options for x in self.correct_answers):
                raise ValueError("选择题正确答案必须来自选项")
            if self.question_type == QuestionType.SINGLE_CHOICE and len(self.correct_answers) != 1:
                raise ValueError("单选题只能有一个正确答案")
            self.options = clean_options
        elif self.question_type == QuestionType.FILL_BLANK:
            self.correct_answers = list(dict.fromkeys(x.strip() for x in self.correct_answers if x.strip()))
            if not self.correct_answers:
                raise ValueError("填空题至少需要一个参考答案")
            self.fill_grading_mode = self.fill_grading_mode or FillGradingMode.EXACT
            if self.match_count > len(self.correct_answers):
                raise ValueError("要求匹配数量不能超过参考答案数量")
        else:
            self.options = []
            self.correct_answers = []
            self.fill_grading_mode = None
        return self


class QuizPayload(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None
    questions: list[QuestionPayload] = Field(min_length=1)


class AnswerPayload(BaseModel):
    question_id: int
    answer: Any = None


class SubmissionPayload(BaseModel):
    answers: list[AnswerPayload]
    is_preview: bool = False


class GradeItem(BaseModel):
    question_id: int
    awarded_score: float = Field(ge=0)
    reviewer_comment: Optional[str] = None


class GradePayload(BaseModel):
    grades: list[GradeItem]


class ReleasePayload(BaseModel):
    show_answers: bool = False


def _loads(raw: Optional[str], fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in {"P", "S", "Z", "C"})


def _fill_fragments(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    return [x for x in re.split(r"[\n,;\uFF0C\uFF1B]+", str(value or "")) if x != ""]


def grade_objective(question: LearningQuestion, submitted: Any) -> Optional[bool]:
    expected = _loads(question.correct_answers_json, [])
    if question.question_type == QuestionType.SINGLE_CHOICE:
        return str(submitted) == str(expected[0]) if expected else False
    if question.question_type == QuestionType.MULTIPLE_CHOICE:
        actual = submitted if isinstance(submitted, list) else []
        return {str(x) for x in actual} == {str(x) for x in expected}
    if question.question_type == QuestionType.SHORT_ANSWER:
        return None

    mode = question.fill_grading_mode or FillGradingMode.EXACT
    fragments = [str(x) for x in submitted] if isinstance(submitted, list) else [str(submitted or "")]
    if mode == FillGradingMode.EXACT:
        return any(fragment == str(answer) for fragment in fragments for answer in expected)
    if mode == FillGradingMode.CONTAINS:
        return any(str(answer).strip().casefold() in fragment.strip().casefold() for fragment in fragments for answer in expected if str(answer) != "")
    normalized_fragments = {_normalized(x) for x in fragments}
    normalized_answers = {_normalized(x) for x in expected}
    normalized_fragments.discard("")
    normalized_answers.discard("")
    if mode == FillGradingMode.NORMALIZED:
        return bool(normalized_fragments & normalized_answers)
    match_fragments = {_normalized(x) for x in _fill_fragments(submitted)}
    match_fragments.discard("")
    if mode == FillGradingMode.MATCH_ANY:
        return bool(match_fragments & normalized_answers)
    matched = len(match_fragments & normalized_answers)
    return matched >= question.match_count


def _question_dict(question: LearningQuestion, reveal_answer: bool) -> dict:
    data = {
        "id": question.id,
        "position": question.position,
        "question_type": question.question_type.value,
        "prompt": question.prompt,
        "score": question.score,
        "options": _loads(question.options_json, []),
        "fill_grading_mode": question.fill_grading_mode.value if question.fill_grading_mode else None,
        "match_count": question.match_count,
    }
    if reveal_answer:
        data["correct_answers"] = _loads(question.correct_answers_json, [])
    return data


def _quiz_base(quiz: LearningQuiz, current_user: User, my_submission: Optional[LearningSubmission] = None) -> dict:
    return {
        "id": quiz.id,
        "title": quiz.title,
        "description": quiz.description,
        "status": quiz.status.value,
        "creator_id": quiz.creator_id,
        "creator_name": quiz.creator.username if quiz.creator else "",
        "is_creator": quiz.creator_id == current_user.id,
        "question_count": len(quiz.questions),
        "total_points": sum(q.score for q in quiz.questions),
        "results_released": quiz.results_released,
        "show_answers": quiz.show_answers if quiz.results_released else False,
        "started_at": quiz.started_at,
        "closed_at": quiz.closed_at,
        "my_submission": None if not my_submission else {
            "id": my_submission.id,
            "status": my_submission.status,
            "submitted_at": my_submission.submitted_at,
            "total_score": my_submission.total_score if quiz.results_released else None,
        },
    }


def _content_dict(item: LearningContent) -> dict:
    return {
        "id": item.id,
        "kind": item.kind.value,
        "title": item.title,
        "description": item.description,
        "body": item.body,
        "resource_url": item.resource_url,
        "published": item.published,
        "creator_id": item.creator_id,
        "creator_name": item.creator.username if item.creator else "",
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def create_learning_router(current_user_dependency: Callable) -> APIRouter:
    router = APIRouter(prefix="/learning", tags=["learning"])
    CurrentUser = Annotated[User, Depends(current_user_dependency)]
    Db = Annotated[Session, Depends(get_db)]

    def get_quiz(db: Session, quiz_id: int) -> LearningQuiz:
        quiz = (
            db.query(LearningQuiz)
            .options(selectinload(LearningQuiz.creator), selectinload(LearningQuiz.questions))
            .filter(LearningQuiz.id == quiz_id)
            .first()
        )
        if not quiz:
            raise HTTPException(status_code=404, detail="答题活动不存在")
        return quiz

    def require_creator(quiz: LearningQuiz, user: User) -> None:
        if quiz.creator_id != user.id:
            raise HTTPException(status_code=403, detail="仅出题人可执行此操作")

    @router.get("/contents")
    def list_contents(current_user: CurrentUser, db: Db):
        items = (
            db.query(LearningContent)
            .options(selectinload(LearningContent.creator))
            .filter((LearningContent.published.is_(True)) | (LearningContent.creator_id == current_user.id))
            .order_by(LearningContent.created_at.desc())
            .all()
        )
        return [_content_dict(item) for item in items]

    @router.post("/contents", status_code=201)
    def create_content(payload: ContentPayload, current_user: CurrentUser, db: Db):
        item = LearningContent(creator_id=current_user.id, **payload.model_dump())
        db.add(item)
        db.commit()
        db.refresh(item)
        item.creator = current_user
        return _content_dict(item)

    @router.put("/contents/{content_id}")
    def update_content(content_id: int, payload: ContentPayload, current_user: CurrentUser, db: Db):
        item = db.query(LearningContent).filter(LearningContent.id == content_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="学习内容不存在")
        if item.creator_id != current_user.id:
            raise HTTPException(status_code=403, detail="仅创建人可修改")
        for key, value in payload.model_dump().items():
            setattr(item, key, value)
        db.commit()
        db.refresh(item)
        item.creator = current_user
        return _content_dict(item)

    @router.delete("/contents/{content_id}")
    def delete_content(content_id: int, current_user: CurrentUser, db: Db):
        item = db.query(LearningContent).filter(LearningContent.id == content_id).first()
        if not item:
            raise HTTPException(status_code=404, detail="学习内容不存在")
        if item.creator_id != current_user.id:
            raise HTTPException(status_code=403, detail="仅创建人可删除")
        db.delete(item)
        db.commit()
        return {"message": "已删除"}

    def apply_quiz_payload(quiz: LearningQuiz, payload: QuizPayload) -> None:
        quiz.title = payload.title
        quiz.description = payload.description
        quiz.questions.clear()
        for index, question in enumerate(payload.questions, start=1):
            quiz.questions.append(
                LearningQuestion(
                    position=index,
                    question_type=question.question_type,
                    prompt=question.prompt,
                    score=question.score,
                    options_json=_dump(question.options),
                    correct_answers_json=_dump(question.correct_answers),
                    fill_grading_mode=question.fill_grading_mode,
                    match_count=question.match_count,
                )
            )

    @router.post("/quizzes", status_code=201)
    def create_quiz(payload: QuizPayload, current_user: CurrentUser, db: Db):
        quiz = LearningQuiz(creator_id=current_user.id, title=payload.title, description=payload.description)
        apply_quiz_payload(quiz, payload)
        db.add(quiz)
        db.commit()
        db.refresh(quiz)
        quiz.creator = current_user
        return {**_quiz_base(quiz, current_user), "questions": [_question_dict(q, True) for q in quiz.questions]}

    @router.put("/quizzes/{quiz_id}")
    def update_quiz(quiz_id: int, payload: QuizPayload, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        require_creator(quiz, current_user)
        if quiz.status != QuizStatus.DRAFT:
            raise HTTPException(status_code=409, detail="只有草稿状态可以修改题目")
        for preview_submission in db.query(LearningSubmission).filter(
            LearningSubmission.quiz_id == quiz.id,
            LearningSubmission.is_preview.is_(True),
        ).all():
            db.delete(preview_submission)
        db.flush()
        apply_quiz_payload(quiz, payload)
        db.commit()
        db.refresh(quiz)
        return {**_quiz_base(quiz, current_user), "questions": [_question_dict(q, True) for q in quiz.questions]}

    @router.delete("/quizzes/{quiz_id}")
    def delete_quiz(quiz_id: int, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        require_creator(quiz, current_user)
        if quiz.status != QuizStatus.DRAFT:
            raise HTTPException(status_code=409, detail="只能删除尚未开始的草稿")
        db.delete(quiz)
        db.commit()
        return {"message": "已删除"}

    @router.get("/quizzes")
    def list_quizzes(current_user: CurrentUser, db: Db):
        quizzes = (
            db.query(LearningQuiz)
            .options(selectinload(LearningQuiz.creator), selectinload(LearningQuiz.questions))
            .filter((LearningQuiz.status != QuizStatus.DRAFT) | (LearningQuiz.creator_id == current_user.id))
            .order_by(LearningQuiz.created_at.desc())
            .all()
        )
        quiz_ids = [q.id for q in quizzes]
        submissions = (
            db.query(LearningSubmission)
            .filter(
                LearningSubmission.quiz_id.in_(quiz_ids),
                LearningSubmission.user_id == current_user.id,
                LearningSubmission.is_preview.is_(False),
            )
            .all()
            if quiz_ids else []
        )
        submission_by_quiz = {s.quiz_id: s for s in submissions}
        return [_quiz_base(quiz, current_user, submission_by_quiz.get(quiz.id)) for quiz in quizzes]

    @router.get("/quizzes/{quiz_id}")
    def quiz_detail(
        quiz_id: int,
        current_user: CurrentUser,
        db: Db,
        preview: bool = Query(default=False),
    ):
        quiz = get_quiz(db, quiz_id)
        is_creator = quiz.creator_id == current_user.id
        if quiz.status == QuizStatus.DRAFT and not is_creator:
            raise HTTPException(status_code=404, detail="答题活动不存在")
        if preview and not is_creator:
            raise HTTPException(status_code=403, detail="仅出题人可以预览")
        submission = (
            db.query(LearningSubmission)
            .filter(
                LearningSubmission.quiz_id == quiz.id,
                LearningSubmission.user_id == current_user.id,
                LearningSubmission.is_preview.is_(preview),
            )
            .first()
        )
        can_see_questions = is_creator or quiz.status == QuizStatus.OPEN or submission is not None
        data = _quiz_base(quiz, current_user, submission if not preview else None)
        data["questions"] = [_question_dict(q, is_creator) for q in quiz.questions] if can_see_questions else []
        data["preview_submission_id"] = submission.id if preview and submission else None
        return data

    @router.post("/quizzes/{quiz_id}/start")
    def start_quiz(quiz_id: int, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        require_creator(quiz, current_user)
        if quiz.status != QuizStatus.DRAFT:
            raise HTTPException(status_code=409, detail="只有草稿可以开始")
        if not quiz.questions:
            raise HTTPException(status_code=400, detail="请先添加题目")
        quiz.status = QuizStatus.OPEN
        quiz.started_at = datetime.utcnow()
        db.commit()
        return {"message": "答题已开始", "status": quiz.status.value}

    @router.post("/quizzes/{quiz_id}/close")
    def close_quiz(quiz_id: int, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        require_creator(quiz, current_user)
        if quiz.status != QuizStatus.OPEN:
            raise HTTPException(status_code=409, detail="活动当前不在答题中")
        quiz.status = QuizStatus.CLOSED
        quiz.closed_at = datetime.utcnow()
        db.commit()
        return {"message": "答题已结束", "status": quiz.status.value}

    @router.post("/quizzes/{quiz_id}/submissions", status_code=201)
    def submit_quiz(quiz_id: int, payload: SubmissionPayload, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        if payload.is_preview:
            require_creator(quiz, current_user)
        else:
            if quiz.status != QuizStatus.OPEN:
                raise HTTPException(status_code=409, detail="当前不允许答题")
            if quiz.creator_id == current_user.id:
                raise HTTPException(status_code=409, detail="出题人请使用预览答题")

        provided = {item.question_id: item.answer for item in payload.answers}
        valid_ids = {q.id for q in quiz.questions}
        if len(provided) != len(payload.answers) or not set(provided).issubset(valid_ids):
            raise HTTPException(status_code=400, detail="提交中含有重复或无效题目")

        old = db.query(LearningSubmission).filter_by(
            quiz_id=quiz.id, user_id=current_user.id, is_preview=payload.is_preview
        ).first()
        if old:
            raise HTTPException(status_code=409, detail="你已经提交过，本活动只允许提交一次")

        submission = LearningSubmission(quiz_id=quiz.id, user_id=current_user.id, is_preview=payload.is_preview)
        auto_score = 0.0
        needs_review = False
        for question in quiz.questions:
            value = provided.get(question.id)
            correct = grade_objective(question, value)
            awarded = None if correct is None else (question.score if correct else 0.0)
            if correct is None:
                needs_review = True
            else:
                auto_score += awarded or 0
            submission.answers.append(
                LearningAnswer(
                    question_id=question.id,
                    answer_json=_dump(value),
                    is_correct=correct,
                    awarded_score=awarded,
                )
            )
        submission.auto_score = auto_score
        submission.status = "submitted" if needs_review else "graded"
        submission.total_score = None if needs_review else auto_score
        submission.graded_at = None if needs_review else datetime.utcnow()
        db.add(submission)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="你已经提交过，本活动只允许提交一次") from exc
        db.refresh(submission)
        return {"id": submission.id, "status": submission.status, "submitted_at": submission.submitted_at}

    @router.delete("/quizzes/{quiz_id}/preview")
    def reset_preview(quiz_id: int, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        require_creator(quiz, current_user)
        preview = db.query(LearningSubmission).filter_by(
            quiz_id=quiz.id, user_id=current_user.id, is_preview=True
        ).first()
        if preview:
            db.delete(preview)
            db.commit()
        return {"message": "预览记录已重置"}

    @router.get("/quizzes/{quiz_id}/submissions")
    def list_submissions(quiz_id: int, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        require_creator(quiz, current_user)
        rows = (
            db.query(LearningSubmission)
            .options(selectinload(LearningSubmission.user), selectinload(LearningSubmission.answers).selectinload(LearningAnswer.question))
            .filter(LearningSubmission.quiz_id == quiz.id)
            .order_by(LearningSubmission.is_preview.desc(), LearningSubmission.submitted_at.asc())
            .all()
        )
        return [
            {
                "id": row.id,
                "user_id": row.user_id,
                "username": row.user.username,
                "is_preview": row.is_preview,
                "status": row.status,
                "auto_score": row.auto_score,
                "total_score": row.total_score,
                "submitted_at": row.submitted_at,
                "answers": [
                    {
                        "question_id": answer.question_id,
                        "prompt": answer.question.prompt,
                        "question_type": answer.question.question_type.value,
                        "max_score": answer.question.score,
                        "answer": _loads(answer.answer_json, None),
                        "is_correct": answer.is_correct,
                        "awarded_score": answer.awarded_score,
                        "reviewer_comment": answer.reviewer_comment,
                        "correct_answers": _loads(answer.question.correct_answers_json, []),
                    }
                    for answer in row.answers
                ],
            }
            for row in rows
        ]

    @router.put("/quizzes/{quiz_id}/submissions/{submission_id}/grade")
    def grade_submission(quiz_id: int, submission_id: int, payload: GradePayload, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        require_creator(quiz, current_user)
        submission = (
            db.query(LearningSubmission)
            .options(selectinload(LearningSubmission.answers).selectinload(LearningAnswer.question))
            .filter(LearningSubmission.id == submission_id, LearningSubmission.quiz_id == quiz.id)
            .first()
        )
        if not submission:
            raise HTTPException(status_code=404, detail="提交记录不存在")
        grade_by_question = {item.question_id: item for item in payload.grades}
        for answer in submission.answers:
            item = grade_by_question.get(answer.question_id)
            if not item:
                continue
            if item.awarded_score > answer.question.score:
                raise HTTPException(status_code=400, detail=f"题目 {answer.question_id} 得分不能超过满分")
            answer.awarded_score = item.awarded_score
            answer.is_correct = item.awarded_score >= answer.question.score
            answer.reviewer_comment = item.reviewer_comment
        if any(a.awarded_score is None for a in submission.answers):
            raise HTTPException(status_code=400, detail="仍有题目尚未评分")
        submission.total_score = sum(a.awarded_score or 0 for a in submission.answers)
        submission.status = "graded"
        submission.graded_at = datetime.utcnow()
        db.commit()
        return {"message": "批阅完成", "total_score": submission.total_score}

    @router.post("/quizzes/{quiz_id}/release")
    def release_results(quiz_id: int, payload: ReleasePayload, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        require_creator(quiz, current_user)
        if quiz.status != QuizStatus.CLOSED:
            raise HTTPException(status_code=409, detail="请先结束答题")
        pending = db.query(LearningSubmission).filter(
            LearningSubmission.quiz_id == quiz.id,
            LearningSubmission.is_preview.is_(False),
            LearningSubmission.status != "graded",
        ).count()
        if pending:
            raise HTTPException(status_code=409, detail=f"还有 {pending} 份答卷未完成批阅")
        quiz.results_released = True
        quiz.show_answers = payload.show_answers
        db.commit()
        return {"message": "成绩已发布", "show_answers": quiz.show_answers}

    @router.get("/quizzes/{quiz_id}/my-result")
    def my_result(quiz_id: int, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        submission = (
            db.query(LearningSubmission)
            .options(selectinload(LearningSubmission.answers).selectinload(LearningAnswer.question))
            .filter_by(quiz_id=quiz.id, user_id=current_user.id, is_preview=False)
            .first()
        )
        if not submission:
            raise HTTPException(status_code=404, detail="尚未提交答卷")
        if not quiz.results_released:
            return {"released": False, "status": submission.status, "submitted_at": submission.submitted_at}
        return {
            "released": True,
            "status": submission.status,
            "score": submission.total_score,
            "total_points": sum(q.score for q in quiz.questions),
            "show_answers": quiz.show_answers,
            "answers": [
                {
                    "question_id": a.question_id,
                    "prompt": a.question.prompt,
                    "answer": _loads(a.answer_json, None),
                    "awarded_score": a.awarded_score,
                    "max_score": a.question.score,
                    "is_correct": a.is_correct,
                    "reviewer_comment": a.reviewer_comment,
                    "correct_answers": _loads(a.question.correct_answers_json, []) if quiz.show_answers else None,
                }
                for a in submission.answers
            ],
        }

    @router.get("/quizzes/{quiz_id}/stats")
    def quiz_stats(quiz_id: int, current_user: CurrentUser, db: Db):
        quiz = get_quiz(db, quiz_id)
        require_creator(quiz, current_user)
        submissions = (
            db.query(LearningSubmission)
            .options(selectinload(LearningSubmission.user), selectinload(LearningSubmission.answers))
            .filter(LearningSubmission.quiz_id == quiz.id, LearningSubmission.is_preview.is_(False))
            .all()
        )
        expected = db.query(User).filter(User.is_team_member.is_(True), User.id != quiz.creator_id).count()
        answer_map = {q.id: [] for q in quiz.questions}
        for submission in submissions:
            for answer in submission.answers:
                answer_map[answer.question_id].append((submission, answer))
        questions = []
        for question in quiz.questions:
            rows = answer_map[question.id]
            judged = [answer for _, answer in rows if answer.is_correct is not None]
            correct = len([answer for answer in judged if answer.is_correct])
            questions.append(
                {
                    **_question_dict(question, True),
                    "answered_count": len(rows),
                    "judged_count": len(judged),
                    "correct_count": correct,
                    "correct_rate": round(correct * 100 / len(judged), 2) if judged else None,
                    "answers": [
                        {
                            "username": submission.user.username,
                            "answer": _loads(answer.answer_json, None),
                            "is_correct": answer.is_correct,
                            "awarded_score": answer.awarded_score,
                        }
                        for submission, answer in rows
                    ],
                }
            )
        return {
            "quiz_id": quiz.id,
            "expected_count": expected,
            "submitted_count": len(submissions),
            "pending_count": max(expected - len(submissions), 0),
            "graded_count": len([s for s in submissions if s.status == "graded"]),
            "questions": questions,
        }

    return router
