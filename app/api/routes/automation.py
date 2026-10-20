from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.exceptions import AppError
from app.models import AutomationRepository, AutomationRun
from app.schemas.automation import (RepositoryCreate, CatalogUpload, RunCreate, BuildStarted,
                                    RunResultUpload, ExternalRunCreate, WritebackConfiguration, ZentaoLaunch,
                                    IntegrationKeyConfiguration)
from app.services import automation_service as service
from app.services.permission_service import ensure_tab_access
from app.core.config import settings

router = APIRouter(prefix='/automation', tags=['automation'])


def automation_user(user=Depends(get_current_user)):
    ensure_tab_access(user, 'testcase-center')
    return user


def integration_repository(repository_id: int, x_automation_key: str | None = Header(default=None), db: Session = Depends(get_db)):
    return service.repository_for_key(db, repository_id, x_automation_key)


async def callback_body(request, schema):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 4 * 1024 * 1024:
            raise AppError('自动化回调超过 4MB，请缩短日志摘要', status_code=413)
        chunks.append(chunk)
    try:
        return schema.model_validate_json(b''.join(chunks))
    except ValidationError as exc:
        raise AppError('自动化回调格式不正确', status_code=422) from exc


@router.get('/repositories')
def repositories(user=Depends(automation_user), db: Session = Depends(get_db)):
    query = db.query(AutomationRepository)
    if not service.is_admin(user):
        query = query.filter_by(owner_id=user.id)
    return {'items': [service.serialize_repository(x) for x in query.order_by(AutomationRepository.id).all()]}


@router.post('/repositories', status_code=201)
def create_repository(payload: RepositoryCreate, user=Depends(automation_user), db: Session = Depends(get_db)):
    return service.create_repository(db, user, payload)


@router.get('/configuration/bindings')
def configuration_bindings(user=Depends(automation_user), db: Session = Depends(get_db)):
    return service.configuration_bindings(db, user)


@router.get('/configuration/integration-key')
def integration_key_status(user=Depends(automation_user), db: Session = Depends(get_db)):
    return service.integration_key_status(db, user)


@router.post('/configuration/integration-key')
def configure_integration_key(payload: IntegrationKeyConfiguration, user=Depends(automation_user), db: Session = Depends(get_db)):
    return service.configure_integration_key(db, user, payload)


@router.get('/configuration/zentao/{kind}')
def zentao_options(kind: Literal['products', 'projects', 'executions', 'builds', 'testtasks'],
                   product_id: int | None = Query(default=None, gt=0),
                   project_id: int | None = Query(default=None, gt=0),
                   execution_id: int | None = Query(default=None, gt=0),
                   build_id: int | None = Query(default=None, gt=0),
                   user=Depends(automation_user), db: Session = Depends(get_db)):
    return service.zentao_options(db, user, kind, product_id=product_id, project_id=project_id,
                                  execution_id=execution_id, build_id=build_id)


@router.post('/repositories/{repository_id}/catalog')
def upload_catalog(repository_id: int, payload: CatalogUpload, user=Depends(automation_user), db: Session = Depends(get_db)):
    repository = service.repository_for_user(db, repository_id, user)
    return service.save_catalog(db, repository, payload)


@router.put('/repositories/{repository_id}/writeback')
def configure_writeback(repository_id: int, payload: WritebackConfiguration,
                        user=Depends(automation_user), db: Session = Depends(get_db)):
    repository = service.repository_for_user(db, repository_id, user)
    if payload.enabled:
        if not repository.default_testtask_id:
            raise AppError('启用回写前必须配置专用测试单', status_code=422)
        ids = [x['case_id'] for x in json.loads(repository.catalog_json) if x.get('case_id')]
        if not ids:
            raise AppError('请先导入带禅道关联的脚本目录', status_code=422)
        client = service.automation_zentao.client_for_repository(db, repository)
        try:
            client.prepare(repository, repository.default_testtask_id, ids)
        finally:
            client.close()
    repository.writeback_enabled = payload.enabled
    db.commit()
    return service.serialize_repository(repository)


@router.get('/repositories/{repository_id}/runs')
def runs(repository_id: int, user=Depends(automation_user), db: Session = Depends(get_db)):
    repository = service.repository_for_user(db, repository_id, user)
    items = db.query(AutomationRun).filter_by(repository_id=repository.id).order_by(AutomationRun.created_at.desc()).limit(100).all()
    return {'items': [service.serialize_run(db, x) for x in items]}


@router.get('/repositories/{repository_id}/testtasks')
def repository_testtasks(repository_id: int, user=Depends(automation_user), db: Session = Depends(get_db)):
    repository = service.repository_for_user(db, repository_id, user)
    return service.repository_testtasks(db, repository)


@router.post('/repositories/{repository_id}/runs', status_code=201)
def create_run(repository_id: int, payload: RunCreate, user=Depends(automation_user), db: Session = Depends(get_db)):
    repository = service.repository_for_user(db, repository_id, user)
    return service.serialize_run(db, service.create_run(db, repository, user.id, payload), detail=True)


def user_run(db, run_id, user):
    run = db.get(AutomationRun, run_id)
    if not run:
        raise AppError('运行不存在', status_code=404)
    return service.repository_for_user(db, run.repository_id, user), run


@router.get('/runs/{run_id}')
def get_run(run_id: str, user=Depends(automation_user), db: Session = Depends(get_db)):
    _, run = user_run(db, run_id, user)
    return service.serialize_run(db, run, detail=True)


@router.post('/runs/{run_id}/refresh')
def refresh_run(run_id: str, user=Depends(automation_user), db: Session = Depends(get_db)):
    repository, run = user_run(db, run_id, user)
    return service.refresh_run(db, repository, run)


@router.post('/runs/{run_id}/sync', status_code=202)
def sync_run(run_id: str, background_tasks: BackgroundTasks, user=Depends(automation_user), db: Session = Depends(get_db)):
    repository, run = user_run(db, run_id, user)
    if not repository.writeback_enabled:
        raise AppError('该自动化项目尚未启用禅道回写', status_code=422)
    background_tasks.add_task(service.sync_run_background, run.id)
    return service.serialize_run(db, run, detail=True)


@router.post('/integrations/{repository_id}/catalog')
async def integration_catalog(request: Request, repository=Depends(integration_repository), db: Session = Depends(get_db)):
    return service.save_catalog(db, repository, await callback_body(request, CatalogUpload))


@router.post('/integrations/{repository_id}/launch', status_code=201)
async def zentao_launch(request: Request, repository=Depends(integration_repository), db: Session = Depends(get_db)):
    payload = await callback_body(request, ZentaoLaunch)
    run = service.create_run(db, repository, repository.owner_id,
            RunCreate.model_validate(payload.model_dump(include=set(RunCreate.model_fields))),
            source='zentao', external_actor=payload.zentao_account)
    return service.serialize_run(db, run, detail=True)


@router.get('/integrations/{repository_id}/runs/{run_id}/plan')
def integration_plan(run_id: str, repository=Depends(integration_repository), db: Session = Depends(get_db)):
    run = service.run_for_repository(db, repository, run_id)
    return service.serialize_run(db, run, detail=True)


@router.post('/integrations/{repository_id}/runs/register', status_code=201)
async def external_run(request: Request, repository=Depends(integration_repository), db: Session = Depends(get_db)):
    payload = await callback_body(request, ExternalRunCreate)
    if payload.revision != repository.catalog_revision or payload.job_name != repository.jenkins_job:
        raise AppError('外部执行的脚本提交或 Job 与目录不一致', status_code=409)
    run = service.create_run(db, repository, repository.owner_id,
            RunCreate.model_validate(payload.model_dump(include=set(RunCreate.model_fields))), dispatch=False, source=payload.source)
    service.bind_build(db, repository, run, payload)
    return service.serialize_run(db, run, detail=True)


@router.post('/integrations/{repository_id}/runs/{run_id}/started')
async def integration_started(run_id: str, request: Request, repository=Depends(integration_repository), db: Session = Depends(get_db)):
    run = service.run_for_repository(db, repository, run_id)
    service.bind_build(db, repository, run, await callback_body(request, BuildStarted))
    return service.serialize_run(db, run, detail=True)


@router.post('/integrations/{repository_id}/runs/{run_id}/results')
async def integration_results(run_id: str, request: Request, background_tasks: BackgroundTasks,
                              repository=Depends(integration_repository), db: Session = Depends(get_db)):
    run = service.run_for_repository(db, repository, run_id)
    service.receive_results(db, repository, run, await callback_body(request, RunResultUpload))
    if repository.writeback_enabled:
        background_tasks.add_task(service.sync_run_background, run.id)
    return service.serialize_run(db, run, detail=True)


@router.post('/integrations/{repository_id}/runs/{run_id}/device/{action}')
def integration_device(run_id: str, action: Literal['lock', 'unlock', 'heartbeat'],
                       repository=Depends(integration_repository), db: Session = Depends(get_db)):
    from app.models import TerminalDevice
    from app.services.device_lock_service import DeviceConflict, DeviceLockService
    run = service.run_for_repository(db, repository, run_id)
    if run.dry_run or not settings.terminal_control_enabled:
        return {'managed': False, 'message': '设备管理未启用或本次为脚本演练'}
    locks = DeviceLockService(db)
    device = db.query(TerminalDevice).filter_by(serial=run.device_serial).first()
    active = locks.active_lock(device.id) if device else None
    if active and (active.jenkins_build != run.id or active.jenkins_job != repository.jenkins_job):
        raise AppError('设备正被其他会话占用，未更改设备锁', status_code=409)
    if action != 'unlock' and not run.build_number:
        raise AppError('请先登记实际 Jenkins 构建', status_code=409)
    if action == 'lock' and run.status != 'running':
        raise AppError('该运行已结束，不能重新占用设备', status_code=409)
    try:
        if action == 'lock':
            return locks.automation_lock(run.device_serial, jenkins_job=repository.jenkins_job, jenkins_build=run.id)
        if action == 'unlock':
            return locks.automation_unlock(run.device_serial, jenkins_build=run.id)
        return locks.automation_heartbeat(run.device_serial)
    except DeviceConflict as exc:
        raise AppError('设备锁操作被拒绝，设备正被其他会话占用', status_code=409) from exc
