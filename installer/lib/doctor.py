from __future__ import annotations

import os
from pathlib import Path

from . import engine, hud, index, manifest, mdblock, settings, telemetry
from .util import UserError, is_windows

PASS = "PASS"
WARN = "WARN"
MISSING = "MISSING"
SKIPPED = "SKIPPED"

THIRD_PARTY_SKILLS = (
    "grilling",
    "research",
    "prototype",
    "domain-modeling",
    "writing-for-agents",
)

OPTIONAL_SKILL = "grilling"


def _same_hook_script(left: dict, command: str, args: tuple[str, ...]) -> bool:
    expected = settings._script_path(command, args)
    actual = settings._script_path(left.get("command", ""), tuple(left.get("args", []) or []))
    return bool(expected) and actual == expected


def run_doctor(home: Path, source: Path | None = None) -> tuple[list[tuple[str, str, str]], int]:
    checks: list[tuple[str, str, str]] = []
    claude_dir = home / ".claude"
    if not claude_dir.is_dir():
        checks.append((MISSING, "home/.claude", str(claude_dir)))
        return checks, 1
    checks.append((PASS, "home/.claude", str(claude_dir)))

    loaded = _load_manifest(home, checks)
    if loaded is None:
        checks.extend(_third_party(home))
        return checks, 1 if any(status == MISSING for status, _n, _d in checks) else 0

    checks.extend(_entries(home, loaded))
    checks.extend(_plugins(home, loaded, source))
    checks.extend(_md_block(home))
    checks.extend(_hooks(home, loaded))
    checks.extend(_hook_requirements(home, loaded, source))
    checks.extend(_optional_hooks(home, loaded, source))
    checks.extend(_bin_path(home, loaded))
    checks.extend(_codex(home, loaded))
    checks.extend(_third_party(home))
    checks.extend(_hud(home, loaded))
    checks.extend(_telemetry(home, source))

    missing = sum(1 for status, _name, _detail in checks if status == MISSING)
    return checks, 1 if missing else 0


def optional_skill_gap(home: Path) -> str | None:
    """Hint text when an optional skill is absent; never a check row, never changes the return code."""
    skill = home / ".claude" / "skills" / OPTIONAL_SKILL
    if skill.is_dir():
        return None
    return f"可选 skill 缺失：{skill} 未安装，需求澄清降级为直接问答"


def _telemetry(home: Path, source: Path | None) -> list[tuple[str, str, str]]:
    """Read-only: never creates telemetry files. Without team.json a single info row."""
    if not telemetry.has_team(home):
        return []  # not configured: no row, keeps the doctor summary unchanged
    lib = telemetry._lib(home, source)
    if lib is None:
        return [(WARN, "team-telemetry", "已配置 team.json，但采集库缺失；重新安装可恢复")]
    try:
        info = lib.status(home / ".claude")
    except Exception as exc:
        return [(WARN, "team-telemetry", f"状态读取失败：{type(exc).__name__}")]
    if not info.get("enabled"):
        return [(WARN, "team-telemetry", "team.json 无效（需要 team_id/collect_repo/salt）")]
    rows = [(PASS, "team-telemetry",
             f"team={info.get('team_id')} branch={info.get('branch')} interval={info.get('upload_interval_hours')}h")]
    anonymous = info.get("anonymous")
    rows.append((WARN if anonymous else PASS, "telemetry-identity",
                 f"user={info.get('user')} install={info.get('install')}"
                 + (" 匿名（未配置 git user.email）" if anonymous else "")))
    clone = home / ".claude" / "aicj" / "telemetry" / "collect-repo"
    reach = "本地克隆存在" if (clone / ".git").exists() else "尚未克隆（首次上传后建立）"
    last_error = info.get("last_error")
    detail = (f"last_success={info.get('last_success') or '-'} last_error={last_error or '-'} "
              f"spool={info.get('spool_files', 0)}文件/{info.get('spool_bytes', 0)}B pending={info.get('pending', 0)} "
              f"collect_repo: {reach}")
    rows.append((WARN if last_error else PASS, "telemetry-report", detail))
    return rows


def _hud(home: Path, loaded: manifest.Manifest) -> list[tuple[str, str, str]]:
    record = loaded.hud
    if not record or loaded.options.no_hud:
        return [(SKIPPED, "claude-hud", "未安装或由 --no-hud 关闭")]
    state, reason = hud.status(home, record)
    if state in ("skipped", "kept-other"):
        return [(SKIPPED, "claude-hud", reason or state)]
    if state != "installed":
        return [(WARN, "claude-hud", reason or state)]
    try:
        payload = settings.load(home / ".claude/settings.json")
        command = hud._statusline_command(payload)
    except Exception as exc:
        return [(WARN, "claude-hud", f"无法读取状态栏：{exc}")]
    if "claude-hud" not in command.strip().casefold():
        return [(WARN, "claude-hud", "settings.json 的 statusLine.command 不含 claude-hud")]
    launcher = home / ".claude/plugins/claude-hud/statusline.mjs"
    if not launcher.is_file():
        return [(WARN, "claude-hud", f"启动器不存在：{launcher}")]
    return [(PASS, "claude-hud", "状态栏已安装")]


def _load_manifest(home: Path, checks: list) -> manifest.Manifest | None:
    path = home / manifest.REL_PATH
    if not path.is_file():
        checks.append((MISSING, "manifest", f"{path} absent"))
        return None
    try:
        loaded = manifest.load(home)
    except UserError as exc:
        checks.append((MISSING, "manifest", str(exc)))
        return None
    checks.append((PASS, "manifest", f"{len(loaded.entries)} entries"))
    if loaded.state == manifest.IN_PROGRESS:
        checks.append((WARN, "manifest state", "install was interrupted; rerun install to finish or uninstall"))
    return loaded


def _entries(home: Path, loaded: manifest.Manifest) -> list[tuple[str, str, str]]:
    if not loaded.entries:
        return [(SKIPPED, "component entries", "no components installed")]
    out: list[tuple[str, str, str]] = []
    for entry in loaded.entries:
        if entry.kind in ("md-block", engine.SKILL_DIR):
            continue
        target = home / entry.path
        if not target.exists() and not target.is_symlink():
            out.append((MISSING, entry.path, f"action={entry.action}"))
            continue
        if entry.kind == "settings-hook":
            out.append((PASS, entry.path, f"action={entry.action}"))
        elif entry.action not in engine.OWNED_ACTIONS:
            if entry.action == "skipped":
                out.append((WARN, entry.path, "skipped at install (existing content kept)"))
            else:
                out.append((PASS, entry.path, f"action={entry.action}"))
        else:
            state = engine.entry_state(target, entry)
            if state == "modified":
                out.append((WARN, entry.path, "modified after install"))
            elif state == "unverified":
                out.append((WARN, entry.path, "no checksum recorded"))
            else:
                out.append((PASS, entry.path, f"action={entry.action}"))
    return out or [(SKIPPED, "component entries", "no file entries")]


def _plugins(home: Path, loaded: manifest.Manifest, source: Path | None) -> list[tuple[str, str, str]]:
    """Optional plugin dependencies are health hints; missing dependencies never become MISSING."""
    root = source
    if root is None and loaded.source_repo:
        root = Path(loaded.source_repo)
    if root is None or not root.is_dir():
        return []
    out: list[tuple[str, str, str]] = []
    for name in loaded.options.adapters:
        plugin_path = index._manifest_path(root, "plugin", name.strip())
        if plugin_path is None or not plugin_path.is_file():
            continue
        try:
            requirements = index.plugin_requirements(plugin_path)
            missing = index.missing_requirements(home, requirements)
        except UserError as exc:
            out.append((SKIPPED, f"plugin {name}", f"requires 声明不可用：{exc}；装好后即可用"))
            continue
        if missing:
            out.append((SKIPPED, f"plugin {requirements.name}", "; ".join(missing) + "；装好后即可用"))
        else:
            out.append((PASS, f"plugin {requirements.name}", "dependencies available"))
    return out


def _md_block(home: Path) -> list[tuple[str, str, str]]:
    path = home / engine.CLAUDE_MD
    if not path.is_file():
        return [(MISSING, engine.CLAUDE_MD, "entry file absent")]
    try:
        text = path.read_text(encoding="utf-8")
        present = mdblock.has_block(text)
    except UnicodeDecodeError:
        return [(WARN, engine.CLAUDE_MD, "not valid UTF-8, marker block not checked")]
    except OSError as exc:
        return [(WARN, engine.CLAUDE_MD, f"unreadable: {exc}")]
    except UserError as exc:
        return [(WARN, engine.CLAUDE_MD, str(exc))]
    if not present:
        return [(MISSING, engine.CLAUDE_MD, "marker block absent")]
    out = [(PASS, engine.CLAUDE_MD, "marker block present")]
    entry = home / ".claude" / engine.GLOBAL_REF
    if entry.is_file():
        out.append((PASS, engine.GLOBAL_REF, str(entry)))
    else:
        out.append((WARN, engine.GLOBAL_REF, "global entry file absent"))
    return out


def _hooks(home: Path, loaded: manifest.Manifest) -> list[tuple[str, str, str]]:
    if not loaded.settings_hooks:
        return [(SKIPPED, "settings hooks", "no hooks registered")]
    out: list[tuple[str, str, str]] = []
    grouped: dict[str, list[settings.HookKey]] = {}
    for row in loaded.settings_hooks:
        target = str(row.get("target", engine.SETTINGS))
        grouped.setdefault(target, []).append(
            settings.HookKey(str(row.get("event", "")), str(row.get("matcher", "")), str(row.get("command", "")), tuple(str(x) for x in row.get("args", []) or []))
        )
    for target, keys in sorted(grouped.items()):
        path = home / target
        try:
            payload = settings.load(path)
        except UserError as exc:
            out.append((MISSING, target, str(exc)))
            continue
        for key in keys:
            status = PASS if settings.has_hook(payload, key) else MISSING
            out.append((status, target, f"{key.event}|{key.matcher}"))
    return out


def _optional_hooks(home: Path, loaded: manifest.Manifest, source: Path | None) -> list[tuple[str, str, str]]:
    """Optional hooks the user did not name are SKIPPED, never MISSING."""
    if source is None:
        return []
    try:
        idx = index.load_index(source, loaded.options.packs, loaded.options.adapters)
    except UserError:
        return []
    out: list[tuple[str, str, str]] = []
    for hook in idx.hooks:
        if not hook.optional or hook.name in loaded.options.enable_hooks:
            continue
        command, args = engine.hook_command(hook, home, loaded.options.strict)
        if any(_same_hook_script(row, command, args) for row in loaded.settings_hooks):
            continue
        out.append((SKIPPED, f"optional hook {hook.name}", "not enabled (--enable-hook to register)"))
    return out


def _hook_requirements(home: Path, loaded: manifest.Manifest, source: Path | None) -> list[tuple[str, str, str]]:
    if source is None and loaded.source_repo:
        source = Path(loaded.source_repo)
    if source is None:
        return []
    try:
        idx = index.load_index(source, loaded.options.packs, loaded.options.adapters)
    except UserError:
        return []
    out = []
    for hook in idx.hooks:
        if not hook.requires:
            continue
        command, args = engine.hook_command(hook, home, loaded.options.strict)
        if any(_same_hook_script(row, command, args) for row in loaded.settings_hooks):
            continue
        missing = [target for target in hook.requires if not any(
            entry.path == engine.hook_required_target(target) and entry.action in engine.HOOK_READY_ACTIONS
            for entry in loaded.entries
        )]
        if missing:
            out.append((WARN, f"hook {hook.name} requirements", ", ".join(missing)))
    return out


def _bin_path(home: Path, loaded: manifest.Manifest) -> list[tuple[str, str, str]]:
    bin_entries = [entry for entry in loaded.entries if entry.path.startswith(".claude/bin/")]
    if not bin_entries:
        return [(SKIPPED, "bin PATH", "no bin tools installed")]
    bin_dir = home / ".claude" / "bin"
    paths = [os.path.abspath(os.path.expanduser(p)) for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    if os.path.abspath(str(bin_dir)) in paths:
        return [(PASS, "bin PATH", str(bin_dir))]
    return [(WARN, "bin PATH", f"{bin_dir} not on PATH")]


def _codex(home: Path, loaded: manifest.Manifest) -> list[tuple[str, str, str]]:
    if not (loaded.options.codex or loaded.options.codex_hooks):
        return [(SKIPPED, "codex", "not enabled")]
    out: list[tuple[str, str, str]] = []
    codex_dir = home / ".codex"
    if codex_dir.is_dir():
        out.append((PASS, "codex", str(codex_dir)))
    else:
        out.append((WARN, "codex", f"{codex_dir} absent"))
    if loaded.options.codex:
        links = [entry for entry in loaded.entries if entry.path.startswith(engine.CODEX_SKILLS + "/")]
        for entry in links:
            target = home / entry.path
            status = PASS if target.is_symlink() or target.is_dir() else MISSING
            out.append((status, entry.path, "skill link" if target.is_symlink() else "skill copy"))
    if is_windows() and loaded.options.codex_hooks:
        out.append((SKIPPED, "codex hooks", "Windows behavior not confirmed; hooks were not merged"))
    return out


def _third_party(home: Path) -> list[tuple[str, str, str]]:
    root = home / ".agents" / "skills"
    out: list[tuple[str, str, str]] = []
    for name in THIRD_PARTY_SKILLS:
        if (root / name).exists():
            out.append((PASS, f"third-party skill {name}", str(root / name)))
        else:
            out.append((WARN, f"third-party skill {name}", f"not found under {root}; install manually if needed"))
    return out
