from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from adapters.adapter_helpers import fake_command
from conftest import AICJ, REPO


FIXTURE = REPO / "tests/fixtures/fake_claude_hud"
NODE = shutil.which("node")


def run_aicj(*args: str, home: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process_env = dict(os.environ)
    process_env.pop("AICJ_SKIP_HUD", None)
    process_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(AICJ), *args, "--home", str(home), "--source", str(REPO)],
        cwd=REPO,
        env=process_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def fake_claude(directory: Path, fixture: Path) -> Path:
    return fake_command(
        directory,
        "claude",
        """
import json, os, shutil, sys
args = sys.argv[1:]
log = os.environ['FAKE_CLAUDE_LOG']
with open(log, 'a', encoding='utf-8') as handle:
    handle.write(json.dumps(args) + '\\n')
claude = os.environ['CLAUDE_CONFIG_DIR']
plugins = claude + '/plugins'
os.makedirs(plugins, exist_ok=True)
if args[:4] == ['plugin', 'marketplace', 'add', 'jarrodwatts/claude-hud']:
    data = {}
    path = os.path.join(plugins, 'known_marketplaces.json')
    if os.path.exists(path):
        data = json.load(open(path, encoding='utf-8'))
    data['claude-hud'] = {'source': {'repo': 'jarrodwatts/claude-hud'}}
    json.dump(data, open(path, 'w', encoding='utf-8'))
elif args[:3] == ['plugin', 'install', 'claude-hud@claude-hud']:
    data = {'claude-hud@claude-hud': {'scope': 'user'}}
    json.dump(data, open(os.path.join(plugins, 'installed_plugins.json'), 'w', encoding='utf-8'))
    cache = os.path.join(plugins, 'cache', 'claude-hud', 'claude-hud', os.environ.get('FAKE_HUD_VERSION', '1.0.0'))
    shutil.copytree(os.environ['FAKE_HUD_FIXTURE'], cache)
elif args[:3] == ['plugin', 'uninstall', 'claude-hud@claude-hud']:
    path = os.path.join(plugins, 'installed_plugins.json')
    if os.path.exists(path): os.unlink(path)
elif args[:4] == ['plugin', 'marketplace', 'remove', 'claude-hud']:
    path = os.path.join(plugins, 'known_marketplaces.json')
    if os.path.exists(path): os.unlink(path)
""",
    )


@pytest.mark.skipif(NODE is None, reason="需要 Node.js 执行 fake claude-hud setup.mjs")
def test_default_install_configures_hud_and_selftest_passes(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "bin"
    log = tmp_path / "claude.log"
    fake_claude(fake_bin, FIXTURE)
    env = {
        "PATH": os.pathsep.join((str(fake_bin), os.path.dirname(NODE or ""), os.environ.get("PATH", ""))),
        "FAKE_CLAUDE_LOG": str(log),
        "FAKE_HUD_FIXTURE": str(FIXTURE),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
    }
    result = run_aicj("install", home=home, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    settings = json.loads((home / ".claude/settings.json").read_text(encoding="utf-8"))
    assert "claude-hud" in settings["statusLine"]["command"]
    config = json.loads((home / ".claude/plugins/claude-hud/config.json").read_text(encoding="utf-8"))
    assert config == {
        "language": "zh",
        "lineLayout": "expanded",
        "showSeparators": False,
        "pathLevels": 1,
        "gitStatus": {"enabled": True, "showDirty": True, "showAheadBehind": False, "showFileStats": False},
        "display": {
            "showModel": True, "showProject": True, "showAddedDirs": False,
            "showContextBar": True, "contextValue": "percent", "showTokenBreakdown": False,
            "showUsage": False, "showConfigCounts": False, "showCost": False,
            "showDuration": False, "showSpeed": False, "showTools": False,
            "showSkills": False, "showMcp": False, "showAgents": False,
            "showTodos": False, "showSessionName": False, "showEffortLevel": False,
        },
    }
    selftest = run_aicj("selftest", home=home, env=env)
    assert selftest.returncode == 0, selftest.stdout + selftest.stderr
    assert "PASS  claude-hud" in selftest.stdout
    doctor = run_aicj("doctor", home=home, env=env)
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "PASS     claude-hud" in doctor.stdout
    status = run_aicj("status", home=home, env=env)
    assert status.returncode == 0, status.stdout + status.stderr
    assert "installed        claude-hud" in status.stdout
    uninstall = run_aicj("uninstall", home=home, env=env)
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    assert not (home / ".claude/plugins/claude-hud/config.json").exists()
    settings_after = home / ".claude/settings.json"
    assert not settings_after.exists() or "statusLine" not in json.loads(settings_after.read_text(encoding="utf-8"))


def _hud_file_paths(loaded: dict) -> set[str]:
    return {e["path"] for e in loaded["entries"] if e["kind"] == "hud-file"}


@pytest.mark.skipif(NODE is None, reason="需要 Node.js 执行 fake claude-hud setup.mjs")
def test_hud_reinstall_is_idempotent_and_no_hud_skips(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "bin"
    log = tmp_path / "claude.log"
    fake_claude(fake_bin, FIXTURE)
    env = {
        "PATH": os.pathsep.join((str(fake_bin), os.path.dirname(NODE or ""), os.environ.get("PATH", ""))),
        "FAKE_CLAUDE_LOG": str(log),
        "FAKE_HUD_FIXTURE": str(FIXTURE),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
    }
    first = run_aicj("install", home=home, env=env)
    assert first.returncode == 0
    config_path = home / ".claude/plugins/claude-hud/config.json"
    config_path.write_text(json.dumps({"user": True}) + "\n", encoding="utf-8")
    first_manifest = json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))
    before = first_manifest["hud"]
    hud_files = _hud_file_paths(first_manifest)
    assert ".claude/plugins/installed_plugins.json" in hud_files
    second = run_aicj("install", home=home, env=env)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "no longer shipped" not in second.stdout
    second_manifest = json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))
    assert _hud_file_paths(second_manifest) == hud_files
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert sum(call[:3] == ["plugin", "install", "claude-hud@claude-hud"] for call in calls) == 1
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"user": True}
    after = json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))["hud"]
    for key in ("plugin_by_aicj", "marketplace_by_aicj", "statusline_by_aicj", "config_by_aicj"):
        assert after[key] is True
    assert before["config_sha256"] == after["config_sha256"]
    uninstall = run_aicj("uninstall", home=home, env=env)
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    assert config_path.is_file()


@pytest.mark.skipif(NODE is None, reason="需要 Node.js 执行 fake claude-hud setup.mjs")
def test_existing_other_statusline_is_kept_and_uninstall_preserves_changed_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    settings_path = home / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"statusLine": {"type": "command", "command": "other-status"}}) + "\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    log = tmp_path / "claude.log"
    fake_claude(fake_bin, FIXTURE)
    env = {
        "PATH": os.pathsep.join((str(fake_bin), os.path.dirname(NODE or ""), os.environ.get("PATH", ""))),
        "FAKE_CLAUDE_LOG": str(log),
        "FAKE_HUD_FIXTURE": str(FIXTURE),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
    }
    result = run_aicj("install", home=home, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(settings_path.read_text(encoding="utf-8"))["statusLine"]["command"] == "other-status"
    assert "kept-other" in json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))["hud"]["status"]
    selftest = run_aicj("selftest", home=home, env=env)
    assert "SKIP  claude-hud" in selftest.stdout
    config_path = home / ".claude/plugins/claude-hud/config.json"
    config_path.write_text('{"user": true}\n', encoding="utf-8")
    uninstall = run_aicj("uninstall", home=home, env=env)
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    assert json.loads(settings_path.read_text(encoding="utf-8"))["statusLine"]["command"] == "other-status"
    assert config_path.is_file()
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert ["plugin", "uninstall", "claude-hud@claude-hud"] in calls


@pytest.mark.skipif(NODE is None, reason="需要 Node.js 执行 fake claude-hud setup.mjs")
def test_hud_selects_numeric_highest_version(tmp_path: Path) -> None:
    from installer.lib import hud

    root = tmp_path / ".claude/plugins/cache/claude-hud/claude-hud"
    for version in ("0.9.9", "0.10.0"):
        (root / version).mkdir(parents=True)
    assert hud.plugin_dir(tmp_path / ".claude").name == "0.10.0"


def test_missing_claude_skips_without_changing_install_exit(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    result = run_aicj("install", home=home, env={"PATH": os.path.dirname(sys.executable)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "未找到 claude 命令" in result.stdout
    assert json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))["hud"]["status"] == "skipped"


def test_no_hud_does_not_call_claude(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "bin"
    log = tmp_path / "claude.log"
    fake_claude(fake_bin, FIXTURE)
    env = {"PATH": str(fake_bin), "FAKE_CLAUDE_LOG": str(log), "FAKE_HUD_FIXTURE": str(FIXTURE)}
    result = run_aicj("install", "--no-hud", home=home, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    selftest = run_aicj("selftest", home=home, env=env)
    assert "N/A   claude-hud" in selftest.stdout
    assert not log.exists()


@pytest.mark.skipif(NODE is None, reason="需要 Node.js 执行 fake claude-hud setup.mjs")
def test_hud_dry_run_does_not_call_claude_or_write_files(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    fake_bin = tmp_path / "bin"
    log = tmp_path / "claude.log"
    fake_claude(fake_bin, FIXTURE)
    env = {
        "PATH": os.pathsep.join((str(fake_bin), os.path.dirname(NODE or ""), os.environ.get("PATH", ""))),
        "FAKE_CLAUDE_LOG": str(log),
        "FAKE_HUD_FIXTURE": str(FIXTURE),
    }
    result = run_aicj("install", "--dry-run", home=home, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry-run" in result.stdout
    assert not log.exists()
    assert not any(home.rglob("*"))


def test_windows_install_mentions_hud_flag_and_bom() -> None:
    path = REPO / "scripts/windows/install.ps1"
    data = path.read_bytes()
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    assert "NoHud" in text
    assert "--no-hud" in text
    assert "OpenJS.NodeJS.LTS" in text


@pytest.mark.skipif(NODE is None, reason="需要 Node.js 执行 fake claude-hud setup.mjs")
def test_user_owned_hud_statusline_survives_uninstall(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    settings_path = home / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    user_line = {"type": "command", "command": "node /opt/user/claude-hud/statusline.mjs"}
    settings_path.write_text(json.dumps({"statusLine": user_line}) + "\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    log = tmp_path / "claude.log"
    fake_claude(fake_bin, FIXTURE)
    env = {
        "PATH": os.pathsep.join((str(fake_bin), os.path.dirname(NODE or ""), os.environ.get("PATH", ""))),
        "FAKE_CLAUDE_LOG": str(log),
        "FAKE_HUD_FIXTURE": str(FIXTURE),
        "CLAUDE_CONFIG_DIR": str(home / ".claude"),
    }
    result = run_aicj("install", home=home, env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(settings_path.read_text(encoding="utf-8"))["statusLine"] == user_line
    record = json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))["hud"]
    assert record["statusline_by_aicj"] is False
    uninstall = run_aicj("uninstall", home=home, env=env)
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    assert json.loads(settings_path.read_text(encoding="utf-8"))["statusLine"] == user_line
