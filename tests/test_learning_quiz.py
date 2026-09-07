from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app
from app.models import User, UserRole


@pytest.fixture()
def learning_client(db_session):
    db_session.add_all(
        [
            User(username="quiz_author", password_hash=User.hash_password("pass"), role=UserRole.USER),
            User(username="quiz_student", password_hash=User.hash_password("pass"), role=UserRole.USER),
        ]
    )
    db_session.commit()

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


def _login(client: TestClient, username: str, *, mobile: bool = False) -> dict[str, str]:
    headers = {"X-Client-Type": "mobile"} if mobile else None
    response = client.post(
        "/auth/token",
        data={"username": username, "password": "pass"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_complete_quiz_workflow(learning_client):
    author_headers = _login(learning_client, "quiz_author")
    student_headers = _login(learning_client, "quiz_student")
    payload = {
        "title": "发布前学习检查",
        "description": "正式答卷只允许提交一次",
        "questions": [
            {
                "question_type": "single_choice",
                "prompt": "请选择 A",
                "score": 2,
                "options": ["A", "B"],
                "correct_answers": ["A"],
            },
            {
                "question_type": "fill_blank",
                "prompt": "填写关键字",
                "score": 3,
                "correct_answers": ["Omni QA"],
                "fill_grading_mode": "normalized",
            },
            {
                "question_type": "short_answer",
                "prompt": "说明风险",
                "score": 5,
            },
        ],
    }

    created = learning_client.post("/learning/quizzes", headers=author_headers, json=payload)
    assert created.status_code == 201, created.text
    quiz_id = created.json()["id"]

    student_drafts = learning_client.get("/learning/quizzes", headers=student_headers).json()
    assert not any(quiz["id"] == quiz_id for quiz in student_drafts)

    started = learning_client.post(f"/learning/quizzes/{quiz_id}/start", headers=author_headers)
    assert started.status_code == 200, started.text
    detail = learning_client.get(f"/learning/quizzes/{quiz_id}", headers=student_headers).json()
    assert "correct_answers" not in detail["questions"][0]

    answers = {
        "answers": [
            {"question_id": detail["questions"][0]["id"], "answer": "A"},
            {"question_id": detail["questions"][1]["id"], "answer": "Omni， QA!"},
            {"question_id": detail["questions"][2]["id"], "answer": "需要回归验证"},
        ],
        "is_preview": False,
    }
    submitted = learning_client.post(
        f"/learning/quizzes/{quiz_id}/submissions", headers=student_headers, json=answers
    )
    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["status"] == "submitted"
    duplicate = learning_client.post(
        f"/learning/quizzes/{quiz_id}/submissions", headers=student_headers, json=answers
    )
    assert duplicate.status_code == 409

    learning_client.post(f"/learning/quizzes/{quiz_id}/close", headers=author_headers)
    rows = learning_client.get(
        f"/learning/quizzes/{quiz_id}/submissions", headers=author_headers
    ).json()
    submission = next(row for row in rows if not row["is_preview"])
    grades = {
        "grades": [
            {
                "question_id": answer["question_id"],
                "awarded_score": 4
                if answer["question_type"] == "short_answer"
                else answer["awarded_score"],
            }
            for answer in submission["answers"]
        ]
    }
    graded = learning_client.put(
        f"/learning/quizzes/{quiz_id}/submissions/{submission['id']}/grade",
        headers=author_headers,
        json=grades,
    )
    assert graded.status_code == 200, graded.text
    assert graded.json()["total_score"] == 9

    released = learning_client.post(
        f"/learning/quizzes/{quiz_id}/release",
        headers=author_headers,
        json={"show_answers": True},
    )
    assert released.status_code == 200, released.text
    result = learning_client.get(
        f"/learning/quizzes/{quiz_id}/my-result", headers=student_headers
    ).json()
    assert result["released"] is True
    assert result["score"] == 9
    assert result["answers"][0]["correct_answers"] == ["A"]

    stats = learning_client.get(
        f"/learning/quizzes/{quiz_id}/stats", headers=author_headers
    ).json()
    assert stats["submitted_count"] == 1
    assert stats["questions"][0]["correct_rate"] == 100


def test_web_and_mobile_sessions_can_coexist(learning_client):
    web_headers = _login(learning_client, "quiz_student")
    mobile_headers = _login(learning_client, "quiz_student", mobile=True)

    assert learning_client.get("/auth/me", headers=web_headers).status_code == 200
    assert learning_client.get("/auth/me", headers=mobile_headers).status_code == 200
