from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

GitRevision = Annotated[str, Field(pattern=r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')]
PositiveID = Annotated[int, Field(gt=0)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)


class RepositoryCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    git_url: str = Field(min_length=1, max_length=500)
    branch: str = Field(default='main', min_length=1, max_length=160, pattern=r'^[A-Za-z0-9][A-Za-z0-9._/-]*$')
    jenkins_job: str = Field(min_length=1, max_length=255)
    zentao_base_url: str | None = Field(default=None, min_length=1, max_length=500)
    product_id: PositiveID
    execution_id: PositiveID | None = None
    target_build_id: PositiveID | None = None
    default_testtask_id: PositiveID | None = None

    @field_validator('jenkins_job')
    @classmethod
    def job_path(cls, value):
        if any(x in {'', '.', '..'} for x in value.split('/')) or any(x in value for x in '\\?#\r\n'):
            raise ValueError('Invalid Jenkins job name')
        return value

    @field_validator('git_url')
    @classmethod
    def git_location(cls, value):
        if re.fullmatch(r'[A-Za-z0-9._-]+@[A-Za-z0-9._-]+:[A-Za-z0-9._/-]+', value):
            return value
        parts = urlsplit(value)
        if parts.scheme not in {'https', 'ssh'} or not parts.hostname or parts.password or parts.query or parts.fragment:
            raise ValueError('Use an HTTPS or SSH Git URL without embedded credentials')
        if parts.scheme == 'https' and parts.username:
            raise ValueError('Git credentials belong in Jenkins Credentials')
        return value


class CatalogCase(StrictModel):
    case_id: PositiveID | None = None
    automation_id: str = Field(min_length=1, max_length=240)
    name: str = Field(min_length=1, max_length=500)
    path: str = Field(min_length=1, max_length=500)
    tags: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=100)

    @field_validator('path')
    @classmethod
    def script_path(cls, value):
        p = PurePosixPath(value)
        if ('\\' in value or ':' in value or not value.startswith('tests/') or not value.endswith('.robot')
                or p.is_absolute() or any(x in {'.', '..', ''} for x in value.split('/'))
                or any(x.startswith(('_', '.')) for x in p.parts)):
            raise ValueError('Only discoverable tests/*.robot paths are accepted')
        return value


class CatalogUpload(StrictModel):
    revision: GitRevision
    branch: str = Field(min_length=1, max_length=160)
    cases: list[CatalogCase] = Field(max_length=10000)


class RunCreate(StrictModel):
    dry_run: bool = False
    case_ids: list[PositiveID] = Field(min_length=1, max_length=500)
    testtask_id: PositiveID
    device_serial: str = Field(min_length=1, max_length=160, pattern=r'^[A-Za-z0-9._:-]+$')
    apk_reference: str = Field(min_length=1, max_length=500)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    parameters: dict[str, str | bool | int | float] = Field(default_factory=dict, max_length=40)


class BuildStarted(StrictModel):
    job_name: str = Field(min_length=1, max_length=255)
    build_number: PositiveID
    revision: GitRevision
    device_serial: str = Field(min_length=1, max_length=160)


class StepResultUpload(StrictModel):
    step_id: PositiveID
    status: Literal['PASS', 'FAIL', 'SKIP', 'ERROR']
    message: str = Field(default='', max_length=4000)


class CaseResultUpload(StrictModel):
    case_id: PositiveID
    status: Literal['PASS', 'FAIL', 'SKIP', 'ERROR']
    duration_seconds: float = Field(ge=0, le=2592000)
    message: str = Field(default='', max_length=30000)
    started_at: str | None = Field(default=None, max_length=60)
    finished_at: str | None = Field(default=None, max_length=60)
    steps: list[StepResultUpload] = Field(default_factory=list, max_length=200)


class RunResultUpload(BuildStarted):
    dry_run: bool = False
    outcome: Literal['completed', 'environment_error', 'cancelled']
    message: str = Field(default='', max_length=2000)
    apk_sha256: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    results: list[CaseResultUpload] = Field(default_factory=list, max_length=500)


class ExternalRunCreate(RunCreate, BuildStarted):
    source: Literal['jenkins', 'zentao'] = 'jenkins'


class WritebackConfiguration(StrictModel):
    enabled: bool


class ZentaoLaunch(RunCreate):
    zentao_account: str = Field(min_length=1, max_length=120)
