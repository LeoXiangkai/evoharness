#!/usr/bin/env python3
"""PreToolUse(Bash) hook: gate `git push` for commits not covered by a recorded code-review outcome.

AICJ_HOOK_MODE (default warn): warn prints the deny text as a hint and allows the push
(exit 0); block rejects it (exit 2). Internal errors also reject in block mode and warn
then allow in warn mode. The hook reads tool_input.command and cwd from stdin JSON.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

try:
    from hook_mode import hook_mode as get_hook_mode
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from hook_mode import hook_mode as get_hook_mode

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

LOG_DIR_ENV = "AICJ_REVIEW_LOG_DIR"
CLAUDE_DIR_ENV = "AICJ_CLAUDE_DIR"
GATE_LOG_FILE = "code-review-gates.jsonl"

SCRIPT_DIR = Path(__file__).resolve().parent
GATE_LOG_HELPER = SCRIPT_DIR / "gate_log.py"


def claude_dir() -> Path:
    """AICJ_CLAUDE_DIR wins; otherwise derive from this script (hooks/aicj/<x>.py -> parents[2])."""
    env = os.environ.get(CLAUDE_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return SCRIPT_DIR.parents[1]


def scope_script() -> Path:
    return claude_dir() / "skills" / "code-review" / "scripts" / "scope.py"


def review_log_dir() -> Path:
    env = os.environ.get(LOG_DIR_ENV)
    if env:
        return Path(env).expanduser().resolve()
    return (claude_dir() / "aicj" / "review-logs").resolve()


def hook_mode() -> str:
    return get_hook_mode()


def deny_or_warn(message: str) -> int:
    """block mode keeps the deny (exit 2); warn mode prints the same text as a hint and allows."""
    if hook_mode() == "block":
        sys.stderr.write(message + "\n")
        return 2
    sys.stderr.write(
        "[push-review-gate] AICJ_HOOK_MODE=warn，本次放行；block 模式（AICJ_HOOK_MODE=block 或 install --strict）将拒绝：\n"
        + message + "\n"
    )
    return 0


def internal_or_warn(message: str) -> int:
    if hook_mode() == "block":
        sys.stderr.write(message + "\n")
        return 2
    sys.stderr.write("[push-review-gate] AICJ_HOOK_MODE=warn，本次放行；\n" + message + "\n")
    return 0

GIT_GLOBAL_OPTS_WITH_ARG = frozenset((
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
    "--config-env", "--attr-source",
))
GIT_GLOBAL_OPTS_NO_ARG = frozenset((
    "-p", "-P", "--paginate", "--no-pager", "--no-replace-objects", "--bare",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs", "--no-optional-locks", "--no-advice", "--no-lazy-fetch",
))

PUSH_OPTS_WITH_ARG = frozenset((
    "-o", "--push-option", "--repo", "--receive-pack", "--exec",
))


class GateDenyError(Exception):
    pass


class _UnresolvableVariable(Exception):
    pass


def _to_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return 0


def _expand_vars(text: str, env: dict[str, str]) -> str | None:
    """Expand $VAR and ${VAR}. Return None if any variable is unset/empty/unparseable."""

    def repl(match: re.Match[str]) -> str:
        brace_expr = match.group(1)
        bare_name = match.group(2)
        if brace_expr is not None:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", brace_expr):
                raise _UnresolvableVariable()
            name = brace_expr
        else:
            name = bare_name  # type: ignore[assignment]
        if name not in env:
            raise _UnresolvableVariable()
        return env[name]

    try:
        result = re.sub(r"\$\{([^}]+)\}|\$([A-Za-z_][A-Za-z0-9_]*)", repl, text)
    except _UnresolvableVariable:
        return None
    if result == "":
        return None
    return result


def log_event(event: str, rule: str, raw_input: str) -> None:
    try:
        subprocess.run(
            [sys.executable, str(GATE_LOG_HELPER), "push-review-gate.py", event, rule, raw_input],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def eval_log_dir() -> Path:
    return review_log_dir()


def git_text(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip() or f"git {' '.join(args)} failed")
    return result.stdout.decode("utf-8", errors="replace").strip()


def git_common_dir(repo: Path) -> Path | None:
    try:
        raw = git_text(repo, "rev-parse", "--git-common-dir")
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = repo / candidate
        return candidate.resolve()
    except Exception:
        return None


def nul_paths(raw: bytes) -> list[str]:
    return [part.decode("utf-8", errors="surrogateescape") for part in raw.split(b"\0") if part]


def split_command(command: str) -> list[list[str]]:
    """Split on unquoted newlines, &, ;, || and | while preserving quoted text."""
    out = []
    quote = None
    escaped = False
    for char in command:
        if escaped:
            out.append(char)
            escaped = False
        elif char == "\\":
            out.append(char)
            escaped = True
        elif quote:
            out.append(char)
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
            out.append(char)
        elif char == "\n":
            out.append(";")
        else:
            out.append(char)
    command = "".join(out)
    segments: list[list[str]] = [[]]
    lexer = shlex.shlex(command, punctuation_chars=True, posix=True)
    lexer.whitespace_split = True
    for token in lexer:
        if token in ("&&", "&", ";", "||", "|"):
            if segments[-1]:
                segments.append([])
        else:
            segments[-1].append(token)
    return [seg for seg in segments if seg]


def parse_ref(spec: str) -> tuple[str, str | None]:
    spec = spec.lstrip("+")
    if ":" in spec:
        src, dst = spec.split(":", 1)
    else:
        src, dst = spec, None
    return src, dst


def resolve_current_branch(repo: Path) -> str:
    branch = git_text(repo, "branch", "--show-current", check=False)
    if branch:
        return branch
    head = git_text(repo, "rev-parse", "--abbrev-ref", "HEAD")
    return head


def _is_assignment(word: str) -> bool:
    if "=" not in word or word.startswith("="):
        return False
    name, sep, value = word.partition("=")
    if name in ("", "="):
        return False
    if name == "export":
        return False
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return False
    # Reject values with command substitution or backticks
    if "$(" in value or "`" in value:
        return False
    return True


def _assignment_name_value(word: str) -> tuple[str, str]:
    name, _sep, value = word.partition("=")
    return name, value


def _is_export_assignment(seg: list[str]) -> tuple[str, str] | None:
    if len(seg) == 1:
        word = seg[0]
        if word.startswith("export "):
            rest = word[len("export "):].strip()
            if _is_assignment(rest):
                return _assignment_name_value(rest)
    if len(seg) >= 2 and seg[0] == "export":
        rest = "=".join(seg[1:])
        if _is_assignment(rest):
            return _assignment_name_value(rest)
    return None


def _resolve_path(text: str, base_cwd: Path | None) -> Path | None:
    """Expand user, resolve relative to base_cwd, and require the path to exist."""
    # Git Bash on Windows sets HOME, but os.path.expanduser there reads USERPROFILE
    home = os.environ.get("HOME")
    if home and (text == "~" or text.startswith(("~/", "~\\"))):
        expanded = home + text[1:]
    else:
        expanded = os.path.expanduser(text)
    path = Path(expanded)
    if not path.is_absolute():
        if base_cwd is not None:
            path = base_cwd / path
        else:
            path = Path.cwd() / path
    try:
        resolved = path.resolve()
    except (OSError, ValueError):
        return None
    if not resolved.exists():
        return None
    return resolved


def _resolve_repo_dir(candidate: Path) -> Path:
    candidate = candidate.expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    result = subprocess.run(
        ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        raise GateDenyError(f"not a git repository: {candidate}")
    return Path(result.stdout.decode("utf-8", errors="replace").strip()).resolve()


def _parse_push_args(push_args: list[str]) -> tuple[str, list[str]]:
    """Return (remote, refspecs). An empty refspecs list means the push should be skipped."""
    remote = "origin"
    remote_set = False
    refspecs: list[str] = []
    dry_run = False
    delete = False
    tags_seen = False
    all_branches = False
    i = 0
    while i < len(push_args):
        arg = push_args[i]
        if arg == "--":
            i += 1
            break
        if arg.startswith("--"):
            lower = arg.lower()
            if lower in ("--dry-run", "-n"):
                dry_run = True
            elif lower in ("--delete", "-d"):
                delete = True
            elif lower == "--tags":
                tags_seen = True
            elif lower in ("--all", "--branches"):
                all_branches = True
            elif lower in PUSH_OPTS_WITH_ARG:
                i += 2
                continue
            i += 1
        elif arg.startswith("-"):
            saw_consume = False
            for ch in arg[1:]:
                if ch == "n":
                    dry_run = True
                elif ch == "d":
                    delete = True
                elif ch == "o":
                    saw_consume = True
            i += 1
            if saw_consume and i < len(push_args):
                i += 1
        elif "=" not in arg:
            if not remote_set:
                remote = arg
                remote_set = True
                i += 1
            else:
                refspecs.append(arg)
                i += 1
        else:
            refspecs.append(arg)
            i += 1
    while i < len(push_args) and push_args[i] != "--":
        refspecs.append(push_args[i])
        i += 1
    if dry_run or delete:
        return remote, []
    if tags_seen and not refspecs and not all_branches:
        return remote, []
    if all_branches:
        refspecs = [
            line.strip()
            for line in git_text(Path.cwd(), "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
            if line.strip()
        ]
    if not refspecs:
        refspecs.append("HEAD")
    return remote, [r for r in refspecs if r]


def _extract_push_segments(
    segments: list[list[str]],
    initial_cwd: Path,
    env: dict[str, str] | None = None,
) -> list[tuple[Path, str, list[str]]]:
    """Single-pass, ordered walk of command segments.

    Maintains (cwd | None, env). Only push segments can raise GateDenyError.
    Non-push segments that fail to resolve a path mark cwd as unknown (None).
    """
    cwd = initial_cwd
    local_env = dict(env or os.environ)
    pushes: list[tuple[Path, str, list[str]]] = []

    for seg in segments:
        # Standalone cd
        if seg and seg[0] == "cd":
            if len(seg) == 1:
                target = "~"
            elif len(seg) == 2:
                target = seg[1]
            else:
                cwd = None
                continue
            expanded = _expand_vars(target, local_env)
            if expanded is None or expanded == "-":
                cwd = None
            else:
                cwd = _resolve_path(expanded, cwd)
            continue

        # Standalone export / assignment: update persistent env
        export_assign = _is_export_assignment(seg)
        if export_assign is not None:
            name, value = export_assign
            expanded = _expand_vars(value, local_env)
            local_env[name] = expanded if expanded is not None else ""
            continue

        if len(seg) == 1 and _is_assignment(seg[0]):
            name, value = _assignment_name_value(seg[0])
            expanded = _expand_vars(value, local_env)
            local_env[name] = expanded if expanded is not None else ""
            continue

        # Inline env assignments for a command (e.g. VAR=x git push)
        seg_env = local_env
        w = 0
        while w < len(seg) and _is_assignment(seg[w]):
            name, value = _assignment_name_value(seg[w])
            expanded = _expand_vars(value, local_env)
            if expanded is not None:
                seg_env = {**seg_env, name: expanded}
            w += 1

        words = seg[w:]
        if not words:
            continue
        while words and words[0] in ("(", "{"):
            words = words[1:]
        while words and words[0] in ("command", "exec", "nohup", "sudo", "time"):
            words = words[1:]
            while words and _is_assignment(words[0]):
                words = words[1:]
        if words and words[0] == "env":
            words = words[1:]
            while words and (_is_assignment(words[0]) or words[0].startswith("-")):
                words = words[1:]
        while words and words[0] in ("command", "exec", "nohup", "sudo", "time"):
            words = words[1:]
        if not words:
            continue
        if os.path.basename(words[0]) in ("bash", "sh", "zsh") and len(words) >= 3 and words[1] == "-c":
            pushes.extend(_extract_push_segments(split_command(words[2]), cwd, seg_env))
            continue
        if words[0] == "eval" and len(words) >= 2:
            pushes.extend(_extract_push_segments(split_command(words[1]), cwd, seg_env))
            continue
        program = os.path.basename(words[0])
        if program not in ("git", "git-safe"):
            continue

        # Parse git global options; -C affects only this segment.
        effective_cwd = cwd
        idx = 1
        while idx < len(words):
            arg = words[idx]
            if arg in GIT_GLOBAL_OPTS_WITH_ARG:
                if idx + 1 < len(words):
                    val = words[idx + 1]
                    expanded = _expand_vars(val, seg_env)
                    if arg == "-C":
                        if expanded is None:
                            effective_cwd = None
                        else:
                            effective_cwd = _resolve_path(expanded, cwd)
                    elif expanded is None:
                        effective_cwd = None
                idx += 2
            elif arg in GIT_GLOBAL_OPTS_NO_ARG or (arg.startswith("--") and "=" in arg):
                idx += 1
            else:
                break

        if idx >= len(words) or words[idx] != "push":
            continue

        # Push segment: an unresolved path is a hard deny.
        if effective_cwd is None:
            raise GateDenyError("cannot resolve push repository path")
        repo_root = _resolve_repo_dir(effective_cwd)
        remote, refspecs = _parse_push_args(words[idx + 1 :])
        if refspecs:
            pushes.append((repo_root, remote, refspecs))

    return pushes


def _remove_quoted(text: str) -> str:
    out: list[str] = []
    quote: str | None = None
    escaped = False
    for char in text:
        if escaped:
            escaped = False
            out.append(" ")
        elif char == "\\":
            escaped = True
            out.append(" ")
        elif quote:
            if char == quote:
                quote = None
            out.append(" ")
        elif char in "'\"":
            quote = char
            out.append(" ")
        else:
            out.append(char)
    return "".join(out)


def _fallback_has_git_push(text: str) -> bool:
    """True when some shell segment has a git token whose subcommand is exactly `push`.

    `text` must already have quoted content removed. Git global options are skipped,
    so `git branch feature/push-x` or `git log origin/push-fix` do not match.
    """
    for seg in re.split(r"[;&|\n]", text):
        words = [w.strip("$({`)}") for w in seg.split()]  # `(git push)`, `$(git push)`
        for i, word in enumerate(words):
            name = os.path.basename(word).lower()
            if name.removesuffix(".exe") not in ("git", "git-safe"):
                continue
            j = i + 1
            while j < len(words) and words[j].startswith("-"):
                j += 2 if words[j] in GIT_GLOBAL_OPTS_WITH_ARG else 1
            if j < len(words) and words[j] == "push":
                return True
    return False


def blobs_at_ref(repo: Path, ref: str, files: list[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    if not files:
        return result
    proc = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", ref, "--", *files],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip() or f"ls-tree {ref} failed")
    lines = nul_paths(proc.stdout)
    for line in lines:
        # Format: <mode> <type> <oid>\t<path>
        tab_idx = line.find("\t")
        if tab_idx == -1:
            continue
        meta = line[:tab_idx]
        path = line[tab_idx + 1:]
        meta_parts = meta.split()
        if len(meta_parts) >= 3:
            result[path] = meta_parts[2]
    for f in files:
        if f not in result:
            result[f] = None
    return result


def read_outcome_records(log_path: Path) -> list[dict]:
    records: list[dict] = []
    if not log_path.exists():
        return records
    try:
        with open(log_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("kind") == "outcome":
                    records.append(record)
    except Exception:
        pass
    return records


def deny_message(repo_root: Path, ref: str, gate: str, risk_signals: list[str], uncovered: list[str]) -> str:
    lines: list[str] = []
    lines.append(
        f"[push-review-gate] 拒绝 push：{repo_root} {ref} 有未经 code-review 的改动（gate={gate}）"
    )
    if risk_signals:
        lines.append("risk_signals：" + ", ".join(risk_signals))
    display = uncovered[:20]
    lines.append(f"未覆盖文件（共 {len(uncovered)} 个）：")
    lines.extend(display)
    if len(uncovered) > len(display):
        lines.append(f"... 还有 {len(uncovered) - len(display)} 个文件未列出")
    lines.append(
        f"请在 {repo_root} 的 {ref} 分支跑 /code-review（scope 会取未推送区间），"
        "收尾按 SKILL 用 `scope.py --target <repo> --outcome ...` 记录后重试 push；"
        "不得伪造 outcome。确认要跳过时请在提示框执行 `! git push` 手动推。"
    )
    return "\n".join(lines)


def check_push(repo: Path, ref: str, remote_ref: str | None, raw_input: str, remote: str = "origin") -> tuple[bool, str]:
    if remote_ref is None:
        pending = git_text(repo, "rev-list", ref, "--not", f"--remotes={remote}", check=False)
        if not pending:
            if git_text(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}", check=False):
                return True, ""
            return False, f"[push-review-gate] 无法计算 {ref} 的待推送提交范围"
        oldest = pending.splitlines()[-1]
        base = git_text(repo, "rev-parse", f"{oldest}^", check=False)
        if not base:
            base = git_text(repo, "hash-object", "-t", "tree", "/dev/null")
    else:
        base = git_text(repo, "merge-base", ref, remote_ref, check=False)
        if not base:
            return False, f"[push-review-gate] 无法计算 {ref} 的待推送提交范围"
    head = git_text(repo, "rev-parse", f"{ref}^{{commit}}")
    if base == head:
        return True, ""
    files = sorted(nul_paths(
        subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", "-z", base, head],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    ))
    if not files:
        return True, ""
    head_blobs = blobs_at_ref(repo, head, files)
    common_dir = git_common_dir(repo)
    if common_dir is None:
        return True, ""
    log_path = eval_log_dir() / GATE_LOG_FILE
    records = read_outcome_records(log_path)
    covered_blobs: dict[str, set[str | None]] = {}
    for record in records:
        if record.get("common_dir") != str(common_dir):
            continue
        reviewers = _to_int(record.get("reviewers", 0))
        review_gate = record.get("review_gate", "")
        if reviewers < 1 and review_gate != "skip":
            continue
        reviewed_blobs = record.get("reviewed_blobs")
        if not isinstance(reviewed_blobs, dict):
            continue
        for path, blob in reviewed_blobs.items():
            covered_blobs.setdefault(path, set()).add(blob)
    uncovered = [f for f in files if head_blobs.get(f) not in covered_blobs.get(f, set())]
    if not uncovered:
        return True, ""

    sys.path.insert(0, str(scope_script().parent))
    # importing scope would drop a __pycache__ next to the installed skill script
    dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        import scope as scope_mod
        head_oid = head
        changed_lines = scope_mod.changed_line_count(repo, base, set(), head=head_oid, paths=uncovered)
        risks, _risk_details = scope_mod.detect_risks(repo, base, uncovered, set(), head=head_oid)
        gate, _max_reviewers = scope_mod.review_budget(len(uncovered), changed_lines, risks)
    finally:
        sys.dont_write_bytecode = dont_write_bytecode
        sys.path.pop(0)

    if gate == "skip":
        return True, ""

    message = deny_message(repo, ref, gate, risks, uncovered)
    log_event("deny", message.splitlines()[0], raw_input)
    return False, message


def main() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8")
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            return 0
        if not isinstance(event, dict):
            return 0
        if event.get("tool_name") != "Bash":
            return 0
        tool_input = event.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return 0
        command = tool_input.get("command")
        if not isinstance(command, str):
            return 0
        cwd = event.get("cwd") or tool_input.get("cwd") or os.getcwd()
        cwd = Path(str(cwd)).expanduser().resolve()
        segments = split_command(command)
        try:
            pushes = _extract_push_segments(segments, cwd)
        except GateDenyError as e:
            message = (
                "[push-review-gate] 无法解析 push 所在仓库，请在 push 命令里用字面绝对路径（-C /abs/path）"
            )
            code = deny_or_warn(message + f"\n({e})")
            log_event("deny", "unresolved-repo-path", raw)
            return code
        if not pushes:
            fallback_text = _remove_quoted(command)
            if _fallback_has_git_push(fallback_text) and not re.search(r"(?:--dry-run|\s-n(?:\s|$))", fallback_text):
                return deny_or_warn("[push-review-gate] 无法解析 git push 命令")
            return 0
        for repo, remote, refspecs in pushes:
            if not repo.is_dir():
                continue
            try:
                root = git_text(repo, "rev-parse", "--show-toplevel")
                repo_root = Path(root).resolve()
            except Exception:
                continue
            parsed = []
            for spec in refspecs:
                src, dst = parse_ref(spec)
                if src == "":
                    continue
                current_branch = resolve_current_branch(repo_root)
                if dst is None:
                    dst = src if src not in ("HEAD", ".") else current_branch
                    if dst == "HEAD":
                        dst = current_branch
                local_ref = current_branch if src in ("HEAD", "") else src
                parsed.append((local_ref, dst))
            if not parsed:
                continue
            for local_ref, dst in parsed:
                remote_ref: str | None = None
                upstream = git_text(
                    repo_root,
                    "rev-parse",
                    "--abbrev-ref",
                    f"{local_ref}@{{upstream}}",
                    check=False,
                )
                if upstream:
                    parts = upstream.split("/", 1)
                    if parts and parts[0] == remote:
                        remote_ref = upstream
                if remote_ref is None:
                    candidate = f"{remote}/{dst}"
                    if subprocess.run(
                        ["git", "-C", str(repo_root), "rev-parse", "--verify", candidate],
                        check=False,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    ).returncode == 0:
                        remote_ref = candidate
                ok, message = check_push(repo_root, local_ref, remote_ref, raw, remote)
                if not ok:
                    return deny_or_warn(message)
        return 0
    except Exception as e:
        message = f"[push-review-gate] 内部异常，无法完成 push 检查：{e} (required: {scope_script()})"
        log_event("error", str(e), raw if "raw" in dir() else "")
        return internal_or_warn(message)


if __name__ == "__main__":
    raise SystemExit(main())
