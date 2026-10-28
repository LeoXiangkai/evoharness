from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import FIXTURE, REPO, files_under

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from installer.lib import doctor, engine, index, manifest, settings, util


def options(**kwargs):
    return manifest.Options(packs=['java'], **kwargs)


def test_symlink_failure_copies_tracks_and_cleans(home, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('symlink privilege unavailable')
    monkeypatch.setattr(Path, 'symlink_to', unavailable)
    report, installed = engine.run_install(home, FIXTURE, options(link=True, codex=True), False)
    rule = home / '.claude/rules/core-sample.md'
    skill = home / '.agents/skills/sample-skill'
    assert rule.read_bytes() == (FIXTURE / 'core/rules/core-sample.md').read_bytes()
    assert not rule.is_symlink() and not skill.is_symlink()
    assert (skill / 'SKILL.md').read_bytes() == (home / '.claude/skills/sample-skill/SKILL.md').read_bytes()
    entries = {e.path: e for e in installed.entries}
    assert entries['.claude/rules/core-sample.md'].kind == 'file'
    assert entries['.agents/skills/sample-skill'].kind == 'skill-copy'
    assert report.warnings and any('复制' in warning for warning in report.warnings)
    assert doctor.run_doctor(home, FIXTURE)[1] == 0
    engine.run_install(home, FIXTURE, options(link=True, codex=True), False)
    (skill / 'SKILL.md').write_text('user edit', encoding='utf-8')
    checks, _ = doctor.run_doctor(home, FIXTURE)
    assert ('WARN', '.agents/skills/sample-skill', 'modified after install') in checks
    engine.run_uninstall(home, False)
    assert (skill / 'SKILL.md').read_text(encoding='utf-8') == 'user edit'
    assert not rule.exists()


def test_symlink_file_exists_error_is_not_copied(home, monkeypatch):
    def already_exists(*args, **kwargs):
        raise FileExistsError('target exists')
    monkeypatch.setattr(Path, 'symlink_to', already_exists)
    with pytest.raises(util.UserError, match='target exists'):
        engine.run_install(home, FIXTURE, options(link=True), False)


def test_windows_launchers_strict_and_codex_skip(home, monkeypatch):
    monkeypatch.setattr(engine, 'is_windows', lambda: True)
    monkeypatch.setattr(doctor, 'is_windows', lambda: True)
    report, installed = engine.run_install(home, REPO, options(codex=True, codex_hooks=True, strict=True), False)
    launchers = [e for e in installed.entries if e.path.endswith('.cmd')]
    assert launchers and all((home / e.path).read_bytes() == f'@"{os.path.abspath(sys.executable)}" "%~dp0{Path(e.path).stem}" %*\r\n'.encode('utf-8') for e in launchers)
    assert json.loads((home / '.claude/aicj/config.json').read_text(encoding='utf-8')) == {'hook_mode': 'block', 'executor_mode': 'cc'}
    hooks = json.loads((home / '.claude/settings.json').read_text(encoding='utf-8'))['hooks']
    assert all(Path(h['command']).is_absolute() and h['command'] == os.path.abspath(sys.executable) and h['args'] for groups in hooks.values() for g in groups for h in g['hooks'])
    assert not (home / '.codex/hooks.json').exists()
    assert any('Codex hooks' in note for note in report.notes)
    assert ('SKIPPED', 'codex hooks', 'Windows behavior not confirmed; hooks were not merged') in doctor.run_doctor(home, FIXTURE)[0]
    engine.run_uninstall(home, False)
    assert files_under(home) == []
    monkeypatch.setattr(engine, 'is_windows', lambda: False)
    engine.run_install(home, FIXTURE, options(), False)
    assert not list(home.rglob('*.cmd'))
    assert json.loads((home / '.claude/aicj/config.json').read_text(encoding='utf-8')) == {'hook_mode': 'warn', 'executor_mode': 'cc'}
    engine.run_uninstall(home, False)
    assert files_under(home) == []


def test_generated_config_and_launcher_preserve_user_conflicts(home, monkeypatch):
    monkeypatch.setattr(engine, 'is_windows', lambda: True)
    config = home / '.claude/aicj/config.json'
    config.parent.mkdir(parents=True)
    config.write_text('{"hook_mode":"warn","mine":true}\n', encoding='utf-8')
    launcher = home / '.claude/bin/git-safe.cmd'
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(b'@echo user\r\n')
    before = {p: p.read_bytes() for p in (config, launcher)}
    engine.run_install(home, REPO, options(strict=True), False)
    assert all(p.read_bytes() == contents for p, contents in before.items())
    engine.run_uninstall(home, False)
    assert all(p.read_bytes() == contents for p, contents in before.items())
    engine.run_install(home, REPO, options(strict=True, on_conflict='backup'), False)
    assert json.loads(config.read_text(encoding='utf-8')) == {'hook_mode': 'block', 'executor_mode': 'cc'}
    engine.run_uninstall(home, False)
    assert all(p.read_bytes() == contents for p, contents in before.items())


@pytest.mark.parametrize('bad', ['C:', 'C:\\x', '\\\\server\\share', '..\\escape', '.claude\\..\\escape', '/absolute', '   '])
def test_windows_unsafe_paths_rejected_on_every_platform(bad):
    with pytest.raises(util.UserError):
        manifest.safe_rel(bad, 'entry')
    with pytest.raises(util.UserError):
        index._target(bad, Path('component.json'))
    assert manifest.safe_rel('.claude\\hooks\\aicj\\safe.py', 'entry') == '.claude/hooks/aicj/safe.py'
    assert index._target('hooks\\aicj\\safe.py', Path('component.json')) == '.claude/hooks/aicj/safe.py'


def test_exec_hook_identity_includes_args_and_preserves_user_hooks():
    command = 'C:\\Python\\python.exe'
    key = settings.HookKey('PreToolUse', 'Bash', command, ('C:\\Home\\.claude\\hooks\\aicj\\safe.py',))
    other = settings.HookKey('PreToolUse', 'Bash', command, ('C:\\Home\\mine.py',))
    payload = {'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': other.command, 'args': list(other.args)}]}]}}
    original = copy.deepcopy(payload)
    assert settings.merge_hooks(payload, [key]) == [key]
    assert settings.has_hook(payload, key) and settings.has_hook(payload, other)
    assert settings.merge_hooks(payload, [key]) == []
    equivalent = settings.HookKey(key.event, key.matcher, 'c:/python/python.exe', ('c:/home/.claude/hooks/aicj/safe.py',))
    assert settings.has_hook(payload, equivalent)
    assert settings.unmerge_hooks(payload, [other, equivalent]) == [equivalent]
    assert payload == original
    manifest.Manifest.from_dict({**manifest.Manifest().to_dict(), 'settings_hooks': [key.to_dict('.claude/settings.json')]})
    with pytest.raises(util.UserError):
        manifest.Manifest.from_dict({**manifest.Manifest().to_dict(), 'settings_hooks': [other.to_dict('.claude/settings.json')]})


def test_foreign_hook_script_path_is_not_owned():
    key = settings.HookKey('PreToolUse', 'Bash', '/usr/bin/python3', ('/home/me/.claude/hooks/aicj/push-review-gate.py',))
    foreign_shell = {'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
        {'type': 'command', 'command': 'python3 /srv/other/.claude/hooks/aicj/push-review-gate.py'}
    ]}]}}
    assert not settings.has_hook(foreign_shell, key)
    assert settings.merge_hooks(foreign_shell, [key]) == [key]
    assert sum(len(group['hooks']) for group in foreign_shell['hooks']['PreToolUse']) == 2


def test_legacy_shell_hook_with_drive_path_matches_case_insensitively():
    key = settings.HookKey('PreToolUse', 'Bash', 'C:/Python/python.exe', ('C:\\Users\\Me\\.claude\\hooks\\aicj\\sample-hook.py',))
    payload = {'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
        {'type': 'command', 'command': "python3 'c:/users/me/.claude/hooks/aicj/sample-hook.py'"}
    ]}]}}
    assert settings.has_hook(payload, key)


def test_doctor_matches_hook_by_script_path_when_interpreter_differs(home, tmp_path):
    source = tmp_path / 'component'
    import shutil
    shutil.copytree(FIXTURE, source)
    component = json.loads((source / 'core/manifest.json').read_text(encoding='utf-8'))
    engine.run_install(home, source, options(), False)
    component['hooks'][0]['requires'] = ['missing-required-file']
    (source / 'core/manifest.json').write_text(json.dumps(component), encoding='utf-8')
    manifest_path = home / '.claude/aicj/manifest.json'
    data = json.loads(manifest_path.read_text(encoding='utf-8'))
    for row in data['settings_hooks']:
        row['command'] = '/opt/another/python'
    manifest_path.write_text(json.dumps(data), encoding='utf-8')
    settings_path = home / '.claude/settings.json'
    payload = json.loads(settings_path.read_text(encoding='utf-8'))
    for groups in payload['hooks'].values():
        for group in groups:
            for hook in group['hooks']:
                hook['command'] = '/opt/another/python'
    settings_path.write_text(json.dumps(payload), encoding='utf-8')
    checks, status = doctor.run_doctor(home, source)
    assert status == 0
    assert not any(name == '.claude/settings.json' and state == 'MISSING' for state, name, _detail in checks)
    assert not any(name == 'hook sample-hook requirements' for _state, name, _detail in checks)


def test_linked_strict_hook_reads_installed_config(home):
    if os.name == 'nt':
        pytest.skip('Windows platform does not provide POSIX symlink execution semantics for this fixture')
    result = subprocess.run([sys.executable, str(REPO / 'installer/aicj.py'), 'install', '--link', '--strict', '--home', str(home), '--source', str(FIXTURE)], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stdout + result.stderr
    hook = json.loads((home / '.claude/settings.json').read_text(encoding='utf-8'))['hooks']['PreToolUse'][0]['hooks'][0]
    env = {k: v for k, v in os.environ.items() if k not in {'AICJ_HOOK_MODE', 'AICJ_CLAUDE_DIR'}}
    run = subprocess.run([hook['command'], *hook['args']], input='{}', capture_output=True, text=True, encoding='utf-8', env=env)
    assert run.returncode == 2


def test_reinstall_replaces_recorded_legacy_shell_hook_but_keeps_user_hook(home):
    import installer.lib.engine as installer_engine
    installer_engine.run_install(home, FIXTURE, options(), False)
    settings_path = home / '.claude/settings.json'
    payload = json.loads(settings_path.read_text(encoding='utf-8'))
    payload['hooks']['PreToolUse'].append({'matcher': 'Bash', 'hooks': [{'type': 'command', 'command': 'echo user'}]})
    legacy = f"python3 {home / '.claude/hooks/aicj/sample-hook.py'}"
    payload['hooks']['PreToolUse'][0]['hooks'][0] = {'type': 'command', 'command': legacy}
    settings_path.write_text(json.dumps(payload), encoding='utf-8')
    manifest_path = home / '.claude/aicj/manifest.json'
    data = json.loads(manifest_path.read_text(encoding='utf-8'))
    for row in data['settings_hooks']:
        row['command'] = legacy
        row.pop('args', None)
    manifest_path.write_text(json.dumps(data), encoding='utf-8')
    installer_engine.run_install(home, FIXTURE, options(), False)
    payload = json.loads(settings_path.read_text(encoding='utf-8'))
    hooks = [hook for group in payload['hooks']['PreToolUse'] for hook in group['hooks']]
    assert any(hook.get('command') == 'echo user' for hook in hooks)
    assert any(hook.get('command') == os.path.abspath(sys.executable) and hook.get('args') for hook in hooks)
    assert not any(hook.get('command') == legacy for hook in hooks)
    installer_engine.run_uninstall(home, False)
    payload = json.loads(settings_path.read_text(encoding='utf-8'))
    assert any(hook.get('command') == 'echo user' for group in payload['hooks']['PreToolUse'] for hook in group['hooks'])


def test_permission_retry_writes_utf8_and_propagates_exhaustion(tmp_path, monkeypatch):
    target = tmp_path / 'config.json'
    target.write_text('baseline', encoding='utf-8')
    replace = os.replace
    count = 0
    def busy_then_replace(src, dst):
        nonlocal count
        count += 1
        if count < 3:
            raise PermissionError('busy')
        replace(src, dst)
    monkeypatch.setattr(util, 'is_windows', lambda: True)
    monkeypatch.setattr(util.os, 'replace', busy_then_replace)
    monkeypatch.setattr(util.time, 'sleep', lambda _: None)
    util.atomic_write_text(target, '中文')
    assert target.read_bytes() == '中文'.encode('utf-8')
    def busy(*args):
        raise PermissionError('busy')
    monkeypatch.setattr(util.os, 'replace', busy)
    with pytest.raises(PermissionError):
        util.atomic_write_text(target, 'overwrite')
    assert target.read_bytes() == '中文'.encode('utf-8')
    assert sorted(p.name for p in tmp_path.iterdir()) == ['config.json']


def test_windows_script_passes_team_config_through() -> None:
    text = (REPO / "scripts/windows/install.ps1").read_bytes().decode("utf-8-sig")
    assert "[string]$TeamConfig" in text
    assert '$installArgs += "--team-config"; $installArgs += $TeamConfig' in text
    assert text.index('"--team-config"') > text.index('$installArgs = @("install")')
