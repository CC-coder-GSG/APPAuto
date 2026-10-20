from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import uuid
from urllib.parse import quote, urlsplit

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import AppError
from app.models import AutomationRepository, AutomationRun, AutomationCaseResult, User, UserZentaoBinding
from app.schemas.automation import CatalogUpload, RunCreate
from app.services import jenkins_service, automation_zentao
from app.services.zentao_v2_protocol import normalize_base_url
from app.services.zentao_auth_service import get_valid_token
from app.services.zentao_client_service import ZentaoClient, ZentaoAPIError
from app.utils.time_utils import utc_now_naive


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256((value if isinstance(value, str) else encode(value)).encode()).hexdigest()


def is_admin(user):
    return getattr(user.role, 'value', user.role) == 'admin'


def repository_for_user(db, repository_id, user):
    row = db.get(AutomationRepository, repository_id)
    if not row or (row.owner_id != user.id and not is_admin(user)):
        raise AppError('自动化项目不存在或无权访问', status_code=404)
    return row


def repository_for_key(db, repository_id, key):
    row = db.get(AutomationRepository, repository_id)
    if not key or not row or not hmac.compare_digest(row.integration_key_hash, digest(key)):
        raise AppError('自动化集成凭据无效', status_code=401)
    return row


def serialize_repository(row):
    return {key: getattr(row, key) for key in ('id', 'owner_id', 'name', 'git_url', 'branch', 'jenkins_job',
            'jenkins_base_url', 'zentao_base_url', 'product_id', 'execution_id', 'target_build_id',
            'default_testtask_id', 'writeback_enabled', 'catalog_revision')} | {'cases': json.loads(row.catalog_json)}


def create_repository(db, user, payload):
    creds = jenkins_service.get_binding_creds(db, user.id)
    if not creds:
        raise AppError('请先保存有效的 Jenkins 账号绑定', status_code=422)
    binding = _zentao_binding(db, user.id)
    base_url = normalize_base_url(binding.base_url)
    if payload.zentao_base_url and normalize_base_url(payload.zentao_base_url) != base_url:
        raise AppError('自动化项目须使用本人已绑定的禅道地址，请刷新配置后重试', status_code=422)
    key = secrets.token_urlsafe(40)
    row = AutomationRepository(owner_id=user.id, jenkins_base_url=creds[0].rstrip('/'),
                               zentao_base_url=base_url, integration_key_hash=digest(key),
                               **payload.model_dump(exclude={'zentao_base_url'}))
    db.add(row)
    db.commit()
    return {'repository': serialize_repository(row), 'integration_key': key}


def _zentao_binding(db, user_id):
    binding = db.query(UserZentaoBinding).filter_by(user_id=user_id).first()
    if not binding:
        raise AppError('请先在账号设置中绑定禅道账号', status_code=422)
    return binding


def configuration_bindings(db, user):
    binding = db.query(UserZentaoBinding).filter_by(user_id=user.id).first()
    creds = jenkins_service.get_binding_creds(db, user.id)
    return {'zentao_base_url': normalize_base_url(binding.base_url) if binding else None,
            'jenkins_base_url': creds[0] if creds else None}


def _configuration_client(db, user):
    binding = _zentao_binding(db, user.id)
    token = get_valid_token(user.id, db)
    if not token:
        raise AppError('禅道认证已失效，请在账号设置中刷新绑定', status_code=422)
    return ZentaoClient.from_binding(binding, token)


def _build_ids(value):
    if isinstance(value, dict):
        return {automation_zentao.object_id(value)} if value.get('id') else {
            automation_zentao.object_id(key) for key in value}
    if isinstance(value, list):
        return {automation_zentao.object_id(item) for item in value}
    return {automation_zentao.object_id(item) for item in str(value or '').split(',')}


def zentao_options(db, user, kind, *, product_id=None, project_id=None, execution_id=None, build_id=None):
    if kind in {'builds', 'testtasks'} and not product_id:
        raise AppError('请先选择禅道产品', status_code=422)
    if kind == 'executions' and not project_id:
        raise AppError('请先选择禅道项目', status_code=422)
    if kind == 'builds' and not execution_id:
        raise AppError('请先选择研发执行以获取构建列表', status_code=422)
    client = _configuration_client(db, user)
    oid = automation_zentao.object_id
    try:
        if kind == 'products':
            rows = client.list_products()
        elif kind == 'projects':
            rows = client.list_projects()
        elif kind == 'executions':
            rows = client.list_project_executions(project_id)
        elif kind == 'builds':
            rows = [row for row in client.list_execution_builds(execution_id) if oid(row.get('product')) == product_id]
        else:
            rows = [row for row in client.list_product_testtasks(product_id)
                    if str(row.get('status', '')).lower() not in {'closed', 'done', '已关闭', '已完成'}
                    and (not execution_id or oid(row.get('execution')) == execution_id)
                    and (not build_id or build_id in _build_ids(row.get('build')))]
    except ZentaoAPIError as exc:
        raise AppError(f'获取禅道选项失败：{exc.message}', status_code=502) from exc
    return {'items': [{'id': oid(row), 'name': str(row.get('name') or row.get('title') or f'#{oid(row)}'),
                       'status': str(row.get('status') or '')} for row in rows if oid(row)]}


def repository_testtasks(db, repository):
    binding = _zentao_binding(db, repository.owner_id)
    if normalize_base_url(binding.base_url) != repository.zentao_base_url:
        raise AppError('项目的禅道地址与账号绑定不一致，请先核对账号设置', status_code=422)
    result = zentao_options(db, db.get(User, repository.owner_id), 'testtasks', product_id=repository.product_id,
                            execution_id=repository.execution_id, build_id=repository.target_build_id)
    if repository.default_testtask_id:
        result['items'] = [row for row in result['items'] if row['id'] == repository.default_testtask_id]
    return result


def save_catalog(db, repository, payload: CatalogUpload):
    if payload.branch != repository.branch:
        raise AppError('脚本目录分支与自动化项目不一致', status_code=409)
    cases = [row.model_dump() for row in payload.cases]
    ids = [x['case_id'] for x in cases if x['case_id']]
    identities = [x['automation_id'] for x in cases]
    if len(ids) != len(set(ids)) or len(identities) != len(set(identities)):
        raise AppError('脚本标识或禅道用例 ID 重复，整个目录未更新', status_code=422)
    for case in cases:
        tags = [x.lower().replace(' ', '').replace('_', '') for x in case['tags'] if x.lower().startswith('zentao:')]
        expected = [f'zentao:{case["case_id"]}'] if case['case_id'] else []
        if tags != expected:
            raise AppError('脚本目录中的禅道标签与关联 ID 不一致', status_code=422)
    if repository.catalog_revision == payload.revision and encode(cases) != repository.catalog_json:
        raise AppError('同一 Git 提交的目录内容发生变化，未覆盖原目录', status_code=409)
    repository.catalog_revision = payload.revision
    repository.catalog_json = encode(cases)
    db.commit()
    return serialize_repository(repository)


def _jenkins(db, repository):
    creds = jenkins_service.get_binding_creds(db, repository.owner_id)
    if not creds or creds[0].rstrip('/') != repository.jenkins_base_url:
        raise AppError('Jenkins 绑定缺失或地址已变化，请重新配置自动化项目', status_code=409)
    return jenkins_service.get_client_for_user(db, repository.owner_id)


def _job_path(job):
    return '/'.join('job/' + quote(x, safe='') for x in job.split('/'))


def build_url(repository, number):
    return f'{repository.jenkins_base_url}/{_job_path(repository.jenkins_job)}/{number}/'


def _prepare_plan(db, repository, payload):
    if not repository.catalog_revision:
        raise AppError('请先从 Git 同步脚本目录', status_code=422)
    if len(payload.case_ids) != len(set(payload.case_ids)):
        raise AppError('所选用例 ID 重复', status_code=422)
    if repository.default_testtask_id and payload.testtask_id != repository.default_testtask_id:
        raise AppError('结果只能关联到配置的专用自动化测试单', status_code=422)
    catalog = {x['case_id']: x for x in json.loads(repository.catalog_json) if x.get('case_id')}
    if any(cid not in catalog for cid in payload.case_ids):
        raise AppError('所选用例缺少当前 Git 提交下的自动化脚本', status_code=422)
    zentao = automation_zentao.client_for_repository(db, repository)
    try:
        contexts = zentao.prepare(repository, payload.testtask_id, payload.case_ids)
    finally:
        zentao.close()
    return [catalog[x['case_id']] | x for x in contexts]


def create_run(db, repository, user_id, payload: RunCreate, *, dispatch=True, source='platform', external_actor=None):
    body = payload.model_dump()
    fingerprint = digest({'request': body, 'source': source, 'external_actor': external_actor})
    existing = db.query(AutomationRun).filter_by(repository_id=repository.id, idempotency_key=payload.idempotency_key).first()
    if existing:
        if existing.request_hash != fingerprint:
            raise AppError('重复请求的参数不同，未创建新运行', status_code=409)
        return existing
    plan = _prepare_plan(db, repository, payload)
    client = _jenkins(db, repository)
    definitions = [d for p in client.get_job(repository.jenkins_job).get('property', []) for d in p.get('parameterDefinitions', [])]
    names = {x['name'] for x in definitions}
    required = {'AUTOMATION_RUN_ID', 'ZENTAO_CASE_IDS', 'ZENTAO_TESTTASK_ID', 'TEST_REVISION', 'OMNIQA_ENABLED', 'OMNIQA_DRY_RUN'}
    if dispatch and not required.issubset(names):
        raise AppError('Jenkins Job 尚未安装自动化接入流水线或缺少运行参数', status_code=422)
    for key, value in payload.parameters.items():
        if (key not in names or key in required or key in {'TEST_BRANCH', 'DEVICE_ID', 'APK_PATH', 'OMNIQA_URL', 'OMNIQA_REPOSITORY_ID'}
                or key.startswith('OMNIQA_') or key.startswith('AUTOMATION_')
                or any(x in key.upper() for x in ('TOKEN', 'PASSWORD', 'SECRET', 'CREDENTIAL', 'API_KEY'))
                or len(str(value)) > 2000):
            raise AppError(f'不允许的 Jenkins 参数：{key[:80]}', status_code=422)
    run = AutomationRun(id=str(uuid.uuid4()), repository_id=repository.id, requested_by_id=user_id,
                        idempotency_key=payload.idempotency_key, request_hash=fingerprint, source=source, dry_run=payload.dry_run, external_actor=external_actor,
                        revision=repository.catalog_revision, branch=repository.branch,
                        testtask_id=payload.testtask_id, device_serial=payload.device_serial,
                        apk_reference=payload.apk_reference, plan_json=encode(plan),
                        parameters_json=encode(payload.parameters), status='dispatching' if dispatch else 'created')
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(AutomationRun).filter_by(repository_id=repository.id, idempotency_key=payload.idempotency_key).one()
        if existing.request_hash != fingerprint:
            raise AppError('重复请求的参数不同', status_code=409)
        return existing
    if dispatch:
        params = dict(payload.parameters) | {'AUTOMATION_RUN_ID': run.id, 'ZENTAO_CASE_IDS': ','.join(map(str, sorted(payload.case_ids))),
                  'ZENTAO_TESTTASK_ID': str(run.testtask_id), 'TEST_REVISION': run.revision, 'TEST_BRANCH': run.branch,
                  'DEVICE_ID': run.device_serial, 'APK_PATH': run.apk_reference}
        params['OMNIQA_ENABLED'] = 'true'
        params['OMNIQA_DRY_RUN'] = 'true' if run.dry_run else 'false'
        try:
            location = client.trigger_build(repository.jenkins_job, params=params)
            expected = urlsplit(repository.jenkins_base_url)
            actual = urlsplit(location or '')
            prefix = expected.path.rstrip('/') + '/queue/item/'
            if actual.scheme != expected.scheme or actual.netloc != expected.netloc or not actual.path.startswith(prefix) or not actual.path[len(prefix):].strip('/').isdigit() or actual.query or actual.fragment:
                raise ValueError('Unconfirmed queue location')
            run.queue_url = location
            # The callback can arrive before the trigger HTTP response.
            db.refresh(run)
            run.queue_url = location
            if run.status == 'dispatching':
                run.status = 'queued'
            db.commit()
        except Exception:
            db.refresh(run)
            if run.status == 'dispatching':
                run.status = 'dispatch_unknown'
                run.message = 'Jenkins 是否接收请求尚未确认，请核对队列，勿重复启动'
                db.commit()
    return run


def run_for_repository(db, repository, run_id):
    row = db.get(AutomationRun, run_id)
    if not row or row.repository_id != repository.id:
        raise AppError('运行不存在', status_code=404)
    return row


def bind_build(db, repository, run, payload):
    if (payload.job_name != repository.jenkins_job or payload.revision != run.revision
            or payload.device_serial != run.device_serial or (run.build_number and run.build_number != payload.build_number)):
        raise AppError('Jenkins 构建、代码版本或设备与运行计划不一致', status_code=409)
    client = _jenkins(db, repository)
    info = client.get_build(repository.jenkins_job, payload.build_number)
    params = {x['name']: str(x.get('value', '')) for a in info.get('actions', []) for x in a.get('parameters', [])}
    if (run.source == 'platform' or run.external_actor) and params.get('AUTOMATION_RUN_ID') != run.id:
        raise AppError('Jenkins 实际构建参数没有匹配的运行编号', status_code=409)
    if (int(info.get('number', 0)) != payload.build_number or params.get('TEST_REVISION', run.revision) != run.revision):
        raise AppError('无法验证 Jenkins 实际构建', status_code=409)
    run.build_number = payload.build_number
    run.build_url = build_url(repository, payload.build_number)
    if run.status in {'created', 'dispatching', 'queued', 'dispatch_unknown'}:
        run.status = 'running'
    if not run.started_at and not run.result_hash:
        run.started_at = utc_now_naive()
    db.commit()


def receive_results(db, repository, run, payload):
    if payload.dry_run != run.dry_run:
        raise AppError('演练模式与运行计划不一致', status_code=409)
    fingerprint = digest(payload.model_dump())
    if run.result_hash:
        if run.result_hash != fingerprint:
            raise AppError('该运行已有不同结果，请创建新的重跑批次', status_code=409)
        return run
    bind_build(db, repository, run, payload)
    plan = {x['case_id']: x for x in json.loads(run.plan_json)}
    ids = [x.case_id for x in payload.results]
    if len(ids) != len(set(ids)) or not set(ids).issubset(plan) or (payload.outcome == 'completed' and set(ids) != set(plan)):
        raise AppError('结果用例集合与运行计划不一致', status_code=422)
    # Serialize terminal callbacks before inserting results; duplicate concurrent
    # requests cannot race to insert or trigger a second Zentao write.
    claimed = db.execute(update(AutomationRun).where(AutomationRun.id == run.id, AutomationRun.result_hash.is_(None))
                         .values(result_hash=fingerprint)).rowcount
    if not claimed:
        db.rollback()
        db.refresh(run)
        if run.result_hash == fingerprint:
            return run
        raise AppError('已有另一个结果回调完成该运行', status_code=409)
    summary = {'PASS': 0, 'FAIL': 0, 'SKIP': 0, 'ERROR': 0, 'apk_sha256': payload.apk_sha256}
    for item in payload.results:
        summary[item.status] += 1
        db.add(AutomationCaseResult(run_id=run.id, case_id=item.case_id, case_version=plan[item.case_id]['case_version'],
               test_name=plan[item.case_id]['name'], status=item.status, duration_seconds=str(item.duration_seconds),
               message=item.message, evidence_json=encode(item.model_dump()),
               sync_message='脚本演练，不写入禅道执行记录' if run.dry_run else 'Robot 跳过，未改写禅道结论' if item.status == 'SKIP' else None,
               sync_status='skipped' if item.status == 'SKIP' or run.dry_run else 'pending'))
    summary['missing_case_ids'] = sorted(set(plan) - set(ids))
    run.summary_json = encode(summary)
    run.status = payload.outcome
    run.message = payload.message[:500] or None
    run.finished_at = utc_now_naive()
    db.commit()
    db.refresh(run)
    return run


def serialize_run(db, run, *, detail=False):
    data = {key: getattr(run, key) for key in ('id', 'repository_id', 'requested_by_id', 'source', 'external_actor', 'dry_run', 'status', 'revision',
            'branch', 'testtask_id', 'device_serial', 'apk_reference', 'queue_url', 'build_number', 'build_url', 'message',
            'created_at', 'started_at', 'finished_at')}
    data['summary'] = json.loads(run.summary_json)
    rows = db.query(AutomationCaseResult).filter_by(run_id=run.id).order_by(AutomationCaseResult.case_id).all()
    data['sync'] = {status: sum(x.sync_status == status for x in rows) for status in {'pending', 'syncing', 'synced', 'error', 'unknown', 'skipped'}}
    if detail:
        data['plan'] = json.loads(run.plan_json)
        data['results'] = [{key: getattr(x, key) for key in ('case_id', 'case_version', 'test_name', 'status', 'duration_seconds',
                           'message', 'sync_status', 'sync_message', 'zentao_result_id', 'synced_at')} for x in rows]
    return data


def synchronize_results(db, repository, run):
    if not repository.writeback_enabled:
        return serialize_run(db, run, detail=True)
    plan = {x['case_id']: x for x in json.loads(run.plan_json)}
    zentao = automation_zentao.client_for_repository(db, repository)
    try:
        for result in db.query(AutomationCaseResult).filter_by(run_id=run.id).order_by(AutomationCaseResult.id).all():
            old_status = result.sync_status
            if old_status in {'synced', 'skipped'}:
                continue
            # Only one worker may perform a fresh POST. Unknown/syncing outcomes
            # permit read-only reconciliation, including after a process crash.
            reconcile_only = old_status in {'unknown', 'syncing'}
            if not reconcile_only:
                changed = db.execute(update(AutomationCaseResult).where(AutomationCaseResult.id == result.id,
                           AutomationCaseResult.sync_status == old_status).values(sync_status='syncing')).rowcount
                db.commit()
                if not changed:
                    continue
            try:
                rid = zentao.submit(repository, run, plan[result.case_id], result, reconcile_only=reconcile_only)
                result.sync_status = 'synced'
                result.zentao_result_id = rid
                result.synced_at = utc_now_naive()
                result.sync_message = None
            except automation_zentao.ResultOutcomeUnknown as exc:
                result.sync_status = 'unknown'
                result.sync_message = exc.message
            except AppError as exc:
                result.sync_status = 'unknown' if reconcile_only else 'error'
                result.sync_message = exc.message[:500]
            except Exception:
                result.sync_status = 'unknown'
                result.sync_message = '结果回写未确认，请先核对禅道执行历史'
            db.commit()
    finally:
        zentao.close()
    return serialize_run(db, run, detail=True)


def refresh_run(db, repository, run):
    client = _jenkins(db, repository)
    if run.queue_url and not run.build_number:
        data = client.get_queue_item(run.queue_url)
        if data.get('cancelled'):
            run.status = 'cancelled'
            run.finished_at = utc_now_naive()
        executable = data.get('executable') or {}
        if executable.get('number'):
            run.build_number = int(executable['number'])
            run.build_url = build_url(repository, run.build_number)
            if run.status in {'queued', 'dispatching', 'dispatch_unknown'}:
                run.status = 'running'
                run.started_at = run.started_at or utc_now_naive()
    if run.build_number and not run.result_hash:
        info = client.get_build(repository.jenkins_job, run.build_number)
        if not info.get('building') and info.get('result'):
            run.message = 'Jenkins 已结束，等待结果回传；可以从该构建重新发送归档结果'
    db.commit()
    return serialize_run(db, run, detail=True)


def sync_run_background(run_id):
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        run = db.get(AutomationRun, run_id)
        if not run:
            return
        repository = db.get(AutomationRepository, run.repository_id)
        if repository and repository.writeback_enabled:
            try:
                synchronize_results(db, repository, run)
            except Exception:
                # Result receipt is durable even if credentials/network fail.
                db.rollback()
                for row in db.query(AutomationCaseResult).filter_by(run_id=run_id, sync_status='pending'):
                    row.sync_status = 'error'
                    row.sync_message = '无法连接禅道结果接口，请检查绑定后重试同步'
                db.commit()
