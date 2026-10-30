from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from . import settings
from .util import is_windows, sha256_file, write_json

PLUGIN_NAME = "claude-hud@claude-hud"
MARKETPLACE_NAME = "claude-hud"
DEFAULT_CONFIG = {
    "language": "zh",
    "lineLayout": "expanded",
    "showSeparators": False,
    "pathLevels": 1,
    "gitStatus": {"enabled": True, "showDirty": True, "showAheadBehind": False, "showFileStats": False},
    "display": {
        "showModel": True,
        "showProject": True,
        "showAddedDirs": False,
        "showContextBar": True,
        "contextValue": "percent",
        "showTokenBreakdown": False,
        "showUsage": False,
        "showConfigCounts": False,
        "showCost": False,
        "showDuration": False,
        "showSpeed": False,
        "showTools": False,
        "showSkills": False,
        "showMcp": False,
        "showAgents": False,
        "showTodos": False,
        "showSessionName": False,
        "showEffortLevel": False,
    },
}


def _warn(report: Any, message: str) -> None:
    report.warn(f"claude-hud：{message}")


def _child_env(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    if home.resolve() != Path.home().resolve():
        env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    return env


def _runtime() -> str | None:
    names = ("node",) if is_windows() else ("bun", "node")
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def shell_name() -> str:
    if not is_windows():
        return "posix"
    configured = os.environ.get("CLAUDE_CODE_GIT_BASH_PATH", "").strip().strip('"')
    if configured and Path(configured).is_file():
        return "gitbash"
    git = shutil.which("git")
    if git:
        git_path = Path(git)
        if (git_path.parent / "bash.exe").is_file() or (git_path.parent.parent / "bin" / "bash.exe").is_file():
            return "gitbash"
    return "powershell"


def _run(command: list[str], env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _read_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _version_key(path: Path) -> tuple[int, ...] | None:
    parts = path.name.strip().split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def plugin_dir(claude_dir: Path) -> Path | None:
    root = claude_dir / "plugins/cache/claude-hud/claude-hud"
    try:
        if not root.is_dir():
            return None
        versions = [(key, path) for path in root.iterdir() if path.is_dir() and (key := _version_key(path)) is not None]
    except OSError:
        return None
    return max(versions, key=lambda item: (item[0], len(item[0])))[1] if versions else None


def runtime_artifacts(home: Path) -> dict[str, str]:
    """Return external Claude files that a plugin command may create, for safe cleanup."""
    claude_dir = home / ".claude"
    candidates = [claude_dir / ".claude.json", claude_dir / "plugins/.last_inuse_sweep"]
    candidates.extend(claude_dir.glob("settings.json.bak.*"))
    candidates.extend((claude_dir / "backups").glob(".claude.json.backup.*"))
    candidates.extend((claude_dir / "plugins").glob("installed_plugins.json"))
    candidates.extend((claude_dir / "plugins").glob("known_marketplaces.json"))
    result: dict[str, str] = {}
    for path in candidates:
        if path.is_file():
            try:
                result[path.relative_to(home).as_posix()] = sha256_file(path)
            except OSError:
                continue
    return result


def _base(previous: dict[str, Any] | None) -> dict[str, Any]:
    return dict(previous or {})


def install(home: Path, report: Any, dry_run: bool = False, disabled: bool = False, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    if disabled:
        report.note("claude-hud：已由 --no-hud 关闭")
        return _base(previous)
    if os.environ.get("AICJ_SKIP_HUD") == "1":
        # 测试专用：本机若有真实 claude，其它安装测试不应联网装插件
        report.note("claude-hud：AICJ_SKIP_HUD=1，已跳过")
        return _base(previous)
    old = _base(previous)
    result: dict[str, Any] = {
        "status": "skipped",
        "reason": "",
        "plugin_by_aicj": bool(old.get("plugin_by_aicj", False)),
        "marketplace_by_aicj": bool(old.get("marketplace_by_aicj", False)),
        "statusline_by_aicj": bool(old.get("statusline_by_aicj", False)),
        "config_by_aicj": bool(old.get("config_by_aicj", False)),
        "config_sha256": str(old.get("config_sha256", "")),
    }
    claude = shutil.which("claude")
    if not claude:
        result["reason"] = "未找到 claude 命令"
        _warn(report, result["reason"] + "，已跳过")
        return result
    runtime = _runtime()
    if not runtime:
        result["reason"] = "未找到 bun 或 node 运行时"
        _warn(report, result["reason"] + "，已跳过")
        return result
    claude_dir = home / ".claude"
    env = _child_env(home)
    if dry_run:
        result["status"] = "skipped"
        result["reason"] = "dry-run"
        report.note("claude-hud：计划检查插件、安装状态栏并写入默认配置（dry-run 未执行）")
        return result

    plugins = claude_dir / "plugins"
    installed_path = plugins / "installed_plugins.json"
    installed = _read_object(installed_path)
    if installed is None:
        result["reason"] = f"无法读取 {installed_path}"
        _warn(report, result["reason"] + "，已跳过")
        return result
    if PLUGIN_NAME not in installed:
        known_path = plugins / "known_marketplaces.json"
        known = _read_object(known_path)
        if known is None:
            result["reason"] = f"无法读取 {known_path}"
            _warn(report, result["reason"] + "，已跳过")
            return result
        if MARKETPLACE_NAME not in known:
            try:
                added = _run([claude, "plugin", "marketplace", "add", "jarrodwatts/claude-hud"], env, 180)
            except (OSError, subprocess.TimeoutExpired) as exc:
                result["reason"] = f"marketplace add 异常：{exc}"
                _warn(report, result["reason"] + "，已跳过")
                return result
            if added.returncode != 0:
                result["reason"] = f"marketplace add 失败（退出码 {added.returncode}）：{(added.stderr or added.stdout).strip()[:300]}"
                _warn(report, result["reason"] + "，已跳过")
                return result
            result["marketplace_by_aicj"] = True
        try:
            installed_result = _run([claude, "plugin", "install", PLUGIN_NAME], env, 180)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["reason"] = f"plugin install 异常：{exc}"
            _warn(report, result["reason"] + "，已跳过")
            return result
        if installed_result.returncode != 0:
            result["reason"] = f"plugin install 失败（退出码 {installed_result.returncode}）：{(installed_result.stderr or installed_result.stdout).strip()[:300]}"
            _warn(report, result["reason"] + "，已跳过")
            return result
        result["plugin_by_aicj"] = True

    version = plugin_dir(claude_dir)
    if version is None:
        result["reason"] = "未找到 claude-hud 插件版本目录"
        _warn(report, result["reason"] + "，已跳过")
        return result
    setup = version / "scripts/setup.mjs"
    if not setup.is_file():
        result["reason"] = f"未找到插件 setup.mjs：{setup}"
        _warn(report, result["reason"] + "，已跳过")
        return result
    shell = shell_name()
    try:
        inspected = _run([runtime, str(setup), "inspect", "--shell", shell], env, 60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["reason"] = f"setup inspect 异常：{exc}"
        _warn(report, result["reason"] + "，已跳过")
        return result
    if inspected.returncode != 0:
        result["reason"] = f"setup inspect 失败（退出码 {inspected.returncode}）：{(inspected.stderr or inspected.stdout).strip()[:300]}"
        _warn(report, result["reason"] + "，已跳过")
        return result
    try:
        inspection = json.loads(inspected.stdout)
    except json.JSONDecodeError:
        inspection = None
    if not isinstance(inspection, dict):
        result["reason"] = "setup inspect 未返回 JSON"
        _warn(report, result["reason"] + "，已跳过")
        return result
    existing = str(inspection.get("existing", "")).strip().casefold()
    if existing == "other":
        result["status"] = "kept-other"
        result["reason"] = "已有其它状态栏，未替换"
        _warn(report, result["reason"])
    elif existing == "claude-hud" and not result["statusline_by_aicj"]:
        # 用户自己配的 claude-hud 状态栏：不重装、不记归属，卸载时才不会删掉它
        report.note("claude-hud：已有 claude-hud 状态栏，保持不变")
    elif existing in ("none", "claude-hud"):
        if existing == "none":
            try:
                installed_line = _run([runtime, str(setup), "install", "--shell", shell], env, 60)
            except (OSError, subprocess.TimeoutExpired) as exc:
                result["reason"] = f"setup install 异常：{exc}"
                _warn(report, result["reason"] + "，已跳过")
                return result
            if installed_line.returncode != 0:
                result["reason"] = f"setup install 失败（退出码 {installed_line.returncode}）：{(installed_line.stderr or installed_line.stdout).strip()[:300]}"
                _warn(report, result["reason"] + "，已跳过")
                return result
        result["statusline_by_aicj"] = True
    else:
        result["reason"] = f"setup inspect 返回未知状态：{existing or '(空)'}"
        _warn(report, result["reason"] + "，已跳过")
        return result

    config_path = claude_dir / "plugins/claude-hud/config.json"
    wrote_config = False
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(config_path, DEFAULT_CONFIG)
        result["config_by_aicj"] = True
        wrote_config = True
    elif not config_path.is_file():
        result["reason"] = f"配置路径不是文件：{config_path}"
        _warn(report, result["reason"] + "，已跳过")
        return result
    try:
        current_hash = sha256_file(config_path)
        if wrote_config or not (result["config_by_aicj"] and old.get("config_sha256")):
            result["config_sha256"] = current_hash
    except OSError as exc:
        result["reason"] = f"无法读取配置：{exc}"
        _warn(report, result["reason"] + "，已跳过")
        return result
    if result["status"] != "kept-other":
        result["status"] = "installed"
    return result


def _statusline_command(payload: dict[str, Any]) -> str:
    value = payload.get("statusLine")
    if isinstance(value, dict):
        return str(value.get("command", ""))
    return str(value or "")


def uninstall(home: Path, record: dict[str, Any], report: Any, dry_run: bool = False) -> None:
    if not record:
        return
    claude_dir = home / ".claude"
    if record.get("statusline_by_aicj"):
        path = claude_dir / "settings.json"
        try:
            payload = settings.load(path)
            if "claude-hud" in _statusline_command(payload).strip().casefold():
                payload.pop("statusLine", None)
                if not dry_run:
                    settings.save(path, payload)
                report.add("removed", ".claude/settings.json", "claude-hud statusLine")
        except Exception as exc:
            _warn(report, f"删除 statusLine 失败：{exc}")
    config = claude_dir / "plugins/claude-hud/config.json"
    if record.get("config_by_aicj") and config.exists():
        try:
            current = sha256_file(config)
            if current == str(record.get("config_sha256", "")):
                if not dry_run:
                    config.unlink()
                report.add("removed", ".claude/plugins/claude-hud/config.json")
            else:
                report.note(".claude/plugins/claude-hud/config.json：已被修改，保留")
        except OSError as exc:
            _warn(report, f"删除配置失败：{exc}")
    if dry_run:
        report.note("claude-hud：计划删除状态栏、配置、插件和 marketplace（dry-run 未执行）")
        return
    claude = shutil.which("claude")
    env = _child_env(home)
    if claude and record.get("plugin_by_aicj"):
        try:
            result = _run([claude, "plugin", "uninstall", PLUGIN_NAME], env, 180)
            if result.returncode != 0:
                _warn(report, f"plugin uninstall 失败（退出码 {result.returncode}）：{(result.stderr or result.stdout).strip()[:300]}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            _warn(report, f"plugin uninstall 异常：{exc}")
    elif record.get("plugin_by_aicj"):
        _warn(report, "未找到 claude，无法卸载插件")
    if record.get("plugin_by_aicj"):
        cache = claude_dir / "plugins/cache/claude-hud"
        if cache.is_dir() and not dry_run:
            shutil.rmtree(cache, ignore_errors=True)
        for name in ("statusline.mjs", "statusline.cmd"):  # setup.mjs also writes the .cmd wrapper on Windows
            launcher = claude_dir / "plugins/claude-hud" / name
            if launcher.is_file() and not dry_run:
                launcher.unlink(missing_ok=True)
        for path in (claude_dir / "plugins/installed_plugins.json",):
            if path.is_file() and not dry_run:
                try:
                    payload = _read_object(path)
                    if payload is not None:
                        plugins = payload.get("plugins") if isinstance(payload.get("plugins"), dict) else payload
                        if isinstance(plugins, dict) and not plugins:
                            path.unlink()
                except OSError:
                    pass
        known = claude_dir / "plugins/known_marketplaces.json"
        if known.is_file() and not dry_run:
            try:
                payload = _read_object(known)
                if payload == {}:
                    known.unlink()
            except OSError:
                pass
    if claude and record.get("marketplace_by_aicj"):
        try:
            result = _run([claude, "plugin", "marketplace", "remove", MARKETPLACE_NAME], env, 180)
            if result.returncode != 0:
                _warn(report, f"marketplace remove 失败（退出码 {result.returncode}）：{(result.stderr or result.stdout).strip()[:300]}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            _warn(report, f"marketplace remove 异常：{exc}")
    elif record.get("marketplace_by_aicj"):
        _warn(report, "未找到 claude，无法移除 marketplace")
    if record.get("marketplace_by_aicj"):
        marketplace = claude_dir / "plugins/marketplaces/jarrodwatts-claude-hud..clone"
        if marketplace.is_dir() and not dry_run:
            shutil.rmtree(marketplace, ignore_errors=True)
        known = claude_dir / "plugins/known_marketplaces.json"
        if known.is_file() and not dry_run:
            try:
                payload = _read_object(known)
                if payload == {}:
                    known.unlink()
            except OSError:
                pass
    if record.get("plugin_by_aicj") and not dry_run:
        path = claude_dir / "settings.json"
        try:
            payload = settings.load(path)
            changed = False
            for key in ("enabledPlugins", "extraKnownMarketplaces"):
                if isinstance(payload.get(key), dict) and not payload[key]:
                    payload.pop(key)
                    changed = True
            if changed:
                settings.save(path, payload)
        except Exception as exc:
            _warn(report, f"清理空插件设置失败：{exc}")


def status(home: Path, record: dict[str, Any]) -> tuple[str, str]:
    if not record:
        return "skipped", "无 hud 记录"
    return str(record.get("status", "skipped")), str(record.get("reason", ""))
