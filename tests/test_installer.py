from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from conftest import FIXTURE, files_under, run_aicj, snapshot


def manifest_of(home: Path) -> dict:
    return json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))


def actions_of(home: Path) -> dict[str, str]:
    return {entry["path"]: entry["action"] for entry in manifest_of(home)["entries"]}


def test_install_uninstall_round_trip_leaves_no_residue(home: Path) -> None:
    result = run_aicj("install", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".claude/rules/core-sample.md").is_file()
    assert (home / ".claude/skills/sample-skill/SKILL.md").is_file()
    assert (home / ".claude/hooks/aicj/sample-hook.py").is_file()
    assert (home / ".claude/rules/pack-sample.md").is_file()
    assert (home / ".claude/settings.json").is_file()
    assert (home / ".claude/aicj/manifest.json").is_file()

    result = run_aicj("uninstall", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert files_under(home) == []


def test_existing_same_content_is_adopted(home: Path) -> None:
    target = home / ".claude/rules/core-sample.md"
    target.parent.mkdir(parents=True)
    target.write_bytes((FIXTURE / "core/rules/core-sample.md").read_bytes())
    result = run_aicj("install", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert actions_of(home)[".claude/rules/core-sample.md"] == "adopted"

    assert run_aicj("uninstall", home=home).returncode == 0
    assert target.is_file()


def test_reinstall_is_idempotent_and_uninstall_still_cleans(home: Path) -> None:
    assert run_aicj("install", home=home).returncode == 0
    before = snapshot(home, ignore=(".claude/aicj/manifest.json",))
    result = run_aicj("install", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(home, ignore=(".claude/aicj/manifest.json",)) == before
    assert actions_of(home)[".claude/rules/core-sample.md"] == "created"

    assert run_aicj("uninstall", home=home).returncode == 0
    assert files_under(home) == []


def test_conflict_default_skips_and_keeps_user_content(home: Path) -> None:
    target = home / ".claude/rules/core-sample.md"
    target.parent.mkdir(parents=True)
    target.write_text("user edit\n", encoding="utf-8")
    result = run_aicj("install", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_text(encoding="utf-8") == "user edit\n"
    assert actions_of(home)[".claude/rules/core-sample.md"] == "skipped"
    assert "skipped" in result.stdout

    assert run_aicj("uninstall", home=home).returncode == 0
    assert target.read_text(encoding="utf-8") == "user edit\n"


def test_on_conflict_backup_replaces_and_uninstall_restores(home: Path) -> None:
    assert run_aicj("install", home=home).returncode == 0
    target = home / ".claude/rules/core-sample.md"
    target.write_text("user edit\n", encoding="utf-8")
    result = run_aicj("install", "--on-conflict", "backup", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert actions_of(home)[".claude/rules/core-sample.md"] == "replaced"
    assert target.read_text(encoding="utf-8") == (FIXTURE / "core/rules/core-sample.md").read_text(encoding="utf-8")
    backups = list((home / ".claude/rules").glob("core-sample.md.aicj-bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "user edit\n"

    result = run_aicj("uninstall", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_text(encoding="utf-8") == "user edit\n"


def test_uninstall_keeps_files_the_user_edited(home: Path) -> None:
    assert run_aicj("install", home=home).returncode == 0
    target = home / ".claude/rules/core-sample.md"
    target.write_text("user edit\n", encoding="utf-8")
    result = run_aicj("uninstall", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "user edit\n"
    assert "modified after install" in result.stdout


def test_symlink_to_source_is_adopted_symlink(home: Path) -> None:
    source = FIXTURE / "core/rules/core-sample.md"
    target = home / ".claude/rules/core-sample.md"
    target.parent.mkdir(parents=True)
    target.symlink_to(source)
    result = run_aicj("install", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert actions_of(home)[".claude/rules/core-sample.md"] == "adopted-symlink"


def test_link_install_writes_symlinks_and_is_idempotent(home: Path) -> None:
    result = run_aicj("install", "--link", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    target = home / ".claude/rules/core-sample.md"
    assert target.is_symlink()
    assert target.resolve() == (FIXTURE / "core/rules/core-sample.md").resolve()
    assert manifest_of(home)["options"]["link"] is True

    result = run_aicj("install", "--link", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert actions_of(home)[".claude/rules/core-sample.md"] == "created"
    assert "adopted-symlink  .claude/rules/core-sample.md" in result.stdout

    assert run_aicj("uninstall", home=home).returncode == 0
    assert files_under(home) == []
    assert (FIXTURE / "core/rules/core-sample.md").is_file()


def test_settings_merge_keeps_user_entries_and_is_idempotent(home: Path) -> None:
    settings_path = home / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": {"A": "1"},
                "permissions": {"allow": ["Bash(ls)"]},
                "hooks": {
                    "PreToolUse": [
                        {"matcher": "Edit", "hooks": [{"type": "command", "command": "echo user"}]}
                    ]
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_aicj("install", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    merged = json.loads(settings_path.read_text(encoding="utf-8"))
    assert merged["env"] == {"A": "1"}
    assert merged["permissions"] == {"allow": ["Bash(ls)"]}
    groups = merged["hooks"]["PreToolUse"]
    assert groups[0] == {"matcher": "Edit", "hooks": [{"type": "command", "command": "echo user"}]}
    assert groups[1]["matcher"] == "Bash"
    assert groups[1]["hooks"][0]["command"] == os.path.abspath(sys.executable)
    assert "/hooks/aicj/" in groups[1]["hooks"][0]["args"][0].replace("\\", "/")

    frozen = settings_path.read_bytes()
    result = run_aicj("install", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert settings_path.read_bytes() == frozen

    result = run_aicj("uninstall", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    restored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert restored["env"] == {"A": "1"}
    assert restored["permissions"] == {"allow": ["Bash(ls)"]}
    assert restored["hooks"]["PreToolUse"] == [
        {"matcher": "Edit", "hooks": [{"type": "command", "command": "echo user"}]}
    ]


def test_settings_uninstall_clears_empty_groups(home: Path) -> None:
    settings_path = home / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"env": {"A": "1"}}) + "\n", encoding="utf-8")

    assert run_aicj("install", home=home).returncode == 0
    assert "hooks" in json.loads(settings_path.read_text(encoding="utf-8"))
    assert run_aicj("uninstall", home=home).returncode == 0
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {"env": {"A": "1"}}


def test_settings_invalid_json_is_error_and_untouched(home: Path) -> None:
    settings_path = home / ".claude/settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not json", encoding="utf-8")
    before = settings_path.read_bytes()

    result = run_aicj("install", home=home)
    assert result.returncode == 2
    assert settings_path.read_bytes() == before
    assert files_under(home) == [".claude/settings.json"]


def test_claude_md_block_is_idempotent_and_removed_on_uninstall(home: Path) -> None:
    claude_md = home / ".claude/CLAUDE.md"
    claude_md.parent.mkdir(parents=True)
    claude_md.write_text("# user entry\n", encoding="utf-8")

    assert run_aicj("install", home=home).returncode == 0
    first = claude_md.read_text(encoding="utf-8")
    assert first.startswith("# user entry\n")
    assert first.count("<!-- aicj:begin -->") == 1
    assert "@aicj/CLAUDE.global.md" in first

    assert run_aicj("install", home=home).returncode == 0
    assert claude_md.read_text(encoding="utf-8") == first

    assert run_aicj("uninstall", home=home).returncode == 0
    assert claude_md.read_text(encoding="utf-8") == "# user entry\n"


def test_dry_run_install_and_uninstall_write_nothing(home: Path) -> None:
    before = snapshot(home)
    result = run_aicj("install", "--dry-run", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(home) == before
    assert before == {}

    assert run_aicj("install", home=home).returncode == 0
    installed = snapshot(home)
    result = run_aicj("uninstall", "--dry-run", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(home) == installed


def test_install_rejects_abbreviated_home_option(tmp_path: Path) -> None:
    result = run_aicj("install", "--hom", str(tmp_path / "home"), home=tmp_path / "ignored")
    assert result.returncode == 2
    assert "usage:" in result.stderr


def test_status_and_doctor_after_install(home: Path) -> None:
    assert run_aicj("install", home=home).returncode == 0
    status = run_aicj("status", home=home)
    assert status.returncode == 0, status.stdout + status.stderr
    assert "installed" in status.stdout
    assert ".claude/rules/core-sample.md" in status.stdout

    doctor = run_aicj("doctor", home=home)
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "MISSING" not in doctor.stdout
    assert "PASS" in doctor.stdout


def test_doctor_reports_missing_install(home: Path) -> None:
    doctor = run_aicj("doctor", home=home)
    assert doctor.returncode == 1
    assert "MISSING" in doctor.stdout


def test_doctor_prints_next_steps_only_when_healthy(home: Path) -> None:
    assert run_aicj("install", home=home).returncode == 0
    doctor = run_aicj("doctor", home=home)
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    lines = doctor.stdout.splitlines()
    assert "下一步  初始化目标项目：/setup-ai-coding" in lines
    assert "下一步  不知道下一步做什么：/flow" in lines
    assert "下一步  只有粗糙想法：/req-intake" in lines
    summary = [ln for ln in lines if ln.startswith("summary")]
    assert len(summary) == 1
    assert lines.index(summary[0]) < lines.index("下一步  初始化目标项目：/setup-ai-coding")
    assert sum(1 for ln in lines if ln.startswith("下一步")) == 3


def test_doctor_omits_next_steps_when_not_installed(home: Path) -> None:
    doctor = run_aicj("doctor", home=home)
    assert doctor.returncode == 1
    assert "下一步" not in doctor.stdout


def test_doctor_summary_line_format_is_unchanged(home: Path) -> None:
    assert run_aicj("install", home=home).returncode == 0
    before = run_aicj("doctor", home=home)
    assert before.returncode == 0, before.stdout + before.stderr
    summary = [ln for ln in before.stdout.splitlines() if ln.startswith("summary")]
    assert len(summary) == 1
    assert re.fullmatch(
        r"summary          (MISSING|PASS|SKIPPED|WARN)=\d+( (MISSING|PASS|SKIPPED|WARN)=\d+)*",
        summary[0],
    ), summary[0]
    assert summary[0] == "summary          PASS=11 SKIPPED=3 WARN=6"

    (home / ".claude/skills/grilling").mkdir(parents=True)
    after = run_aicj("doctor", home=home)
    assert after.returncode == 0, after.stdout + after.stderr
    assert [ln for ln in after.stdout.splitlines() if ln.startswith("summary")] == summary


def test_doctor_hints_when_optional_grilling_skill_is_absent(home: Path) -> None:
    assert run_aicj("install", home=home).returncode == 0
    missing = run_aicj("doctor", home=home)
    assert missing.returncode == 0, missing.stdout + missing.stderr
    hints = [ln for ln in missing.stdout.splitlines() if ln.startswith("hint")]
    assert len(hints) == 1
    assert "grilling" in hints[0]
    assert "直接问答" in hints[0]
    rows_before = [ln for ln in missing.stdout.splitlines() if ln.split(" ", 1)[0] in {"PASS", "WARN", "MISSING", "SKIPPED"}]

    (home / ".claude/skills/grilling").mkdir(parents=True)
    present = run_aicj("doctor", home=home)
    assert present.returncode == 0, present.stdout + present.stderr
    assert [ln for ln in present.stdout.splitlines() if ln.startswith("hint")] == []
    rows_after = [ln for ln in present.stdout.splitlines() if ln.split(" ", 1)[0] in {"PASS", "WARN", "MISSING", "SKIPPED"}]
    assert rows_after == rows_before


def test_install_option_flags_all_parse(home: Path) -> None:
    result = run_aicj(
        "install",
        "--dry-run",
        "--global",
        "--packs",
        "java,python,vue",
        "--adapters",
        "executor,lesson,jev,verify-probe",
        "--codex",
        "--codex-hooks",
        "--strict",
        "--link",
        "--on-conflict",
        "skip",
        home=home,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(home) == {}


def test_codex_links_skills_and_strict_configures_hook(home: Path) -> None:
    result = run_aicj("install", "--codex", "--codex-hooks", "--strict", "--packs", "java", home=home)
    assert result.returncode == 0, result.stdout + result.stderr

    link = home / ".agents/skills/sample-skill"
    assert link.is_symlink()
    assert link.resolve() == (home / ".claude/skills/sample-skill").resolve()

    merged = json.loads((home / ".claude/settings.json").read_text(encoding="utf-8"))
    command = merged["hooks"]["PreToolUse"][0]["hooks"][0]
    assert command["command"] == os.path.abspath(sys.executable)
    assert "/hooks/aicj/" in command["args"][0].replace("\\", "/")
    assert json.loads((home / ".claude/aicj/config.json").read_text(encoding="utf-8")) == {"hook_mode": "block", "executor_mode": "cc"}

    codex_hooks = home / ".codex/hooks.json"
    if os.name == "nt":
        assert not codex_hooks.exists()
        assert "Windows 上 Codex hooks 暂不合并：Codex hooks 行为未确认" in result.stdout
        doctor = run_aicj("doctor", home=home)
        assert "SKIPPED  codex hooks  Windows behavior not confirmed; hooks were not merged" in doctor.stdout
    else:
        codex = json.loads(codex_hooks.read_text(encoding="utf-8"))
        assert "/hooks/aicj/" in codex["hooks"]["PreToolUse"][0]["hooks"][0]["args"][0].replace("\\", "/")

    assert run_aicj("uninstall", home=home).returncode == 0
    assert not (home / ".agents/skills/sample-skill").is_symlink()


def test_project_scope_is_accepted(home: Path, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    result = run_aicj("install", "--project", str(project), home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "project layer" in result.stdout


def test_uninstall_without_install_is_noop(home: Path) -> None:
    result = run_aicj("uninstall", home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert files_under(home) == []
