from app.services.zentao_task_status import effective_task_status


def test_changed_with_positive_left_is_active_even_with_old_finish_date():
    task = {
        "status": "changed",
        "left": 3,
        "finishedDate": "2026-07-06 18:45:02",
    }

    assert effective_task_status(task) == "changed"


def test_changed_with_zero_left_and_finish_fact_is_done():
    task = {
        "status": "changed",
        "left": 0,
        "finishedDate": "2026-07-24 17:19:11",
    }

    assert effective_task_status(task) == "done"


def test_changed_with_zero_left_but_no_finish_fact_is_not_assumed_done():
    assert effective_task_status({"status": "changed", "left": 0}) == "changed"
