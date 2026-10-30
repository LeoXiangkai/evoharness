#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

from lib import doctor as doctor_mod
from lib import telemetry as telemetry_mod
from lib import engine, index, manifest, release
from lib import selftest as selftest_mod
from lib.util import UserError, abspath
from lib import wsl

REPO_ROOT = Path(__file__).resolve().parents[1]

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aicj", description="ai-coding-java global installer", allow_abbrev=False)
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser("install", help="install components into the global home", allow_abbrev=False)
    _common(install)
    _scope(install)
    install.add_argument("--packs", default="auto", help="auto or comma separated pack names")
    install.add_argument("--adapters", default="", help="comma separated adapter names")
    install.add_argument("--codex", action="store_true", help="also link skills for codex")
    install.add_argument("--codex-hooks", action="store_true", help="also merge hooks into codex hooks.json")
    install.add_argument("--strict", action="store_true", help="run blocking hooks in block mode")
    install.add_argument(
        "--enable-hook",
        action="append",
        default=[],
        metavar="NAME",
        dest="enable_hook",
        help="also register an optional hook by name (repeatable; default registers none)",
    )
    install.add_argument("--link", action="store_true", help="symlink instead of copy")
    install.add_argument("--on-conflict", choices=("skip", "backup"), default="skip")
    install.add_argument("--no-hud", action="store_true", help="do not install the claude-hud status line")
    install.add_argument("--team-config", default="", metavar="URL|PATH",
                         help="join team telemetry: http(s)/file:// URL or path of a team.json")
    install.add_argument("--channel", choices=("stable", "beta", "auto"), default="",
                         help="record the release channel (auto clears the member override); `upgrade` switches code")
    install.add_argument("--dry-run", action="store_true")

    upgrade = sub.add_parser("upgrade", help="move the source checkout to the channel target and reinstall", allow_abbrev=False)
    _common(upgrade)
    upgrade.add_argument("--channel", choices=("stable", "beta", "auto"), default="")
    upgrade.add_argument("--check", action="store_true", help="print current -> target only; change nothing")
    upgrade.add_argument("--no-install", action="store_true", help="update the checkout but skip the reinstall")
    upgrade.add_argument("--team-config", default="", metavar="URL|PATH",
                         help="read the team default channel from this team.json (first run, before team.json is installed)")

    uninstall = sub.add_parser("uninstall", help="remove what the manifest records as ours", allow_abbrev=False)
    _common(uninstall)
    _scope(uninstall)
    uninstall.add_argument("--dry-run", action="store_true")

    status = sub.add_parser("status", help="read-only install report", allow_abbrev=False)
    _common(status)

    check = sub.add_parser("doctor", help="read-only health report", allow_abbrev=False)
    _common(check)

    selftest = sub.add_parser("selftest", help="verify installed components in disposable repositories", allow_abbrev=False)
    _common(selftest)
    selftest.add_argument("--with-claude", action="store_true", help="also run the Claude Code end-to-end check")
    return parser


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", help="override home directory")
    parser.add_argument("--source", help="component root (default: this repository)")


def _scope(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--global", dest="global_install", action="store_true", help="global install (default)")
    group.add_argument("--project", help="project archive directory")


def _home(args: argparse.Namespace) -> Path:
    return abspath(args.home) if args.home else Path.home()


def _source(args: argparse.Namespace, default: Path | None) -> Path | None:
    if args.source:
        return abspath(args.source)
    return default


def _print_report(title: str, report: engine.Report, dry_run: bool) -> None:
    suffix = " (dry-run, nothing written)" if dry_run else ""
    print(f"=== {title}{suffix} ===")
    for verb, path, note in report.lines:
        line = f"{verb:<16} {path}"
        if note:
            line += f"  {note}"
        print(line)
    for note in report.notes:
        print(f"note             {note}")
    for warning in report.warnings:
        print(f"warning          {warning}")
    counts = Counter(verb for verb, _path, _note in report.lines)
    summary = " ".join(f"{name}={counts[name]}" for name in sorted(counts)) or "nothing"
    print(f"summary          {summary}")


def cmd_install(args: argparse.Namespace) -> int:
    home = _home(args)
    source = _source(args, REPO_ROOT)
    if source is None or not source.is_dir():
        raise UserError(f"source root not found: {source}")
    if args.project:
        print(f"project layer {args.project}: no project entries in this build")
        return 0

    packs = index.resolve_packs(args.packs, Path.cwd(), index.available_names(source, "pack"), not args.project)
    adapters = []
    seen_adapters = set()
    for part in args.adapters.split(","):
        name = part.strip()
        key = name.casefold()
        if name and key not in seen_adapters:
            adapters.append(name)
            seen_adapters.add(key)
    if args.team_config.strip() and "lesson" not in seen_adapters:
        adapters.append("lesson")  # rework-cause telemetry reads the lesson adapter's corrections log
    options = manifest.Options(
        packs=packs,
        adapters=adapters,
        codex=args.codex,
        codex_hooks=args.codex_hooks,
        strict=args.strict,
        link=args.link,
        on_conflict=args.on_conflict,
        enable_hooks=list(args.enable_hook),
        no_hud=args.no_hud,
        team_config=args.team_config,
        channel_request=args.channel,
    )
    report, _result = engine.run_install(home, source, options, args.dry_run)
    _print_report("install", report, args.dry_run)
    return 0


def _install_command(repo: Path, home: Path, old: manifest.Manifest, channel_arg: str) -> list[str]:
    options = old.options
    cmd = [sys.executable, str(repo / "installer" / "aicj.py"), "install", "--home", str(home),
           "--source", str(repo), "--channel", channel_arg]
    if options.packs:
        cmd += ["--packs", ",".join(options.packs)]
    if options.adapters:
        cmd += ["--adapters", ",".join(options.adapters)]
    for flag, on in (("--codex", options.codex), ("--codex-hooks", options.codex_hooks), ("--strict", options.strict),
                     ("--link", options.link), ("--no-hud", options.no_hud)):
        if on:
            cmd.append(flag)
    cmd += ["--on-conflict", options.on_conflict if options.on_conflict in ("skip", "backup") else "skip"]
    for name in options.enable_hooks:
        cmd += ["--enable-hook", name]
    return cmd


def cmd_upgrade(args: argparse.Namespace) -> int:
    home = _home(args)
    repo = _source(args, REPO_ROOT)
    if repo is None or not repo.is_dir():
        raise UserError(f"source root not found: {repo}")
    old = manifest.load(home)
    if old is None and not args.check and not args.no_install:
        raise UserError("not installed (no manifest); run `aicj install` first")
    team_bytes = telemetry_mod.fetch_team_config(args.team_config) if args.team_config.strip() \
        else telemetry_mod.existing_team_bytes(home)
    channel, override = release.resolve_channel(args.channel, old.options.channel_override if old else "",
                                                release.team_channel(team_bytes))
    real = not args.check
    try:
        release.require_checkout(repo)
        release.fetch(repo)
        warnings: list[str] = []
        target = release.resolve_target(repo, channel, warnings)
        current_commit = release.local_commit(repo)
        current_version = release.read_version(repo)
        downgrade = release.is_downgrade(repo, current_commit, target.commit)
        for text in warnings:
            print(f"warning: {text}", file=sys.stderr)
        before = release.display_version(current_version, channel, release.short_commit(current_commit))
        after = release.display_version(target.version, channel, release.short_commit(target.commit))
        print(f"upgrade ({channel}): {before} [{release.short_commit(current_commit)}] -> "
              f"{after} [{release.short_commit(target.commit)}]" + ("  降级" if downgrade else "")
              + ("  (无变化)" if current_commit == target.commit else ""))
        if args.check:
            return 0
        release.checkout(repo, target)
        if args.no_install:
            return 0
        if old is None:
            raise UserError("not installed (no manifest); run `aicj install` first")
        cmd = _install_command(repo, home, old, override or "auto")
        if args.team_config.strip():
            cmd += ["--team-config", args.team_config]  # the reinstall must resolve the same team default
        result = subprocess.run(cmd)
        telemetry_mod.emit_install(home, repo, "upgrade", result.returncode == 0)
        return result.returncode
    except UserError as exc:
        if real:
            telemetry_mod.emit_install(home, repo, "upgrade", False, error_kind="UserError")
        raise


def cmd_uninstall(args: argparse.Namespace) -> int:
    if args.project:
        print(f"project layer {args.project}: no project entries in this build")
        return 0
    report = engine.run_uninstall(_home(args), args.dry_run)
    _print_report("uninstall", report, args.dry_run)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    report = engine.run_status(_home(args), _source(args, None))
    _print_report("status", report, False)
    return 0


NEXT_STEPS = (
    "初始化目标项目：/setup-ai-coding",
    "不知道下一步做什么：/flow",
    "只有粗糙想法：/req-intake",
)


def _manifest_source(home: Path) -> Path | None:
    try:
        loaded = manifest.load(home)
    except UserError:
        return None
    return Path(loaded.source_repo) if loaded and loaded.source_repo else None


def cmd_doctor(args: argparse.Namespace) -> int:
    home = _home(args)
    checks, code = doctor_mod.run_doctor(home, _source(args, None))
    print("=== doctor ===")
    for status, name, detail in checks:
        print(f"{status:<8} {name}  {detail}")
    counts = Counter(status for status, _name, _detail in checks)
    print("summary          " + " ".join(f"{name}={counts[name]}" for name in sorted(counts)))
    for line in release.framework_lines(home, _source(args, None) or _manifest_source(home)):
        print(line)
    telemetry_mod.emit_install(home, _source(args, None), "doctor", code == 0,
                               failed_checks=[name for status, name, _d in checks if status == "MISSING"])
    if code == 0:
        for text in NEXT_STEPS:
            print(f"下一步  {text}")
    gap = doctor_mod.optional_skill_gap(home)
    if gap:
        print(f"hint    {gap}")
    return code


def cmd_selftest(args: argparse.Namespace) -> int:
    return selftest_mod.run_selftest(_home(args), _source(args, None), args.with_claude)


def _replace_path_arguments(argv: list[str], values: dict[str, str]) -> tuple[list[str], bool]:
    converted: list[str] = []
    home_seen = False
    index = 0
    while index < len(argv):
        token = argv[index]
        matched = False
        for option, value in values.items():
            if token == option:
                converted.extend((option, value))
                home_seen = home_seen or option == "--home"
                index += 2
                matched = True
                break
            prefix = option + "="
            if token.startswith(prefix):
                converted.append(prefix + value)
                home_seen = home_seen or option == "--home"
                index += 1
                matched = True
                break
        if not matched:
            converted.append(token)
            index += 1
    return converted, home_seen


def _maybe_handoff(argv: list[str], args: argparse.Namespace) -> int | None:
    if not wsl.is_wsl():
        return None

    home = _home(args).resolve()
    source = (abspath(args.source) if args.source else REPO_ROOT).resolve()
    if wsl.windows_mount(source):
        print(
            "warning: WSL 仓库位于 Windows 盘，读写较慢，换行符与权限位可能出问题；"
            "建议克隆到 WSL 文件系统（如 ~/src）。",
            file=sys.stderr,
        )

    if "AICJ_WSL_HANDOFF" in os.environ or not wsl.windows_mount(home):
        return None

    found = wsl.find_windows_python()
    if found is None:
        raise wsl.windows_python_missing_error()
    win_python, prefix = found
    paths = {
        "--home": wsl.wslpath_windows(home),
        "--source": wsl.wslpath_windows(source) if args.source else "",
        "--project": wsl.wslpath_windows(abspath(args.project).resolve()) if getattr(args, "project", None) else "",
    }
    paths = {option: value for option, value in paths.items() if value}
    forwarded, home_seen = _replace_path_arguments(argv, paths)
    if "--link" in forwarded:
        forwarded = [token for token in forwarded if token != "--link"]
        print(
            "WSL 转交到 Windows 时不支持 --link（源在 \\wsl.localhost 下，发行版关闭即失效），已改为复制安装",
            file=sys.stderr,
        )
    if not home_seen:
        forwarded.extend(("--home", paths["--home"]))
    win_script = wsl.wslpath_windows(Path(__file__).resolve())
    print(f"检测到 WSL 且目标为 Windows 目录，已转交 Windows Python：{win_python}", file=sys.stderr)
    environment = {**os.environ, "AICJ_WSL_HANDOFF": "1"}
    result = subprocess.run([win_python, *prefix, win_script, *forwarded], env=environment)
    return result.returncode


COMMANDS = {
    "install": cmd_install,
    "upgrade": cmd_upgrade,
    "uninstall": cmd_uninstall,
    "status": cmd_status,
    "doctor": cmd_doctor,
    "selftest": cmd_selftest,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(raw_argv)
    try:
        handoff = _maybe_handoff(raw_argv, args)
        if handoff is not None:
            return handoff
        return COMMANDS[args.command](args)
    except UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
