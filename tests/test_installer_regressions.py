from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from conftest import FIXTURE, REPO, run_aicj

sys.path.insert(0, str(REPO / "installer"))
from lib import engine, hud  # noqa: E402
from lib.util import UserError  # noqa: E402


def _manifest(home: Path) -> dict:
    return json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))


def test_settings_with_utf8_bom_installs_and_uninstalls(home: Path) -> None:
    settings = home / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b"\xef\xbb\xbf" + b'{"theme": "dark"}\n')
    result = run_aicj("install", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not settings.read_bytes().startswith(b"\xef\xbb\xbf")
    result = run_aicj("uninstall", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(settings.read_text(encoding="utf-8-sig")) == {"theme": "dark"}


def test_crlf_claude_md_keeps_crlf_and_restores_exactly(home: Path) -> None:
    claude_md = home / ".claude/CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    original = b"line1\r\nline2\r\n"
    claude_md.write_bytes(original)
    assert run_aicj("install", home=home).returncode == 0
    data = claude_md.read_bytes()
    assert data.startswith(original) and b"aicj:begin" in data
    assert data.count(b"\n") == data.count(b"\r\n")
    again = run_aicj("install", home=home)
    assert again.returncode == 0
    assert claude_md.read_bytes() == data
    assert "edited" not in again.stdout + again.stderr
    gone = run_aicj("uninstall", home=home)
    assert gone.returncode == 0
    assert "edited" not in gone.stdout + gone.stderr
    assert claude_md.read_bytes() == original


def test_first_backup_survives_hook_change_on_reinstall(home: Path, tmp_path: Path) -> None:
    settings = home / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    original = b'{"theme": "dark"}\n'
    settings.write_bytes(original)
    source = tmp_path / "component"
    shutil.copytree(FIXTURE, source)
    assert run_aicj("install", home=home, source=source).returncode == 0
    backup_rel = next(e["backup"] for e in _manifest(home)["entries"] if e["path"] == ".claude/settings.json")
    assert backup_rel and (home / backup_rel).read_bytes() == original

    manifest_path = source / "core/manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["hooks"][0]["matcher"] = "Edit"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    result = run_aicj("install", home=home, source=source)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"Edit"' in settings.read_text(encoding="utf-8")
    assert (home / backup_rel).read_bytes() == original
    assert next(e["backup"] for e in _manifest(home)["entries"] if e["path"] == ".claude/settings.json") == backup_rel
    assert run_aicj("uninstall", home=home, source=source).returncode == 0
    assert json.loads(settings.read_text(encoding="utf-8")) == {"theme": "dark"}
    assert not (home / backup_rel).exists()


def test_uninstall_validation_failure_does_not_touch_hud(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_aicj("install", home=home).returncode == 0
    (home / ".claude/settings.json").write_text("{not json", encoding="utf-8")
    calls: list[object] = []
    monkeypatch.setattr(hud, "uninstall", lambda *a, **k: calls.append(a))
    with pytest.raises(UserError):
        engine._run_uninstall(home, False)
    assert calls == []
