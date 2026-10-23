from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from adapter_helpers import WORKER, fake_command, run_script


def git(*args, cwd):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.test",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t.test"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture()
def repo(tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    git("init", "-q", "-b", "main", cwd=main)
    (main / "a.txt").write_text("a\n", encoding="utf-8")
    git("add", "a.txt", cwd=main)
    git("commit", "-q", "-m", "init", cwd=main)
    wt = tmp_path / "wt"
    git("worktree", "add", "-q", "-b", "feature/x", str(wt), cwd=main)
    return main, wt


def git_only_path(tmp_path):
    """PATH that has git but no engine CLI."""
    import shutil
    d = tmp_path / "git-only-bin"
    d.mkdir(exist_ok=True)
    if os.name == "nt":
        real_git = shutil.which("git")
        assert real_git
        return Path(real_git).parent
    else:
        link = d / "git"
        if not link.exists():
            link.symlink_to(shutil.which("git"))
    return d


def worker(*args, claude, env_extra=None, path=None):
    return run_script(WORKER, *args, claude=claude, env_extra=env_extra, path=path)


def dry(repo, claude, *extra, env_extra=None):
    _, wt = repo
    result = worker("--cd", str(wt), "--dry-run", *extra, "do it", claude=claude, env_extra=env_extra)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_main_worktree_rejected(repo, tmp_path):
    main, _ = repo
    result = worker("--cd", str(main), "--tier", "high", "--dry-run", "x", claude=tmp_path)
    assert result.returncode == 2


def test_non_git_directory_rejected(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert worker("--cd", str(plain), "--tier", "high", "--dry-run", "x", claude=tmp_path).returncode == 2


def test_missing_directory_and_blank_prompt_rejected(repo, tmp_path):
    _, wt = repo
    assert worker("--cd", str(tmp_path / "nope"), "--tier", "high", "--dry-run", "x", claude=tmp_path).returncode == 2
    assert worker("--cd", str(wt), "--tier", "high", "--dry-run", "   ", claude=tmp_path).returncode == 2


def test_missing_tier_is_usage_error(repo, tmp_path):
    _, wt = repo
    assert worker("--cd", str(wt), "--dry-run", "x", claude=tmp_path).returncode == 2


def test_claude_engine_argv(repo, tmp_path):
    argv = dry(repo, tmp_path, "--tier", "mid")
    assert argv[:2] == ["claude", "-p"]
    assert argv[argv.index("--disallowedTools") + 1] == "Agent"
    assert argv[argv.index("--") + 1] == "do it"
    assert "--model" not in argv
    assert argv[-1] == "do it"


def test_codex_engine_argv(repo, tmp_path):
    _, wt = repo
    argv = dry(repo, tmp_path, "--tier", "low", "--engine", "codex")
    assert argv[:2] == ["codex", "exec"]
    assert argv[argv.index("--cd") + 1] == str(wt.resolve())
    assert "-m" not in argv
    assert argv[-1].startswith("do it")
    assert "不得再派子代理" in argv[-1] and "commit/push" in argv[-1]
    assert argv[argv.index("-s") + 1] == "danger-full-access"
    assert 'model_reasoning_effort="low"' in argv
    high = dry(repo, tmp_path, "--tier", "high", "--engine", "codex", env_extra={"AICJ_EXECUTOR_CODEX_SANDBOX": "workspace-write"})
    assert high[high.index("-s") + 1] == "workspace-write"
    assert 'model_reasoning_effort="high"' in high


def test_engine_from_environment_and_flag_precedence(repo, tmp_path):
    assert dry(repo, tmp_path, "--tier", "low", env_extra={"AICJ_EXECUTOR_ENGINE": " Codex "})[0] == "codex"
    assert dry(repo, tmp_path, "--tier", "low", "--engine", "claude", env_extra={"AICJ_EXECUTOR_ENGINE": "codex"})[0] == "claude"


def test_unknown_engine_from_environment_rejected(repo, tmp_path):
    _, wt = repo
    result = worker("--cd", str(wt), "--tier", "low", "--dry-run", "x", claude=tmp_path,
                    env_extra={"AICJ_EXECUTOR_ENGINE": "bogus"})
    assert result.returncode == 2


def test_tier_model_passed_only_for_its_tier(repo, tmp_path):
    env = {"AICJ_EXECUTOR_MODEL_HIGH": "model-h", "AICJ_EXECUTOR_MODEL_LOW": "model-l"}
    high = dry(repo, tmp_path, "--tier", "high", env_extra=env)
    assert high[high.index("--model") + 1] == "model-h"
    assert high.index("--model") < high.index("--")
    assert "--model" not in dry(repo, tmp_path, "--tier", "mid", env_extra=env)
    codex = dry(repo, tmp_path, "--tier", "low", "--engine", "codex", env_extra=env)
    assert codex[codex.index("-m") + 1] == "model-l"


def test_prompt_reaches_engine_as_one_complete_value(repo, tmp_path):
    fake = tmp_path / "fake-bin"
    fake_command(fake, "claude", "import sys; print(repr(sys.argv[1:])); print(repr(sys.stdin.read()))\n")
    path = os.pathsep.join([str(fake), str(git_only_path(tmp_path))])
    prompt = "line one\nline two & %VALUE%"
    result = worker("--cd", str(repo[1]), "--tier", "high", prompt, claude=tmp_path, path=path)
    assert result.returncode == 0
    assert repr(prompt) in result.stdout or prompt in result.stdout


def test_blank_model_variable_is_ignored(repo, tmp_path):
    assert "--model" not in dry(repo, tmp_path, "--tier", "high", env_extra={"AICJ_EXECUTOR_MODEL_HIGH": "  "})


def test_engine_cli_missing_returns_127(repo, tmp_path):
    _, wt = repo
    empty = git_only_path(tmp_path)
    for engine in ("claude", "codex"):
        result = worker("--cd", str(wt), "--tier", "high", "--engine", engine, "x", claude=tmp_path, path=str(empty))
        assert result.returncode == 127
        assert "降级" in result.stderr


def test_engine_nonzero_exit_is_propagated(repo, tmp_path):
    _, wt = repo
    fake = tmp_path / "fake-bin"
    fake_command(fake, "claude", "import sys\nsys.exit(7)\n")
    path = os.pathsep.join([str(fake), str(git_only_path(tmp_path))])
    result = worker("--cd", str(wt), "--tier", "high", "x", claude=tmp_path, path=path)
    assert result.returncode == 7


@pytest.mark.parametrize("value", ["wat", "0", "-1", "nan", "inf"])
def test_invalid_timeout_reports_error_without_limiting_execution(repo, tmp_path, value):
    fake = tmp_path / "fake-bin"
    fake_command(fake, "claude", "import time; time.sleep(0.1); print('completed')\n")
    path = os.pathsep.join([str(fake), str(git_only_path(tmp_path))])
    result = worker("--cd", str(repo[1]), "--tier", "high", "x", claude=tmp_path, path=path, env_extra={"AICJ_EXECUTOR_TIMEOUT": value})
    assert result.returncode == 0, result.stderr
    assert result.stdout == "completed\n"
    assert "AICJ_EXECUTOR_TIMEOUT" in result.stderr
    control = worker("--cd", str(repo[1]), "--tier", "high", "x", claude=tmp_path, path=path, env_extra={"AICJ_EXECUTOR_TIMEOUT": "0.01"})
    assert control.returncode == 124
    assert control.stdout == ""


def test_timeout_returns_124(repo, tmp_path):
    fake = tmp_path / "fake-bin"
    fake_command(fake, "claude", "import time; time.sleep(0.1); print('completed')\n")
    path = os.pathsep.join([str(fake), str(git_only_path(tmp_path))])
    result = worker("--cd", str(repo[1]), "--tier", "high", "x", claude=tmp_path, path=path, env_extra={"AICJ_EXECUTOR_TIMEOUT": "0.01"})
    assert result.returncode == 124
    assert "超时" in result.stderr
    assert result.stdout == ""
    control = worker("--cd", str(repo[1]), "--tier", "high", "x", claude=tmp_path, path=path, env_extra={"AICJ_EXECUTOR_TIMEOUT": ""})
    assert (control.returncode, control.stdout, control.stderr) == (0, "completed\n", "")


def _cpa_server(expected_token: str, status: int = 200):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/v1/models":
                self.send_response(404)
            elif self.headers.get("Authorization") != "Bearer " + expected_token:
                self.send_response(401)
            else:
                self.send_response(status)
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def test_claude_cpa_environment_is_isolated_and_engine_runs(repo, tmp_path):
    server = _cpa_server("test-token")
    try:
        fake = tmp_path / "fake-bin"
        marker = repo[1] / "worker-env.txt"
        fake_command(fake, "claude", """
import os
from pathlib import Path
Path('worker-env.txt').write_text('BASE=' + os.environ.get('ANTHROPIC_BASE_URL', '') + '\\nCONFIG=' + os.environ.get('CLAUDE_CONFIG_DIR', '') + '\\nKEY=' + os.environ.get('ANTHROPIC_API_KEY', '<missing>') + '\\nNO_PROXY=' + os.environ.get('NO_PROXY', '') + '\\nno_proxy=' + os.environ.get('no_proxy', ''), encoding='utf-8')
""")
        path = os.pathsep.join([str(fake), str(git_only_path(tmp_path))])
        env = {
            "AICJ_EXECUTOR_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "AICJ_EXECUTOR_TOKEN": "test-" + "token",
            "AICJ_EXECUTOR_CONFIG_DIR": str(tmp_path / "isolated"),
            "ANTHROPIC_API_KEY": "must-disappear",
            "CLAUDECODE": "must-disappear",
        }
        result = worker("--cd", str(repo[1]), "--tier", "low", "x", claude=tmp_path, path=path, env_extra=env)
        assert result.returncode == 0, result.stderr
        text = marker.read_text(encoding="utf-8")
        assert f"BASE=http://127.0.0.1:{server.server_port}" in text
        assert f"CONFIG={tmp_path / 'isolated'}" in text
        assert "KEY=<missing>" in text
        assert "127.0.0.1" in text and "localhost" in text
    finally:
        server.shutdown()


def test_claude_cpa_failure_returns_69_without_engine(repo, tmp_path):
    server = _cpa_server("right-token")
    try:
        fake = tmp_path / "fake-bin"
        marker = repo[1] / "called"
        fake_command(fake, "claude", f"from pathlib import Path; Path({str(marker)!r}).touch()")
        path = os.pathsep.join([str(fake), str(git_only_path(tmp_path))])
        bad = worker("--cd", str(repo[1]), "--tier", "low", "x", claude=tmp_path, path=path, env_extra={
            "AICJ_EXECUTOR_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "AICJ_EXECUTOR_TOKEN": "wrong-" + "token",
        })
        assert bad.returncode == 69 and not marker.exists()
        missing = worker("--cd", str(repo[1]), "--tier", "low", "x", claude=tmp_path, path=path, env_extra={
            "AICJ_EXECUTOR_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "AICJ_EXECUTOR_TOKEN": "",
        })
        assert missing.returncode == 69 and not marker.exists()
    finally:
        server.shutdown()


def test_claude_dry_run_does_not_contact_cpa(repo, tmp_path):
    server = _cpa_server("test-token", status=500)
    try:
        result = worker("--cd", str(repo[1]), "--tier", "low", "--dry-run", "x", claude=tmp_path, env_extra={
            "AICJ_EXECUTOR_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "AICJ_EXECUTOR_TOKEN": "test-" + "token",
        })
        assert result.returncode == 0
        assert f"AICJ_EXECUTOR_BASE_URL=http://127.0.0.1:{server.server_port}" in result.stderr
    finally:
        server.shutdown()
