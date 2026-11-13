"""Read fresh task state even when IPD's detail/action-history rendering fails."""
from __future__ import annotations

import logging

from app.services.zentao_task_schedule_service import _id
from app.services.zentao_v2_protocol import ZentaoAPIError

logger = logging.getLogger(__name__)


def read_task_with_fallback(client, task_id, execution_id=None, *, skip_detail=False):
    if not skip_detail:
        try:
            task = client.get_task(task_id)
            if isinstance(task, dict) and _id(task.get('id')) == task_id:
                return task
        except Exception as exc:
            logger.warning('task detail read failed task=%s kind=%s', task_id, type(exc).__name__)
    # The local execution locates the live list; cached task state is never
    # accepted as proof that a remote action succeeded or can safely be sent.
    if execution_id:
        tasks = client.list_execution_tasks(execution_id)
        if isinstance(tasks, list):
            for task in tasks:
                if (isinstance(task, dict) and _id(task.get('id')) == task_id
                        and _id(task.get('execution')) == execution_id):
                    logger.info('task read recovered from execution list task=%s execution=%s', task_id, execution_id)
                    return task
    raise ZentaoAPIError(502, '无法从禅道详情或执行任务列表核对任务')
