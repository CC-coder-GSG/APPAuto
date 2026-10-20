"""Isolated, execution-only REST v1 adapter. Existing business clients stay on v2.

Never use ciresults: it can overwrite case definitions. Every write has a unique
marker, a frozen run/version/steps context, and read-after-write reconciliation.
"""
from __future__ import annotations

import hashlib
import json

import httpx

from app.core.exceptions import AppError
from app.models import UserZentaoBinding
from app.services.zentao_auth_service import decrypt_password
from app.services.zentao_v2_protocol import normalize_base_url


def object_id(value) -> int:
    if isinstance(value, dict):
        value = value.get('id')
    try:
        return int(value or 0)
    except (ValueError, TypeError):
        return 0


def definition_fingerprint(case: dict) -> str:
    # Execution updates lastRunner/lastRunResult, not business definitions.
    fields = ('id', 'product', 'title', 'precondition', 'type', 'stage', 'pri', 'status', 'version', 'steps')
    raw = {key: case.get(key) for key in fields}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ResultOutcomeUnknown(AppError):
    def __init__(self):
        super().__init__('禅道结果尚未确认，请先核对执行历史，禁止直接重复写入', status_code=409)


class AutomationZentaoClient:
    def __init__(self, base_url: str, account: str, password: str):
        self.base_url = normalize_base_url(base_url)
        self.http = httpx.Client(timeout=20, follow_redirects=False)
        try:
            response = self.http.post(f'{self.base_url}/api.php/v1/tokens', json={'account': account, 'password': password})
            data = self._decode(response)
            if not isinstance(data.get('token'), str) or not data['token']:
                raise AppError('禅道执行结果接口认证失败', status_code=502)
            self.http.headers['Token'] = data['token']
        except Exception:
            self.http.close()
            raise

    def close(self):
        self.http.close()

    @staticmethod
    def _decode(response) -> dict:
        if not 200 <= response.status_code < 300:
            raise AppError(f'禅道执行结果接口 HTTP {response.status_code}', status_code=502)
        try:
            data = json.loads(response.text.lstrip('\ufeff').strip())
        except (TypeError, ValueError) as exc:
            raise AppError('禅道执行结果接口未返回有效 JSON', status_code=502) from exc
        if (not isinstance(data, dict) or data.get('error') or data.get('errors')
                or data.get('result') in ('fail', 'error') or data.get('status') in ('fail', 'error')):
            raise AppError('禅道拒绝执行结果请求，请检查接口权限与测试单状态', status_code=502)
        return data

    def get(self, path: str, **params):
        try:
            return self._decode(self.http.get(f'{self.base_url}/api.php/v1/{path}', params=params))
        except httpx.HTTPError as exc:
            raise AppError('读取禅道执行上下文失败', status_code=502) from exc

    def prepare(self, repository, testtask_id: int, case_ids: list[int]) -> list[dict]:
        requested = set(case_ids)
        found = {}
        for page in range(1, 101):
            task = self.get(f'testtasks/{testtask_id}', limit=500, page=page)
            if object_id(task.get('id')) != testtask_id or object_id(task.get('product')) != repository.product_id:
                raise AppError('测试单不存在或不属于该产品', status_code=422)
            if repository.execution_id and object_id(task.get('execution')) != repository.execution_id:
                raise AppError('测试单不属于配置的研发执行', status_code=422)
            if repository.target_build_id:
                build = task.get('build')
                builds = [object_id(x) for x in build] if isinstance(build, list) else [object_id(x) for x in str(build).split(',')]
                if repository.target_build_id not in builds:
                    raise AppError('测试单没有关联指定的自动化测试构建', status_code=422)
            if str(task.get('status', '')).lower() in {'closed', 'done', '已关闭', '已完成'}:
                raise AppError('测试单已关闭，请选择进行中的测试单', status_code=422)
            rows = task.get('testcases')
            if not isinstance(rows, list):
                raise AppError('无法确认测试单用例集合', status_code=502)
            for row in rows:
                cid = object_id(row.get('case') or row.get('caseID'))
                if cid in requested:
                    if not object_id(row.get('id')):
                        raise AppError('测试单缺少用例运行 ID', status_code=502)
                    found[cid] = row
            if requested.issubset(found) or len(rows) < 500:
                break
        if not requested.issubset(found):
            raise AppError('部分用例尚未关联到指定测试单', status_code=422)
        result = []
        for cid in sorted(requested):
            case = self.get(f'testcases/{cid}')
            version = object_id(case.get('version'))
            run = found[cid]
            if (object_id(case.get('id')) != cid or object_id(case.get('product')) != repository.product_id
                    or not version or object_id(run.get('version')) != version or case.get('deleted', 0) not in (False, 0, '0')):
                raise AppError(f'用例 #{cid} 的产品、版本或测试单关联发生变化', status_code=409)
            steps = case.get('steps')
            if isinstance(steps, dict):
                steps = list(steps.values())
            if not isinstance(steps, list):
                raise AppError('无法确认禅道用例步骤', status_code=502)
            leaf_ids = [object_id(s.get('id')) for s in steps if s.get('type') != 'group']
            if not leaf_ids or not all(leaf_ids) or len(set(leaf_ids)) != len(leaf_ids):
                raise AppError(f'用例 #{cid} 没有可验证的执行步骤', status_code=422)
            result.append({'case_id': cid, 'case_version': version, 'run_id': object_id(run.get('id')),
                           'title': case.get('title', ''), 'step_ids': leaf_ids,
                           'definition_hash': definition_fingerprint(case)})
        return result

    def history(self, case_id: int, version: int, run_id: int) -> list[dict]:
        rows = self.get(f'testcases/{case_id}/results', version=version, runID=run_id).get('results')
        if not isinstance(rows, list):
            raise AppError('无法确认禅道执行历史，已停止写入', status_code=502)
        return rows

    def submit(self, repository, run, plan: dict, result, *, reconcile_only=False) -> int:
        marker = f'[OmniQA:{run.id}:{result.case_id}]'
        context = self.prepare(repository, run.testtask_id, [result.case_id])[0]
        if any(context[key] != plan[key] for key in ('case_version', 'run_id', 'step_ids', 'definition_hash')):
            raise AppError('用例内容或测试单版本已变化，结果保留在平台，未写入禅道', status_code=409)

        def locate(rows):
            matches = []
            for row in rows:
                if (object_id(row.get('run')) != plan['run_id'] or object_id(row.get('version')) != plan['case_version']
                        or object_id(row.get('case')) != result.case_id):
                    continue
                if marker in json.dumps(row.get('stepResults', {}), ensure_ascii=False):
                    expected_status = {'PASS': 'pass', 'FAIL': 'fail', 'ERROR': 'blocked'}.get(result.status)
                    if row.get('caseResult') != expected_status:
                        raise ResultOutcomeUnknown()
                    matches.append(row)
            if len(matches) > 1:
                raise ResultOutcomeUnknown()
            return object_id(matches[0].get('id')) if matches else 0

        existing = locate(self.history(result.case_id, plan['case_version'], plan['run_id']))
        if existing:
            return existing
        if reconcile_only:
            raise ResultOutcomeUnknown()
        evidence = json.loads(result.evidence_json)
        explicit = evidence.get('steps', [])
        step_ids = plan['step_ids']
        mapped = {x['step_id']: x for x in explicit}
        if explicit and (len(mapped) != len(explicit) or set(mapped) != set(step_ids)):
            raise AppError('步骤结果与禅道步骤不一致，已停止回写', status_code=422)
        if not explicit and len(step_ids) != 1:
            raise AppError('多步骤用例需要 zentao-step:<用例ID>:<步骤ID> 关键字标签，整体结果已保留', status_code=422)
        status_map = {'PASS': 'pass', 'FAIL': 'fail', 'ERROR': 'blocked', 'SKIP': 'n/a'}
        steps = []
        for step_id in step_ids:
            step = mapped.get(step_id, {'status': result.status, 'message': result.message})
            steps.append({'result': status_map[step['status']],
                          'real': f'{marker}\nGit: {run.revision}\nJenkins: {run.build_url}\n'
                                  f'Robot: {result.status}; {result.duration_seconds}s\n{step.get("message", "")}'} )
        # Never silently turn a skipped multi-step result into a pass.
        expected = 'fail' if any(x['result'] == 'fail' for x in steps) else 'blocked' if any(x['result'] == 'blocked' for x in steps) else 'pass'
        if status_map[result.status] != expected:
            raise AppError('步骤结论与用例整体结论不一致，未写入禅道', status_code=422)
        try:
            response = self.http.post(f'{self.base_url}/api.php/v1/testcases/{result.case_id}/results',
                                      params={'testtask': run.testtask_id, 'version': plan['case_version']},
                                      json={'steps': steps}, timeout=45)
            self._decode(response)
        except Exception:
            # A failed transport may have committed. Do not replay the POST.
            try:
                found = locate(self.history(result.case_id, plan['case_version'], plan['run_id']))
                if found:
                    return found
            except Exception:
                pass
            raise ResultOutcomeUnknown()
        try:
            found = locate(self.history(result.case_id, plan['case_version'], plan['run_id']))
            after = self.get(f'testcases/{result.case_id}')
            if not found or definition_fingerprint(after) != plan['definition_hash']:
                raise ResultOutcomeUnknown()
            return found
        except Exception as exc:
            raise ResultOutcomeUnknown() from exc


def client_for_repository(db, repository) -> AutomationZentaoClient:
    binding = db.query(UserZentaoBinding).filter_by(user_id=repository.owner_id).first()
    if not binding or normalize_base_url(binding.base_url) != repository.zentao_base_url:
        raise AppError('请先绑定该仓库对应的禅道账号', status_code=422)
    password = decrypt_password(binding.zentao_password_ciphertext or '')
    if not password:
        raise AppError('无法解密禅道绑定，请重新保存账号密码', status_code=422)
    return AutomationZentaoClient(repository.zentao_base_url, binding.zentao_account, password)
