import pytest

from app.models import AssessmentQuestion, AssessmentStatus, User, UserRole
from app.services.learning_service import LearningService


def _user(db, username, role=UserRole.USER, team=True):
    u = User(username=username, password_hash=User.hash_password("pass123"), role=role, is_team_member=team)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


def _questions(db, assessment_id):
    return db.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == assessment_id).order_by(AssessmentQuestion.sort_order).all()


def _standard_questions():
    return [
        {"type": "single", "prompt": "1+1=?", "options": [{"key": "A", "text": "1"}, {"key": "B", "text": "2"}], "answer": "B", "score": 10},
        {"type": "multi", "prompt": "选偶数", "options": [{"key": "A", "text": "2"}, {"key": "B", "text": "3"}, {"key": "C", "text": "4"}], "answer": ["A", "C"], "score": 10},
        {"type": "judge", "prompt": "地球是圆的", "answer": "true", "score": 10},
        {"type": "blank", "prompt": "法国首都", "answer": ["Paris", "巴黎"], "score": 10},
        {"type": "short", "prompt": "简述测试", "answer": "参考答案", "score": 10},
    ]


def _setup_published(db, author, questions=None):
    svc = LearningService(db)
    topic = svc.create_topic(title="主题A", description=None, actor=author)
    a = svc.create_assessment(topic_id=topic["id"], actor=author)
    svc.upsert_questions(assessment_id=a["id"], questions=questions or _standard_questions(), actor=author)
    svc.publish_assessment(assessment_id=a["id"], actor=author)
    return topic["id"], a["id"]


def test_autograde_objective_correct(db_session):
    author = _user(db_session, "ln_author1")
    taker = _user(db_session, "ln_taker1")
    _, aid = _setup_published(db_session, author)
    qs = _questions(db_session, aid)
    answers = {
        str(qs[0].id): "B",          # single 对
        str(qs[1].id): ["A", "C"],   # multi 全对
        str(qs[2].id): "true",       # judge 对
        str(qs[3].id): " 巴黎 ",      # blank 宽松匹配（空格）
        str(qs[4].id): "我的回答",     # short 待人工
    }
    svc = LearningService(db_session)
    res = svc.submit_answers(assessment_id=aid, answers=answers, actor=taker)
    assert res["objective_score"] == 40  # 4 道客观题各 10 分
    assert res["pending_manual_grading"] is True


def test_multi_all_or_nothing(db_session):
    author = _user(db_session, "ln_author2")
    taker = _user(db_session, "ln_taker2")
    _, aid = _setup_published(db_session, author)
    qs = _questions(db_session, aid)
    answers = {str(qs[1].id): ["A"]}  # multi 漏选 → 0 分
    svc = LearningService(db_session)
    res = svc.submit_answers(assessment_id=aid, answers=answers, actor=taker)
    assert res["objective_score"] == 0


def test_blank_case_insensitive_and_alternatives(db_session):
    author = _user(db_session, "ln_author3")
    t1 = _user(db_session, "ln_t3a")
    t2 = _user(db_session, "ln_t3b")
    _, aid = _setup_published(db_session, author)
    qs = _questions(db_session, aid)
    svc = LearningService(db_session)
    r1 = svc.submit_answers(assessment_id=aid, answers={str(qs[3].id): "paris"}, actor=t1)
    r2 = svc.submit_answers(assessment_id=aid, answers={str(qs[3].id): "巴黎"}, actor=t2)
    assert r1["objective_score"] == 10  # 大小写不敏感命中 Paris
    assert r2["objective_score"] == 10  # 另一可接受答案命中


def test_author_cannot_answer_own(db_session):
    author = _user(db_session, "ln_author4")
    _, aid = _setup_published(db_session, author)
    svc = LearningService(db_session)
    with pytest.raises(Exception) as e:
        svc.get_paper(aid, author)
    assert getattr(e.value, "status_code", None) == 403
    with pytest.raises(Exception) as e2:
        svc.submit_answers(assessment_id=aid, answers={}, actor=author)
    assert getattr(e2.value, "status_code", None) == 403


def test_no_duplicate_submission(db_session):
    author = _user(db_session, "ln_author5")
    taker = _user(db_session, "ln_taker5")
    _, aid = _setup_published(db_session, author)
    svc = LearningService(db_session)
    svc.submit_answers(assessment_id=aid, answers={}, actor=taker)
    with pytest.raises(Exception) as e:
        svc.submit_answers(assessment_id=aid, answers={}, actor=taker)
    assert getattr(e.value, "status_code", None) == 400


def test_non_author_cannot_grade(db_session):
    author = _user(db_session, "ln_author6")
    taker = _user(db_session, "ln_taker6")
    other = _user(db_session, "ln_other6")
    _, aid = _setup_published(db_session, author)
    qs = _questions(db_session, aid)
    svc = LearningService(db_session)
    svc.submit_answers(assessment_id=aid, answers={str(qs[4].id): "答"}, actor=taker)
    queue = svc.grading_queue(aid, author)
    answer_id = queue["items"][0]["answer_id"]
    with pytest.raises(Exception) as e:
        svc.grade_short_answer(answer_id=answer_id, score=5, comment=None, actor=other)
    assert getattr(e.value, "status_code", None) == 403


def test_finalize_blocked_until_short_graded_then_results(db_session):
    author = _user(db_session, "ln_author7")
    taker = _user(db_session, "ln_taker7")
    absent = _user(db_session, "ln_absent7")  # 缺考者
    _, aid = _setup_published(db_session, author)
    qs = _questions(db_session, aid)
    svc = LearningService(db_session)
    svc.submit_answers(
        assessment_id=aid,
        answers={str(qs[0].id): "B", str(qs[1].id): ["A", "C"], str(qs[2].id): "true", str(qs[3].id): "巴黎", str(qs[4].id): "回答"},
        actor=taker,
    )
    # 简答未批改 → finalize 受阻
    with pytest.raises(Exception) as e:
        svc.finalize(assessment_id=aid, actor=author)
    assert getattr(e.value, "status_code", None) == 400

    queue = svc.grading_queue(aid, author)
    answer_id = queue["items"][0]["answer_id"]
    svc.grade_short_answer(answer_id=answer_id, score=8, comment="不错", actor=author)
    svc.finalize(assessment_id=aid, actor=author)

    results = svc.results(aid, taker)
    assert results["status"] == AssessmentStatus.PUBLISHED.value
    assert len(results["people"]) == 1
    assert results["people"][0]["total_score"] == 48  # 40 客观 + 8 主观
    assert any(p["user_name"] == absent.shown_name for p in results["absent"])


def test_delete_topic_cascades_rows_and_files(db_session, tmp_path):
    import app.services.learning_service as ls
    from app.models import Assessment, AssessmentAnswer, AssessmentQuestion, AssessmentSubmission, LearningMaterial, LearningTopic

    author = _user(db_session, "ln_del_author")
    taker = _user(db_session, "ln_del_taker")
    topic_id, aid = _setup_published(db_session, author)
    qs = _questions(db_session, aid)
    LearningService(db_session).submit_answers(
        assessment_id=aid, answers={str(qs[0].id): "B", str(qs[4].id): "答"}, actor=taker
    )

    # 造一个真实的物理文件 + 资料行，验证删除主题会清理文件
    folder = ls.UPLOAD_ROOT / "_test"
    folder.mkdir(parents=True, exist_ok=True)
    fpath = folder / "ln_del_file.txt"
    fpath.write_text("hello", encoding="utf-8")
    rel = str(fpath.relative_to(ls.PROJECT_ROOT)).replace("\\", "/")
    mat = LearningMaterial(topic_id=topic_id, original_name="a.txt", stored_name="ln_del_file.txt", file_path=rel, file_size=5, uploaded_by_id=author.id)
    db_session.add(mat)
    db_session.commit()
    assert fpath.exists()

    LearningService(db_session).delete_topic(topic_id, author)

    assert fpath.exists() is False  # 物理文件被清理
    assert db_session.query(LearningTopic).filter(LearningTopic.id == topic_id).first() is None
    assert db_session.query(LearningMaterial).filter(LearningMaterial.topic_id == topic_id).count() == 0
    assert db_session.query(Assessment).filter(Assessment.id == aid).count() == 0
    assert db_session.query(AssessmentQuestion).filter(AssessmentQuestion.assessment_id == aid).count() == 0
    assert db_session.query(AssessmentSubmission).filter(AssessmentSubmission.assessment_id == aid).count() == 0
    assert db_session.query(AssessmentAnswer).count() == 0


def test_delete_topic_forbidden_for_non_creator(db_session):
    author = _user(db_session, "ln_del_author2")
    other = _user(db_session, "ln_del_other2")
    svc = LearningService(db_session)
    topic = svc.create_topic(title="T", description=None, actor=author)
    with pytest.raises(Exception) as e:
        svc.delete_topic(topic["id"], other)
    assert getattr(e.value, "status_code", None) == 403


def test_results_hidden_before_publish_for_non_author(db_session):
    author = _user(db_session, "ln_author8")
    taker = _user(db_session, "ln_taker8")
    _, aid = _setup_published(db_session, author)
    svc = LearningService(db_session)
    with pytest.raises(Exception) as e:
        svc.results(aid, taker)
    assert getattr(e.value, "status_code", None) == 403
