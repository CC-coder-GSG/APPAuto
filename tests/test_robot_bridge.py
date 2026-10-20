from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

robot = pytest.importorskip('robot')
spec = importlib.util.spec_from_file_location('omniqa_robot', Path(__file__).parents[1] / 'integrations/robot/omniqa_robot.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


@pytest.fixture
def project(tmp_path):
    tests = tmp_path / 'tests'
    tests.mkdir()
    (tests / '__init__.robot').write_text('*** Settings ***\nSuite Setup    Log    Parent suite setup\n', encoding='utf-8')
    (tests / 'example.robot').write_text('''*** Test Cases ***
Passing
    [Tags]    zentao:41    autoid:sm.pass
    Mapped Step
Failing
    [Tags]    zentao:42
    Fail    Expected assertion failure
Skipped
    [Tags]    zentao:43
    Skip    Not applicable
Unmapped
    Fail    Must never be selected
*** Keywords ***
Mapped Step
    [Tags]    zentao-step:41:541
    Should Be Equal    1    1
''', encoding='utf-8')
    (tests / '_hidden.robot').write_text('*** Test Cases ***\nHidden\n    No Operation\n', encoding='utf-8')
    return tmp_path


def plan(snapshot, ids):
    return {'revision': snapshot['revision'], 'plan': [x for x in snapshot['cases'] if x['case_id'] in ids]}


def test_static_catalog_does_not_execute_failing_tests(project):
    snapshot = bridge.catalog(project, 'main', verify_git=False)
    assert len(snapshot['cases']) == 4
    assert sum(bool(x['case_id']) for x in snapshot['cases']) == 3
    assert all('_hidden' not in x['path'] for x in snapshot['cases'])


def test_precise_tag_selection_keeps_parent_suite_and_ignores_other_tests(project):
    snapshot = bridge.catalog(project, 'main', verify_git=False)
    frozen = plan(snapshot, [41])
    output = project / 'results'
    args = bridge.make_arguments(frozen, snapshot, project, output)
    argfile = project / 'args.txt'
    argfile.write_text(args, encoding='utf-8')
    assert robot.run_cli(['--log', 'NONE', '--report', 'NONE', '-A', str(argfile)], exit=False) == 0
    rows = bridge.parse_results(output / 'output.xml', frozen)
    assert len(rows) == 1 and rows[0]['case_id'] == 41 and rows[0]['status'] == 'PASS'
    assert rows[0]['steps'] == [{'step_id': 541, 'status': 'PASS', 'message': ''}]
    assert 'Parent suite setup' in (output / 'output.xml').read_text(encoding='utf-8')


def test_robot_pass_fail_skip_are_preserved(project):
    snapshot = bridge.catalog(project, 'main', verify_git=False)
    frozen = plan(snapshot, [41, 42, 43])
    output = project / 'results'
    argfile = project / 'args.txt'
    argfile.write_text(bridge.make_arguments(frozen, snapshot, project, output), encoding='utf-8')
    assert robot.run_cli(['--log', 'NONE', '--report', 'NONE', '-A', str(argfile)], exit=False) == 1
    rows = bridge.parse_results(output / 'output.xml', frozen)
    assert {x['case_id']: x['status'] for x in rows} == {41: 'PASS', 42: 'FAIL', 43: 'SKIP'}
    assert all(x['duration_seconds'] >= 0 for x in rows)


def test_parent_suite_setup_failure_is_environment_error(project):
    (project / 'tests/__init__.robot').write_text('*** Settings ***\nSuite Setup    Fail    Appium unavailable\n', encoding='utf-8')
    snapshot = bridge.catalog(project, 'main', verify_git=False)
    frozen = plan(snapshot, [41])
    output = project / 'results'
    robot.run(str(project / 'tests'), include=['zentao:41'], outputdir=str(output), log='NONE', report='NONE')
    rows = bridge.parse_results(output / 'output.xml', frozen)
    assert rows[0]['status'] == 'ERROR'
    assert 'Parent suite setup failed' in rows[0]['message']


def test_empty_selection_or_wrong_revision_never_runs_whole_directory(project):
    snapshot = bridge.catalog(project, 'main', verify_git=False)
    with pytest.raises(ValueError, match='No cases selected'):
        bridge.make_arguments(plan(snapshot, []), snapshot, project, project / 'results')
    frozen = plan(snapshot, [41])
    frozen['revision'] = 'a' * 40
    with pytest.raises(ValueError, match='Git revision'):
        bridge.make_arguments(frozen, snapshot, project, project / 'results')


def test_unexpected_robot_results_cannot_be_reported(project):
    snapshot = bridge.catalog(project, 'main', verify_git=False)
    output = project / 'results'
    robot.run(str(project / 'tests'), include=['zentao:42'], outputdir=str(output), log='NONE', report='NONE')
    with pytest.raises(ValueError, match='unselected'):
        bridge.parse_results(output / 'output.xml', plan(snapshot, [41]))


def test_dry_run_is_explicit_and_does_not_execute_failure_keyword(project):
    snapshot = bridge.catalog(project, 'main', verify_git=False)
    frozen = plan(snapshot, [42]) | {'dry_run': True}
    output = project / 'results'
    argfile = project / 'args.txt'
    argfile.write_text(bridge.make_arguments(frozen, snapshot, project, output), encoding='utf-8')
    assert robot.run_cli(['--log', 'NONE', '--report', 'NONE', '-A', str(argfile)], exit=False) == 0
    assert bridge.parse_results(output / 'output.xml', frozen)[0]['status'] == 'PASS'


def test_revision_uses_configured_git_executable(monkeypatch, tmp_path):
    executable = r'D:\software\Git\bin\git.exe'
    monkeypatch.setenv('OMNIQA_GIT_EXECUTABLE', executable)
    commands = []
    def output(command, **kwargs):
        commands.append(command)
        return 'a' * 40 if 'rev-parse' in command else ''
    monkeypatch.setattr(bridge.subprocess, 'check_output', output)
    assert bridge.revision(tmp_path) == 'a' * 40
    assert all(command[0] == executable for command in commands)


def test_revision_reports_missing_git_without_ambiguous_windows_error(monkeypatch, tmp_path):
    def missing(*args, **kwargs):
        raise FileNotFoundError(2, 'The system cannot find the file specified')
    monkeypatch.setattr(bridge.subprocess, 'check_output', missing)
    with pytest.raises(RuntimeError, match='OMNIQA_GIT_EXECUTABLE'):
        bridge.revision(tmp_path)


def test_revision_works_without_git_on_path_and_rejects_changed_tests(monkeypatch, tmp_path):
    import shutil
    import subprocess
    executable = shutil.which('git')
    if not executable:
        pytest.skip('Git executable is required for this integration check')
    root = tmp_path / 'repository'
    root.mkdir()
    (root / 'tests').mkdir()
    case = root / 'tests/example.robot'
    case.write_text('*** Test Cases ***\nSmoke\n    No Operation\n', encoding='utf-8')
    def git(*args):
        return subprocess.check_output([executable, '-C', str(root), *args], text=True).strip()
    git('init')
    git('add', 'tests/example.robot')
    git('-c', 'user.name=Regression', '-c', 'user.email=regression@example.invalid', 'commit', '-m', 'fixture')
    revision = git('rev-parse', 'HEAD')
    monkeypatch.setenv('OMNIQA_GIT_EXECUTABLE', executable)
    monkeypatch.setenv('PATH', '')
    assert bridge.revision(root) == revision
    case.write_text('Changed', encoding='utf-8')
    with pytest.raises(ValueError, match='differ from the Git commit'):
        bridge.revision(root)
