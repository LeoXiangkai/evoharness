from __future__ import annotations

import copy
import hashlib
import json
import os
import posixpath
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import conflict, hud, index, manifest, mdblock, settings, telemetry
from .util import (
    UserError,
    atomic_write,
    atomic_write_text,
    git_commit,
    iso_now,
    is_windows,
    sha256_file,
    sha256_text,
    timestamp,
    unique_backup_rel,
)

GLOBAL_REF = "aicj/CLAUDE.global.md"
CLAUDE_MD = ".claude/CLAUDE.md"
SETTINGS = ".claude/settings.json"
TEAM_REL = telemetry.TEAM_REL
CODEX_HOOKS = ".codex/hooks.json"
CODEX_SKILLS = ".agents/skills"
HOOK_PATH_MARK = settings.HOOK_PATH_MARK
OWNED_ACTIONS = (conflict.CREATED, conflict.REPLACED)
UPDATED = "updated"
SKILL_DIR = "skill-dir"
HUD_FILE = "hud-file"
GATEWAY_ENV = "ANTHROPIC_BASE_URL"
# a hook may only be registered when its script is actually in place
HOOK_READY_ACTIONS = (conflict.CREATED, conflict.REPLACED, conflict.ADOPTED, conflict.ADOPTED_SYMLINK)


@dataclass
class Report:
    lines: list[tuple[str, str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add(self, verb: str, path: str, note: str = "") -> None:
        self.lines.append((verb, path, note))

    def note(self, text: str) -> None:
        self.notes.append(text)

    def warn(self, text: str) -> None:
        self.warnings.append(text)


def hook_command(hook: index.HookItem, home: Path, strict: bool) -> tuple[str, tuple[str, ...]]:
    script_rel = posixpath.normpath(hook.script.replace("\\", "/"))
    if not script_rel.startswith("hooks/aicj/") or ".." in script_rel.split("/"):
        raise UserError(f"hook script must live under hooks/aicj/: {hook.script}")
    script = home / ".claude" / script_rel
    return os.path.abspath(sys.executable), (str(script),)


def hook_script_target(hook: index.HookItem) -> str:
    return f"{index.CLAUDE_DIR}/{posixpath.normpath(hook.script.replace(chr(92), '/'))}"


def hook_required_target(raw: str) -> str:
    value = posixpath.normpath(str(raw).replace("\\", "/")).lstrip("/")
    if value.startswith((".claude/", ".codex/", ".agents/")):
        return value
    return f".claude/{value}"


def entry_state(target: Path, entry: manifest.Entry) -> str:
    """ok / missing / modified / unverified: does the path still look like what we installed."""
    if not target.exists() and not target.is_symlink():
        return "missing"
    if entry.kind == "symlink":
        if not entry.link:
            return "unverified"
        return "ok" if target.is_symlink() and os.path.normcase(os.path.realpath(target)) == os.path.normcase(entry.link) else "modified"
    if not entry.sha256:
        return "unverified"
    if entry.kind == "skill-copy":
        try:
            return "ok" if target.is_dir() and not target.is_symlink() and _dir_digest(target) == entry.sha256 else "modified"
        except OSError:
            return "modified"
    if target.is_symlink() or not target.is_file():
        return "modified"
    try:
        return "ok" if sha256_file(target) == entry.sha256 else "modified"
    except OSError:
        return "modified"


def _dir_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise OSError(f"unexpected symlink in copied skill: {path}")
        if path.is_file():
            digest.update(path.relative_to(directory).as_posix().encode("utf-8") + b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def payload_digest(payload: dict) -> str:
    return sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise UserError(f"{path} is not valid UTF-8 text: {exc}") from exc
    except OSError as exc:
        raise UserError(f"cannot read {path}: {exc}") from exc


class Journal:
    """Writes the manifest as an append-only log so an interrupted run stays uninstallable."""

    def __init__(self, home: Path, base: manifest.Manifest | None, enabled: bool) -> None:
        self.home = home
        self.enabled = enabled
        self.entries: dict[str, manifest.Entry] = {e.path: e for e in (base.entries if base else [])}
        self.hooks: dict[str, list[dict]] = {}
        for row in base.settings_hooks if base else []:
            self.hooks.setdefault(str(row.get("target", SETTINGS)), []).append(row)
        self.created_dirs: list[str] = list(base.created_dirs) if base else []
        self.meta = manifest.Manifest()

    def ensure_dir(self, directory: Path) -> None:
        if not self.enabled:
            return
        missing: list[Path] = []
        current = directory
        while current != self.home and not current.exists():
            missing.append(current)
            current = current.parent
        if not self.home.exists():
            self.home.mkdir(parents=True)
        for path in reversed(missing):
            path.mkdir()
            rel = path.relative_to(self.home).as_posix()
            if rel not in self.created_dirs:
                self.created_dirs.append(rel)

    def record(self, entry: manifest.Entry) -> None:
        self.entries[entry.path] = entry
        self.flush(manifest.IN_PROGRESS)

    def set_hooks(self, rel: str, rows: list[dict]) -> None:
        self.hooks[rel] = rows
        self.flush(manifest.IN_PROGRESS)

    def set_gateway(self, record: dict) -> None:
        self.meta.gateway = record
        self.flush(manifest.IN_PROGRESS)

    def flush(self, state: str, entries: list[manifest.Entry] | None = None) -> None:
        if not self.enabled:
            return
        self.ensure_dir((self.home / manifest.REL_PATH).parent)
        meta = self.meta
        meta.entries = list(self.entries.values()) if entries is None else entries
        meta.settings_hooks = [row for rows in self.hooks.values() for row in rows]
        meta.created_dirs = list(self.created_dirs)
        meta.state = state
        meta.save(self.home)


# ---------------------------------------------------------------------------
# planning (pure: reads state, writes nothing)
# ---------------------------------------------------------------------------


@dataclass
class FilePlan:
    item: index.FileItem
    decision: str
    entry: manifest.Entry
    backup_rel: str = ""


@dataclass
class DirBackup:
    entry: manifest.Entry
    backup_rel: str


@dataclass
class CodexPlan:
    rel: str
    source: Path
    decision: str
    entry: manifest.Entry
    backup_rel: str = ""


@dataclass
class GeneratedPlan:
    data: bytes
    decision: str
    entry: manifest.Entry
    backup_rel: str = ""


def _plan_generated(rel: str, data: bytes, home: Path, options: manifest.Options,
                    previous: dict[str, manifest.Entry], ts: str, report: Report) -> GeneratedPlan:
    target = home / rel
    if (blocker := _blocker(home, target.parent)) is not None:
        raise UserError(f"{blocker} is not a directory; cannot install {rel}")
    prev = previous.get(rel)
    digest = hashlib.sha256(data).hexdigest()
    backup = ""
    if not target.exists() and not target.is_symlink():
        decision = conflict.CREATED
    elif target.is_file() and not target.is_symlink() and sha256_file(target) == digest:
        decision = conflict.ADOPTED
    elif _is_owned(prev) and entry_state(target, prev) == "ok":
        decision = UPDATED
    elif options.on_conflict == "backup":
        decision = conflict.REPLACED
        backup = unique_backup_rel(home, rel, ts)
    else:
        decision = conflict.SKIPPED
    if decision == conflict.SKIPPED:
        entry = prev if _is_owned(prev) else manifest.Entry(rel, "file", decision, digest)
    elif decision == conflict.REPLACED:
        original = prev.backup if _is_owned(prev) and _backup_present(home, prev.backup) else backup
        entry = manifest.Entry(rel, "file", decision, digest, original)
    elif _is_owned(prev):
        entry = manifest.Entry(rel, "file", prev.action, digest, prev.backup)
    else:
        entry = manifest.Entry(rel, "file", decision, digest)
    report.add(decision, rel, f"backup {backup}" if backup else "")
    return GeneratedPlan(data, decision, entry, backup)


def _execute_generated(plan: GeneratedPlan, home: Path, journal: Journal) -> None:
    target = home / plan.entry.path
    if plan.decision not in (conflict.SKIPPED, conflict.ADOPTED):
        journal.ensure_dir(target.parent)
        if plan.decision == conflict.REPLACED:
            journal.record(plan.entry)
            target.rename(home / plan.backup_rel)
        atomic_write(target, plan.data)
    journal.record(plan.entry)


def _skill_dir(target: str) -> str | None:
    parts = Path(target).parts
    if len(parts) >= 4 and parts[0] == ".claude" and parts[1] == "skills":
        return "/".join(parts[:3])
    return None


def _blocker(home: Path, path: Path) -> Path | None:
    """First existing ancestor (from `path` up to home) that is not a directory."""
    current = path
    while current != home and home in current.parents:
        if (current.exists() or current.is_symlink()) and not current.is_dir():
            return current
        current = current.parent
    return None


def _is_owned(previous: manifest.Entry | None) -> bool:
    return previous is not None and previous.action in OWNED_ACTIONS


def _decide(item: index.FileItem, home: Path, options: manifest.Options, previous: manifest.Entry | None) -> str:
    target = home / item.target
    decision = conflict.classify(item.source, target, options.link)
    if decision == conflict.CREATED or not _is_owned(previous):
        return decision
    wanted_kind = "symlink" if options.link else "file"
    if previous.kind in ("file", "symlink") and previous.kind != wanted_kind:
        return UPDATED if entry_state(target, previous) == "ok" else conflict.CONFLICT
    if decision == conflict.CONFLICT and entry_state(target, previous) == "ok":
        return UPDATED
    return decision


def _file_entry(
    item: index.FileItem,
    decision: str,
    options: manifest.Options,
    previous: manifest.Entry | None,
    backup_rel: str,
    home: Path,
    source_digest: str,
) -> manifest.Entry:
    kind = "symlink" if options.link or decision == conflict.ADOPTED_SYMLINK else "file"
    link = os.path.realpath(item.source) if kind == "symlink" else ""
    owned = _is_owned(previous)
    if decision == conflict.REPLACED:
        backup = backup_rel
        if owned and previous.action == conflict.REPLACED and previous.backup:
            earlier = home / previous.backup
            if earlier.exists() or earlier.is_symlink():
                backup = previous.backup
        return manifest.Entry(item.target, kind, conflict.REPLACED, source_digest, backup, link)
    if owned:
        # keep ownership from the earlier install so uninstall still removes or restores it
        return manifest.Entry(item.target, kind, previous.action, source_digest, previous.backup, link)
    return manifest.Entry(item.target, kind, decision, source_digest, "", link)


def _plan_file(
    item: index.FileItem,
    home: Path,
    options: manifest.Options,
    ts: str,
    report: Report,
    previous: manifest.Entry | None,
) -> FilePlan:
    target = home / item.target
    blocker = _blocker(home, target.parent)
    if blocker is not None:
        raise UserError(f"{blocker} is not a directory; cannot install {item.target}")
    source_digest = sha256_file(item.source)
    decision = _decide(item, home, options, previous)
    backup_rel = ""
    if decision == conflict.CONFLICT:
        if options.on_conflict != "backup":
            report.add(conflict.SKIPPED, item.target, "content differs; rerun with --on-conflict backup")
            entry = previous if _is_owned(previous) else manifest.Entry(item.target, "file", conflict.SKIPPED, source_digest)
            return FilePlan(item, conflict.SKIPPED, entry)
        backup_rel = unique_backup_rel(home, item.target, ts)
        decision = conflict.REPLACED
    entry = _file_entry(item, decision, options, previous, backup_rel, home, source_digest)
    if decision == conflict.REPLACED and entry.backup != backup_rel:
        report.note(f"{item.target}: earlier user original kept at {entry.backup}; newer copy at {backup_rel}")
    report.add(decision, item.target, f"backup {backup_rel}" if backup_rel else "")
    return FilePlan(item, decision, entry, backup_rel)


def _plan_group(
    key: str,
    items: list[index.FileItem],
    home: Path,
    options: manifest.Options,
    ts: str,
    report: Report,
    previous: dict[str, manifest.Entry],
) -> tuple[list, bool]:
    """A skill directory installs as a unit: any conflicting file conflicts the whole skill."""
    skill = home / key
    above = _blocker(home, skill.parent)
    if above is not None:
        raise UserError(f"{above} is not a directory; cannot install {key}")
    conflicting = skill.is_symlink() or (skill.exists() and not skill.is_dir())
    for item in items:
        if _blocker(home, (home / item.target).parent) is not None:
            conflicting = True
        elif _decide(item, home, options, previous.get(item.target)) == conflict.CONFLICT:
            conflicting = True

    if not conflicting:
        return [_plan_file(item, home, options, ts, report, previous.get(item.target)) for item in items], True

    if options.on_conflict != "backup":
        plans: list = []
        for item in items:
            prev = previous.get(item.target)
            entry = prev if _is_owned(prev) else manifest.Entry(item.target, "file", conflict.SKIPPED, sha256_file(item.source))
            report.add(conflict.SKIPPED, item.target, "skill has conflicting files; whole skill skipped (--on-conflict backup)")
            plans.append(FilePlan(item, conflict.SKIPPED, entry))
        return plans, False

    backup_rel = unique_backup_rel(home, key, ts)
    earlier = previous.get(key)
    kept = backup_rel
    if earlier is not None and earlier.action == conflict.REPLACED and earlier.backup:
        if (home / earlier.backup).exists():
            kept = earlier.backup
            report.note(f"{key}: earlier user original kept at {kept}; newer copy at {backup_rel}")
    plans = [DirBackup(manifest.Entry(key, SKILL_DIR, conflict.REPLACED, "", kept), backup_rel)]
    report.add("backup", key, f"whole skill directory -> {backup_rel}")
    for item in items:
        entry = _file_entry(item, conflict.CREATED, options, None, "", home, sha256_file(item.source))
        report.add(conflict.CREATED, item.target, "fresh skill directory")
        plans.append(FilePlan(item, conflict.CREATED, entry))
    return plans, True


def _plan_files(
    files: list[index.FileItem],
    home: Path,
    options: manifest.Options,
    ts: str,
    report: Report,
    previous: dict[str, manifest.Entry],
) -> tuple[list, dict[str, bool]]:
    groups: dict[str, list[index.FileItem]] = {}
    order: list[tuple[str, object]] = []
    for item in files:
        key = _skill_dir(item.target)
        if key is None:
            order.append(("file", item))
        else:
            if key not in groups:
                groups[key] = []
                order.append(("group", key))
            groups[key].append(item)
    ops: list = []
    installed: dict[str, bool] = {}
    for kind, value in order:
        if kind == "file":
            ops.append(_plan_file(value, home, options, ts, report, previous.get(value.target)))
        else:
            plans, ok = _plan_group(value, groups[value], home, options, ts, report, previous)
            ops.extend(plans)
            installed[Path(value).name] = ok
    return ops, installed


def _plan_codex(
    skills: dict[str, bool],
    home: Path,
    options: manifest.Options,
    ts: str,
    report: Report,
    previous: dict[str, manifest.Entry],
) -> list[CodexPlan]:
    if not options.codex:
        return []
    out: list[CodexPlan] = []
    for name, installed in skills.items():
        rel = f"{CODEX_SKILLS}/{name}"
        prev = previous.get(rel)
        if not installed:
            report.note(f"{rel}: skill skipped, no codex link")
            if _is_owned(prev):
                out.append(CodexPlan(rel, home, conflict.SKIPPED, prev))
            continue
        source = home / ".claude" / "skills" / name
        target = home / rel
        blocker = _blocker(home, target.parent)
        if blocker is not None:
            raise UserError(f"{blocker} is not a directory; cannot link {rel}")
        decision = conflict.classify(source, target, True)
        if prev is not None and prev.kind == "skill-copy" and _is_owned(prev) and entry_state(target, prev) == "ok":
            decision = conflict.ADOPTED if _dir_digest(source) == prev.sha256 else UPDATED
        backup_rel = ""
        if decision == conflict.CONFLICT:
            if options.on_conflict != "backup":
                report.add(conflict.SKIPPED, rel, "codex skill link conflict")
                out.append(CodexPlan(rel, source, conflict.SKIPPED, prev if _is_owned(prev) else manifest.Entry(rel, "symlink", conflict.SKIPPED)))
                continue
            backup_rel = unique_backup_rel(home, rel, ts)
            decision = conflict.REPLACED
        link = os.path.realpath(source)
        report.add(decision, rel, "codex skill link")
        if decision == conflict.ADOPTED and prev is not None and prev.kind == "skill-copy":
            entry = prev
        elif _is_owned(prev) and decision in (conflict.ADOPTED_SYMLINK, UPDATED):
            entry = manifest.Entry(rel, "symlink", prev.action, "", prev.backup, link)
        else:
            entry = manifest.Entry(rel, "symlink", decision, "", backup_rel, link)
        out.append(CodexPlan(rel, source, decision, entry, backup_rel))
    return out


# ---------------------------------------------------------------------------
# execution
# ---------------------------------------------------------------------------


def _write_item(item: index.FileItem, target: Path, link: bool, journal: Journal, report: Report) -> None:
    journal.ensure_dir(target.parent)
    if link:
        try:
            target.symlink_to(item.source)
            return
        except OSError as exc:
            if isinstance(exc, FileExistsError):
                raise
            report.warn(f"{item.target}: 软链失败，已改为复制；改仓库即生效失效 ({exc})")
    atomic_write(target, item.source.read_bytes())
    if not is_windows():
        target.chmod(item.source.stat().st_mode)


def _execute_file(plan: FilePlan, home: Path, options: manifest.Options, journal: Journal, report: Report) -> None:
    target = home / plan.item.target
    if plan.decision in (conflict.SKIPPED, conflict.ADOPTED, conflict.ADOPTED_SYMLINK):
        journal.record(plan.entry)
        return
    if plan.decision == conflict.REPLACED:
        journal.record(plan.entry)
        target.rename(home / plan.backup_rel)
    elif plan.decision == UPDATED:
        target.unlink()
    requested_link = options.link
    _write_item(plan.item, target, requested_link, journal, report)
    if requested_link and not target.is_symlink():
        plan.entry.kind = "file"
        plan.entry.link = ""
        plan.entry.sha256 = sha256_file(target)
    journal.record(plan.entry)


def _execute_dir_backup(plan: DirBackup, home: Path, journal: Journal) -> None:
    journal.record(plan.entry)
    (home / plan.entry.path).rename(home / plan.backup_rel)


def _execute_codex(plan: CodexPlan, home: Path, journal: Journal, report: Report) -> None:
    target = home / plan.rel
    if plan.decision in (conflict.SKIPPED, conflict.ADOPTED, conflict.ADOPTED_SYMLINK):
        journal.record(plan.entry)
        return
    if plan.decision == conflict.REPLACED:
        journal.record(plan.entry)
        target.rename(home / plan.backup_rel)
    elif plan.decision == UPDATED:
        shutil.rmtree(target)
    journal.ensure_dir(target.parent)
    try:
        target.symlink_to(plan.source, target_is_directory=True)
    except OSError as exc:
        report.warn(f"{plan.rel}: 软链失败，已改为复制；改仓库即生效失效 ({exc})")
        shutil.copytree(plan.source, target, copy_function=lambda src, dst: atomic_write(Path(dst), Path(src).read_bytes()))
        plan.entry.kind = "skill-copy"
        plan.entry.link = ""
        plan.entry.sha256 = _dir_digest(target)
    journal.record(plan.entry)


def _hook_keys(rows: list[dict], target: str) -> list[settings.HookKey]:
    return [
        settings.HookKey(str(row.get("event", "")), str(row.get("matcher", "")), str(row.get("command", "")), tuple(str(x) for x in row.get("args", []) or []))
        for row in rows
        if str(row.get("target", SETTINGS)) == target
    ]


def _merge_hook_file(
    rel: str,
    home: Path,
    payload: dict,
    wanted: list[settings.HookKey],
    stale: list[settings.HookKey],
    owned_before: list[settings.HookKey],
    prev_entry: manifest.Entry | None,
    ts: str,
    dry_run: bool,
    report: Report,
    journal: Journal,
) -> manifest.Entry:
    path = home / rel
    existed = path.exists()
    original_digest = payload_digest(payload)
    removed = settings.unmerge_hooks(payload, stale)
    added = settings.merge_hooks(payload, wanted)
    # only keys we put there are ours; a user's pre-existing identical hook stays theirs
    ours = [key for key in wanted if key in added or key in owned_before]
    rows = [key.to_dict(rel) for key in ours]
    action = prev_entry.action if prev_entry else (conflict.ADOPTED if existed else conflict.CREATED)
    backup_rel = prev_entry.backup if prev_entry else ""
    # digest of the user's own settings (before our first change), used to prove a full restore later
    user_digest = prev_entry.sha256 if prev_entry else ""
    if not user_digest and existed and action != conflict.CREATED:
        user_digest = original_digest
    if (added or removed) and not dry_run:
        if existed:
            new_backup = unique_backup_rel(home, rel, ts)
            shutil.copy2(path, home / new_backup)
            report.add("backup", new_backup)
            old = home / backup_rel if backup_rel else None
            if old is not None and (old.exists() or old.is_symlink()):
                old.unlink()
            backup_rel = new_backup
        entry = manifest.Entry(rel, "settings-hook", action, user_digest, backup_rel)
        journal.set_hooks(rel, rows)
        journal.record(entry)
        journal.ensure_dir(path.parent)
        settings.save(path, payload)
        settings.load(path)
    for key in removed:
        report.add("unhook", rel, f"{key.event}|{key.matcher} (stale)")
    for key in added:
        report.add("hook", rel, f"{key.event}|{key.matcher}")
    for key in wanted:
        if key not in added:
            report.add(conflict.ADOPTED, rel, f"{key.event}|{key.matcher}")
    entry = manifest.Entry(rel, "settings-hook", action, user_digest, backup_rel)
    if not dry_run:
        journal.set_hooks(rel, rows)
        journal.record(entry)
    return entry


def _emit_failure(home: Path, source: Path | None, command: str, exc: BaseException,
                  dry_run: bool, options=None) -> None:
    if not dry_run:
        telemetry.emit_install(home, source, command, False, error_kind=type(exc).__name__, options=options)


def run_install(
    home: Path,
    source: Path,
    options: manifest.Options,
    dry_run: bool,
) -> tuple[Report, manifest.Manifest]:
    try:
        return _run_install(home, source, options, dry_run)
    except UnicodeError as exc:
        _emit_failure(home, source, "install", exc, dry_run, options)
        raise UserError(f"install aborted: undecodable text: {exc}") from exc
    except OSError as exc:
        _emit_failure(home, source, "install", exc, dry_run, options)
        raise UserError(f"install aborted: {exc}; rerun after fixing it (progress is recorded, uninstall still works)") from exc
    except Exception as exc:
        _emit_failure(home, source, "install", exc, dry_run, options)
        raise


def _run_install(
    home: Path,
    source: Path,
    options: manifest.Options,
    dry_run: bool,
) -> tuple[Report, manifest.Manifest]:
    report = Report()
    idx = index.load_index(source, options.packs, options.adapters)
    for note in idx.notes:
        report.note(note)
    for requirements in idx.plugins:
        missing = index.missing_requirements(home, requirements)
        if missing:
            report.note(f"plugin {requirements.name}: {'; '.join(missing)}；装好后即可用")

    old = manifest.load(home)
    previous = {entry.path: entry for entry in old.entries} if old else {}
    old_hooks = old.settings_hooks if old else []
    if old is not None and old.state == manifest.IN_PROGRESS:
        report.note("previous install was interrupted; entries it recorded are still treated as ours")

    # 1. read and validate every input before anything is written
    codex_hooks_enabled = options.codex_hooks and not is_windows()
    if options.codex_hooks and is_windows():
        report.note("Windows 上 Codex hooks 暂不合并：Codex hooks 行为未确认")
    hook_targets = [SETTINGS, CODEX_HOOKS] if codex_hooks_enabled else [SETTINGS]
    hook_targets += sorted({str(row.get("target", SETTINGS)) for row in old_hooks} - set(hook_targets))
    payloads = {rel: settings.load(home / rel) for rel in hook_targets}
    claude_path = home / CLAUDE_MD
    claude_text = _read_text(claude_path) if claude_path.exists() else ""
    block_span = mdblock.find_block(claude_text)

    team_bytes: bytes | None = None
    if options.team_config.strip():
        team_bytes = telemetry.fetch_team_config(options.team_config)
        if not dry_run:
            warning = telemetry.probe_collect_repo(team_bytes, home, source)
            if warning:
                report.warn(warning)
    elif TEAM_REL in previous:
        team_bytes = telemetry.existing_team_bytes(home)
    gateway = telemetry.gateway_url(team_bytes)
    if gateway:
        _env_of(payloads[SETTINGS])  # malformed env aborts before anything is written

    # 2. plan (pure)
    ts = timestamp()
    file_ops, skills = _plan_files(idx.files, home, options, ts, report, previous)
    codex_ops = _plan_codex(skills, home, options, ts, report, previous)
    planned = {op.entry.path: op.entry for op in file_ops + codex_ops}
    generated_ops: list[GeneratedPlan] = []
    config_rel = ".claude/aicj/config.json"
    mode = "block" if options.strict else "warn"
    executor_mode = "worker" if any(str(name).strip().casefold() == "executor" for name in options.adapters) else "cc"
    config = json.dumps({"hook_mode": mode, "executor_mode": executor_mode}, ensure_ascii=False, separators=(",", ":")) + "\n"
    generated_ops.append(_plan_generated(config_rel, config.encode("utf-8"), home, options, previous, ts, report))
    if team_bytes is not None:
        generated_ops.append(_plan_generated(TEAM_REL, team_bytes, home, options, previous, ts, report))
    if is_windows():
        for op in file_ops:
            if isinstance(op, FilePlan) and op.entry.path.startswith(".claude/bin/") and op.decision != conflict.SKIPPED:
                name = Path(op.entry.path).name
                rel = f".claude/bin/{name}.cmd"
                launcher = f'@"{os.path.abspath(sys.executable)}" "%~dp0{name}" %*\r\n'
                generated_ops.append(_plan_generated(rel, launcher.encode("utf-8"), home, options, previous, ts, report))

    keys: list[settings.HookKey] = []
    unknown = [name for name in options.enable_hooks if name not in {h.name for h in idx.hooks}]
    if unknown:
        raise UserError(
            f"unknown --enable-hook name(s): {', '.join(unknown)}; available: "
            + (", ".join(sorted({h.name for h in idx.hooks})) or "(none)")
        )
    for hook in idx.hooks:
        if hook.optional and hook.name not in options.enable_hooks:
            report.note(f"可选钩子未启用：{hook.name}（用 --enable-hook 开启）")
            continue
        if team_bytes is None and Path(hook.script).name.startswith("telemetry-"):
            continue  # no team config: telemetry leaves no footprint in settings
        command, args = hook_command(hook, home, options.strict)
        script_entry = planned.get(hook_script_target(hook))
        if script_entry is None or script_entry.action not in HOOK_READY_ACTIONS:
            report.warn(f"hook {hook.event} {hook.script}: script not installed, hook not registered")
            continue
        missing = [target for target in hook.requires if (entry := planned.get(hook_required_target(target))) is None or entry.action not in HOOK_READY_ACTIONS]
        if missing:
            report.note(f"hook {hook.name} 未注册：依赖未就绪（{', '.join(missing)}）")
            continue
        matcher = "" if hook.event in settings.NO_MATCHER_EVENTS else hook.matcher
        keys.append(settings.HookKey(hook.event, matcher, command, args))

    hook_plan: list[tuple[str, list, list, list]] = []
    for rel in hook_targets:
        wanted = keys if rel == SETTINGS or (rel == CODEX_HOOKS and codex_hooks_enabled) else []
        owned_before = _hook_keys(old_hooks, rel)
        stale = [key for key in owned_before if key not in wanted]
        if not wanted and not stale:
            continue
        trial = copy.deepcopy(payloads[rel])
        settings.unmerge_hooks(trial, stale)
        settings.merge_hooks(trial, wanted)
        hook_plan.append((rel, wanted, stale, owned_before))

    # 3. execute, journaling every landed entry
    journal = Journal(home, old, enabled=not dry_run)
    journal.meta = manifest.Manifest(
        source_repo=str(source),
        source_commit=git_commit(source) if not dry_run else "",
        installed_at=iso_now(),
        options=options,
        gateway=dict(old.gateway) if old else {},
    )
    if not dry_run:
        journal.flush(manifest.IN_PROGRESS)
        for op in file_ops:
            if isinstance(op, DirBackup):
                _execute_dir_backup(op, home, journal)
            else:
                _execute_file(op, home, options, journal, report)
        for op in codex_ops:
            _execute_codex(op, home, journal, report)
        for op in generated_ops:
            _execute_generated(op, home, journal)

    hook_entries: list[manifest.Entry] = []
    for rel, wanted, stale, owned_before in hook_plan:
        hook_entries.append(
            _merge_hook_file(
                rel, home, payloads[rel], wanted, stale, owned_before, previous.get(rel), ts, dry_run, report, journal
            )
        )

    md_entry = _apply_md_block(home, claude_text, block_span, dry_run, report, previous.get(CLAUDE_MD), journal)

    gateway_record = _apply_gateway(home, gateway, old.gateway if old else {}, options, dry_run, report, journal)

    hud_before = hud.runtime_artifacts(home) if not dry_run else {}
    hud_record = hud.install(home, report, dry_run=dry_run, disabled=options.no_hud, previous=old.hud if old else None)
    hud_entries: list[manifest.Entry] = []
    if not dry_run:
        for path, digest in hud.runtime_artifacts(home).items():
            if path not in hud_before:
                hud_entries.append(manifest.Entry(path, HUD_FILE, conflict.CREATED, digest))

    ordered = [op.entry for op in file_ops + codex_ops]
    ordered.extend(op.entry for op in generated_ops)
    ordered.extend(hud_entries)
    names = {entry.path for entry in ordered} | {entry.path for entry in hook_entries} | {CLAUDE_MD}
    for path, entry in previous.items():
        if path in names:
            continue
        keep_kind = entry.kind in ("file", "symlink", "skill-copy", SKILL_DIR, HUD_FILE)
        if keep_kind and entry.action in OWNED_ACTIONS:
            # hud-file entries are Claude Code files recorded when the plugin was first installed; a reinstall
            # does not recreate them, so carrying them over is the normal case, not a dropped source file.
            if entry.kind != HUD_FILE:
                report.note(f"{path}: no longer shipped, kept and still tracked for uninstall")
            ordered.append(entry)
    ordered.extend(hook_entries)
    ordered.append(md_entry)

    hooks_rows = [row for rows in journal.hooks.values() for row in rows] if not dry_run else []
    result = journal.meta
    result.entries = ordered
    result.settings_hooks = hooks_rows
    result.created_dirs = list(journal.created_dirs)
    result.hud = hud_record
    result.gateway = gateway_record
    if not dry_run:
        # hook files dropped from this run (nothing wanted, nothing stale) must not leave rows behind
        for rel in list(journal.hooks):
            if rel not in {row[0] for row in hook_plan} and not journal.hooks[rel]:
                journal.hooks.pop(rel)
        journal.flush(manifest.COMPLETE, ordered)
        report.add("manifest", manifest.REL_PATH)
        telemetry.emit_install(home, source, "install", True, options=options)
    return report, result


def _env_of(payload: dict) -> dict:
    env = payload.get("env")
    if env is None:
        return {}
    if not isinstance(env, dict):
        raise UserError(f"{SETTINGS}: env must be a JSON object")
    return env


def _apply_gateway(
    home: Path,
    url: str | None,
    record: dict,
    options: manifest.Options,
    dry_run: bool,
    report: Report,
    journal: Journal,
) -> dict:
    """Write env.ANTHROPIC_BASE_URL from team.json; returns the manifest record (never touches any key/token)."""
    if not url:
        return dict(record)
    path = home / SETTINGS
    existed = path.exists()
    payload = settings.load(path)
    env = _env_of(payload)
    current = env.get(GATEWAY_ENV)
    unset = current is None or (isinstance(current, str) and not current.strip())
    ours_before = bool(record) and telemetry.same_gateway(current, record.get("value", ""))
    new: dict = {"value": url}
    if record.get("previous"):
        new["previous"] = record["previous"]
    if record.get("created_file") or not existed:
        new["created_file"] = True
    noop = telemetry.same_gateway(current, url)
    if noop and not ours_before:
        new["previous"] = current  # the user's own value: uninstall must leave it in place
    elif not noop and not ours_before:
        if unset:
            new.pop("previous", None)
        elif options.on_conflict == "backup":
            if isinstance(current, str):
                new["previous"] = current
            else:
                new.pop("previous", None)
        else:
            report.warn(f"{SETTINGS}: env.{GATEWAY_ENV} differs; rerun with --on-conflict backup")
            return dict(record)
    if noop:
        report.add(conflict.ADOPTED, SETTINGS, f"env.{GATEWAY_ENV}")
    else:
        report.add("env", SETTINGS, f"{GATEWAY_ENV} -> {url}")
    if not dry_run:
        journal.set_gateway(new)  # recorded before the write so an interrupted run stays uninstallable
        if not noop:
            payload["env"] = {**env, GATEWAY_ENV: url}
            journal.ensure_dir(path.parent)
            settings.save(path, payload)
            settings.load(path)
    return new


def _remove_gateway(home: Path, record: dict, report: Report, dry_run: bool) -> None:
    value = record.get("value") if record else None
    if not isinstance(value, str) or not value:
        return
    path = home / SETTINGS
    if not path.exists():
        report.note(f"{SETTINGS}: already gone")
        return
    payload = settings.load(path)
    env = _env_of(payload)
    current = env.get(GATEWAY_ENV)
    if current is None:
        return
    if not telemetry.same_gateway(current, value):
        report.note(f"{SETTINGS}: env.{GATEWAY_ENV} changed since install, kept")
        return
    previous = record.get("previous")
    env = dict(env)
    if isinstance(previous, str) and previous:
        env[GATEWAY_ENV] = previous
        report.add("restored", SETTINGS, f"env.{GATEWAY_ENV}")
    else:
        env.pop(GATEWAY_ENV)
        report.add("removed", SETTINGS, f"env.{GATEWAY_ENV}")
    if env:
        payload["env"] = env
    else:
        payload.pop("env", None)
    if dry_run:
        return
    if not payload and record.get("created_file"):
        path.unlink()
    else:
        settings.save(path, payload)


def _apply_md_block(
    home: Path,
    text: str,
    span: tuple[int, int] | None,
    dry_run: bool,
    report: Report,
    previous: manifest.Entry | None,
    journal: Journal,
) -> manifest.Entry:
    path = home / CLAUDE_MD
    if span is not None:
        found = text[span[0] : span[1]]
        action = previous.action if previous else conflict.ADOPTED
        block = previous.block if previous and previous.block else found
        entry = manifest.Entry(CLAUDE_MD, "md-block", action, sha256_text(block), block=block)
        report.add(conflict.ADOPTED, CLAUDE_MD, "marker block present")
        if not dry_run:
            journal.record(entry)
        return entry
    block = mdblock.build_block(GLOBAL_REF)
    entry = manifest.Entry(CLAUDE_MD, "md-block", conflict.CREATED, sha256_text(block), block=block)
    if not dry_run:
        journal.record(entry)
        journal.ensure_dir(path.parent)
        atomic_write_text(path, mdblock.append_block(text, GLOBAL_REF))
    report.add(conflict.CREATED, CLAUDE_MD, "marker block")
    return entry


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------


def _backup_present(home: Path, rel: str) -> bool:
    return bool(rel) and ((home / rel).exists() or (home / rel).is_symlink())


def _restore_or_remove(entry: manifest.Entry, home: Path, report: Report, dry_run: bool, gone: set[Path]) -> bool:
    """Returns True when the entry must stay in the manifest (a backup is still tracked)."""
    target = home / entry.path
    state = entry_state(target, entry)
    replaced = entry.action == conflict.REPLACED
    if state == "missing":
        if replaced and _backup_present(home, entry.backup):
            if not dry_run:
                os.replace(home / entry.backup, target)
            report.add("restored", entry.path, f"from {entry.backup}")
            return False
        report.note(f"{entry.path}: already gone")
        return False
    if state != "ok":
        reason = "modified after install" if state == "modified" else "no checksum recorded"
        text = f"{entry.path}: {reason}, kept"
        if replaced and entry.backup:
            text += f"; your original is at {entry.backup}"
        report.warn(text)
        return replaced
    if replaced and _backup_present(home, entry.backup):
        if not dry_run:
            shutil.rmtree(target) if entry.kind == "skill-copy" else target.unlink()
            os.replace(home / entry.backup, target)
        report.add("restored", entry.path, f"from {entry.backup}")
        return False
    if replaced:
        report.warn(f"{entry.path}: backup {entry.backup} missing, removed without restore")
    if not dry_run:
        shutil.rmtree(target) if entry.kind == "skill-copy" else target.unlink()
    gone.add(target)
    report.add("removed", entry.path)
    return False


def _dir_has_files(directory: Path, gone: set[Path]) -> bool:
    for child in directory.rglob("*"):
        if (child.is_symlink() or not child.is_dir()) and child not in gone:
            return True
    return False


def _restore_skill_dir(entry: manifest.Entry, home: Path, report: Report, dry_run: bool, gone: set[Path], kept: set[Path]) -> bool:
    directory = home / entry.path
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()) or (
        directory.is_dir() and _dir_has_files(directory, gone)
    ):
        report.warn(f"{entry.path}: skill directory not empty, kept; your original is at {entry.backup}")
        return True
    if not _backup_present(home, entry.backup):
        report.warn(f"{entry.path}: backup {entry.backup} missing")
        return False
    if not dry_run:
        if directory.is_dir():
            for sub in sorted(directory.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                sub.rmdir()
            directory.rmdir()
        os.replace(home / entry.backup, directory)
    kept.add(directory)
    report.add("restored", entry.path, f"from {entry.backup}")
    return False


def _remove_md_block(entry: manifest.Entry, home: Path, report: Report, dry_run: bool, text: str | None) -> None:
    path = home / entry.path
    if entry.action != conflict.CREATED:
        report.add("kept", entry.path, f"marker block ({entry.action})")
        return
    if text is None:
        report.note(f"{entry.path}: already gone")
        return
    expected = entry.block
    span = mdblock.find_block(text)
    if span is None:
        report.note(f"{entry.path}: marker block absent")
        return
    found = text[span[0] : span[1]]
    if (expected and found != expected) or (not expected and sha256_text(found) != entry.sha256):
        report.warn(f"{entry.path}: marker block was edited after install, kept")
        return
    rest = text[: span[0]] + text[span[1] :]
    if not dry_run:
        if rest.strip() or path.is_symlink():
            atomic_write_text(path, rest)
        else:
            path.unlink()
    report.add("removed", entry.path, "marker block")


def _unmerge_hook_file(
    rel: str,
    home: Path,
    keys: list[settings.HookKey],
    report: Report,
    entry: manifest.Entry | None,
    dry_run: bool,
) -> None:
    if not keys:
        return
    path = home / rel
    if not path.exists():
        report.note(f"{rel}: already gone")
        return
    payload = settings.load(path)
    removed = settings.unmerge_hooks(payload, keys)
    for key in removed:
        report.add("unhook", rel, f"{key.event}|{key.matcher}")
    if not removed:
        return
    created = entry is not None and entry.action == conflict.CREATED
    backup = entry.backup if entry else ""
    if not payload and created:
        if not dry_run:
            path.unlink()
            if _backup_present(home, backup):
                (home / backup).unlink()
        return
    if not dry_run:
        settings.save(path, payload)
    if backup and _backup_present(home, backup):
        restored = entry is not None and bool(entry.sha256) and payload_digest(payload) == entry.sha256
        if restored:
            if not dry_run:
                (home / backup).unlink()
            report.add("removed", backup, "aicj backup, original fully restored")
        else:
            report.note(f"{backup}: kept (settings differ from your original)")


def run_uninstall(home: Path, dry_run: bool) -> Report:
    try:
        return _run_uninstall(home, dry_run)
    except UnicodeError as exc:
        _emit_failure(home, None, "uninstall", exc, dry_run)
        raise UserError(f"uninstall aborted: undecodable text: {exc}") from exc
    except OSError as exc:
        _emit_failure(home, None, "uninstall", exc, dry_run)
        raise UserError(f"uninstall aborted: {exc}") from exc
    except Exception as exc:
        _emit_failure(home, None, "uninstall", exc, dry_run)
        raise


def _run_uninstall(home: Path, dry_run: bool) -> Report:
    report = Report()
    loaded = manifest.load(home)
    if loaded is None:
        report.note("no manifest; nothing installed")
        return report

    hud.uninstall(home, loaded.hud, report, dry_run=dry_run)

    hook_keys: dict[str, list[settings.HookKey]] = {}
    for row in loaded.settings_hooks:
        target = str(row.get("target", SETTINGS))
        hook_keys.setdefault(target, []).append(
            settings.HookKey(str(row.get("event", "")), str(row.get("matcher", "")), str(row.get("command", "")), tuple(str(x) for x in row.get("args", []) or []))
        )
    hook_entries = {entry.path: entry for entry in loaded.entries if entry.kind == "settings-hook"}

    # validate before removing anything: a malformed CLAUDE.md or settings file aborts the whole run
    md_text: str | None = None
    claude_path = home / CLAUDE_MD
    if any(entry.kind == "md-block" and entry.action == conflict.CREATED for entry in loaded.entries):
        if claude_path.exists():
            md_text = _read_text(claude_path)
            mdblock.find_block(md_text)
    for target in hook_keys:
        if (home / target).exists():
            settings.load(home / target)

    if not dry_run:
        telemetry.uninstall_report(home, None)  # after validation, before the library and team.json are removed
        telemetry.remove_collect_artifacts(home)

    # before hook unmerge: the original-settings digest check expects our env change already gone
    _remove_gateway(home, loaded.gateway, report, dry_run)

    gone: set[Path] = set()
    kept_dirs: set[Path] = set()
    retained: list[manifest.Entry] = []
    # reverse order: codex links point into installed skills and must go first
    for entry in reversed(loaded.entries):
        if entry.kind == "settings-hook":
            continue
        if entry.kind == "md-block":
            _remove_md_block(entry, home, report, dry_run, md_text)
        elif entry.kind == SKILL_DIR:
            if _restore_skill_dir(entry, home, report, dry_run, gone, kept_dirs):
                retained.append(entry)
        elif entry.action in OWNED_ACTIONS:
            if _restore_or_remove(entry, home, report, dry_run, gone):
                retained.append(entry)
        else:
            report.add("kept", entry.path, entry.action)

    for target, keys in sorted(hook_keys.items()):
        _unmerge_hook_file(target, home, keys, report, hook_entries.get(target), dry_run)

    _cleanup_runtime_data(home, report, dry_run, {entry.path for entry in loaded.entries})

    if retained:
        retained.reverse()
        report.warn(f"{len(retained)} entr{'y' if len(retained) == 1 else 'ies'} kept in {manifest.REL_PATH} (backups still tracked)")
        if not dry_run:
            loaded.entries = retained
            loaded.settings_hooks = []
            loaded.gateway = {}
            loaded.state = manifest.COMPLETE
            loaded.save(home)
        return report

    report.add("removed", manifest.REL_PATH)
    if not dry_run:
        manifest.remove(home)
        _prune_created_dirs(home, loaded.created_dirs, kept_dirs)
    return report


def _prune_created_dirs(home: Path, created: list[str], kept: set[Path]) -> None:
    for rel in sorted(set(created), key=lambda r: len(Path(r).parts), reverse=True):
        path = home / rel
        if path in kept or path.is_symlink() or not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            continue


def _cleanup_runtime_data(home: Path, report: Report, dry_run: bool, tracked: set[str]) -> None:
    """Drop transient state; list (never delete) other aicj/ data that no manifest entry owns."""
    aicj = home / ".claude" / "aicj"
    state = aicj / "state"
    if state.exists() or state.is_symlink():
        report.add("removed", ".claude/aicj/state", "runtime state")
        if not dry_run:
            if state.is_dir() and not state.is_symlink():
                shutil.rmtree(state)
            else:
                state.unlink()
    if not aicj.is_dir():
        return
    for child in sorted(aicj.iterdir()):
        rel = child.relative_to(home).as_posix()
        if child.name == "state" or rel == manifest.REL_PATH:
            continue
        if any(path == rel or path.startswith(rel + "/") for path in tracked):
            continue
        if child.is_dir() and not child.is_symlink() and not any(child.rglob("*")):
            continue
        report.note(f"保留的运行数据：{rel}（可手动删除）")


def run_status(home: Path, source: Path | None) -> Report:
    report = Report()
    loaded = manifest.load(home)
    if loaded is None:
        report.note("not installed (no manifest)")
        return report
    if loaded.state == manifest.IN_PROGRESS:
        report.note("install was interrupted; rerun install to finish")

    root = source or (Path(loaded.source_repo) if loaded.source_repo else None)
    hud_status, hud_reason = hud.status(home, loaded.hud)
    report.add(hud_status, "claude-hud", hud_reason)
    if root is None or not root.is_dir():
        report.note(f"source root unavailable: {root}")
        return report

    idx = index.load_index(root, loaded.options.packs, loaded.options.adapters)
    seen: set[str] = set()
    for item in idx.files:
        seen.add(item.target)
        target = home / item.target
        if not target.exists() and not target.is_symlink():
            report.add("missing", item.target, str(item.source))
            continue
        if conflict.classify(item.source, target, False) in (conflict.ADOPTED, conflict.ADOPTED_SYMLINK):
            report.add("installed", item.target, str(item.source))
        else:
            report.add("differs", item.target, f"diff {item.source} {target}")
    for entry in loaded.entries:
        if entry.path in seen or entry.kind in ("md-block", SKILL_DIR):
            continue
        target = home / entry.path
        state = "installed" if target.exists() or target.is_symlink() else "missing"
        report.add(state, entry.path, entry.action)
    report.note(f"installed_at {loaded.installed_at}")
    report.note(f"options {loaded.options.packs} adapters={loaded.options.adapters}")
    return report
