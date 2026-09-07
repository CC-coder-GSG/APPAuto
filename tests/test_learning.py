import unittest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models import User, UserRole


class LearningFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        cls.Session = sessionmaker(bind=cls.engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(cls.engine)

        db = cls.Session()
        db.add_all(
            [
                User(username="author", password_hash=User.hash_password("pass"), role=UserRole.USER),
                User(username="student", password_hash=User.hash_password("pass"), role=UserRole.USER),
            ]
        )
        db.commit()
        db.close()

        def override_db():
            db = cls.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        cls.client = TestClient(app)
        cls.author_headers = cls.login("author")
        cls.student_headers = cls.login("student")

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        cls.engine.dispose()

    @classmethod
    def login(cls, username):
        response = cls.client.post("/auth/token", data={"username": username, "password": "pass"})
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_complete_quiz_workflow(self):
        payload = {
            "title": "发布前学习检查",
            "description": "跨端只允许一次提交",
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
        created = self.client.post("/learning/quizzes", headers=self.author_headers, json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        quiz_id = created.json()["id"]

        student_drafts = self.client.get("/learning/quizzes", headers=self.student_headers).json()
        self.assertFalse(any(q["id"] == quiz_id for q in student_drafts))

        started = self.client.post(f"/learning/quizzes/{quiz_id}/start", headers=self.author_headers)
        self.assertEqual(started.status_code, 200, started.text)
        detail = self.client.get(f"/learning/quizzes/{quiz_id}", headers=self.student_headers).json()
        self.assertNotIn("correct_answers", detail["questions"][0])

        answers = {
            "answers": [
                {"question_id": detail["questions"][0]["id"], "answer": "A"},
                {"question_id": detail["questions"][1]["id"], "answer": "Omni， QA!"},
                {"question_id": detail["questions"][2]["id"], "answer": "需要回归验证"},
            ],
            "is_preview": False,
        }
        submitted = self.client.post(f"/learning/quizzes/{quiz_id}/submissions", headers=self.student_headers, json=answers)
        self.assertEqual(submitted.status_code, 201, submitted.text)
        self.assertEqual(submitted.json()["status"], "submitted")
        duplicate = self.client.post(f"/learning/quizzes/{quiz_id}/submissions", headers=self.student_headers, json=answers)
        self.assertEqual(duplicate.status_code, 409)

        self.client.post(f"/learning/quizzes/{quiz_id}/close", headers=self.author_headers)
        rows = self.client.get(f"/learning/quizzes/{quiz_id}/submissions", headers=self.author_headers).json()
        submission = next(x for x in rows if not x["is_preview"])
        grades = {
            "grades": [
                {
                    "question_id": answer["question_id"],
                    "awarded_score": 4 if answer["question_type"] == "short_answer" else answer["awarded_score"],
                }
                for answer in submission["answers"]
            ]
        }
        graded = self.client.put(
            f"/learning/quizzes/{quiz_id}/submissions/{submission['id']}/grade",
            headers=self.author_headers,
            json=grades,
        )
        self.assertEqual(graded.status_code, 200, graded.text)
        self.assertEqual(graded.json()["total_score"], 9)

        released = self.client.post(
            f"/learning/quizzes/{quiz_id}/release",
            headers=self.author_headers,
            json={"show_answers": True},
        )
        self.assertEqual(released.status_code, 200, released.text)
        result = self.client.get(f"/learning/quizzes/{quiz_id}/my-result", headers=self.student_headers).json()
        self.assertTrue(result["released"])
        self.assertEqual(result["score"], 9)
        self.assertEqual(result["answers"][0]["correct_answers"], ["A"])

        stats = self.client.get(f"/learning/quizzes/{quiz_id}/stats", headers=self.author_headers).json()
        self.assertEqual(stats["submitted_count"], 1)
        self.assertEqual(stats["questions"][0]["correct_rate"], 100)

    def test_creator_can_edit_after_preview(self):
        payload = {
            "title": "可预览草稿",
            "questions": [
                {
                    "question_type": "single_choice",
                    "prompt": "旧题目",
                    "score": 1,
                    "options": ["是", "否"],
                    "correct_answers": ["是"],
                }
            ],
        }
        created = self.client.post("/learning/quizzes", headers=self.author_headers, json=payload).json()
        question_id = created["questions"][0]["id"]
        preview = self.client.post(
            f"/learning/quizzes/{created['id']}/submissions",
            headers=self.author_headers,
            json={"is_preview": True, "answers": [{"question_id": question_id, "answer": "是"}]},
        )
        self.assertEqual(preview.status_code, 201, preview.text)
        payload["questions"][0]["prompt"] = "修改后的题目"
        updated = self.client.put(f"/learning/quizzes/{created['id']}", headers=self.author_headers, json=payload)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["questions"][0]["prompt"], "修改后的题目")

    def test_web_and_mobile_sessions_can_coexist(self):
        first = self.login("student")
        second = self.login("student")
        self.assertEqual(self.client.get("/auth/me", headers=first).status_code, 200)
        self.assertEqual(self.client.get("/auth/me", headers=second).status_code, 200)


if __name__ == "__main__":
    unittest.main()
