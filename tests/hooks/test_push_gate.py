import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK_SCRIPT = REPO / "core" / "hooks" / "push-review-gate.py"
SCOPE_SCRIPT = REPO / "core" / "skills" / "code-review" / "scripts" / "scope.py"


@pytest.fixture(autouse=True)
def isolated_aicj_env(tmp_path, monkeypatch):
    """Every test gets a temp claude dir and review log dir; real HOME is never touched."""
    claude_dir = tmp_path / "claude-home" / ".claude"
    log_dir = tmp_path / "review-logs"
    monkeypatch.setenv("AICJ_CLAUDE_DIR", str(claude_dir))
    monkeypatch.setenv("AICJ_REVIEW_LOG_DIR", str(log_dir))
    monkeypatch.setenv("AICJ_HOOK_MODE", "block")
    monkeypatch.setenv("HOME", str(tmp_path / "claude-home"))
    installed_scope = claude_dir / "skills/code-review/scripts/scope.py"
    installed_scope.parent.mkdir(parents=True)
    shutil.copyfile(SCOPE_SCRIPT, installed_scope)
    monkeypatch.setenv("PYTHONPATH", "")
    return claude_dir, log_dir


def run_hook(stdin: dict, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **(env or {})}
    return subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(stdin),
        text=True, encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )


def make_repo(tmp_path: Path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    subprocess.run(["git", "init", "--bare", "--initial-branch=main", str(remote)], check=True, capture_output=True)

    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", str(remote), str(clone)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "symbolic-ref", "HEAD", "refs/heads/main"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.email", "t@t.test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "config", "user.name", "T"], check=True, capture_output=True)
    (clone / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(clone), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "commit", "-m", "base"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(clone), "push", "origin", "main"], check=True, capture_output=True)
    return remote, clone


def commit(repo: Path, rel_path: str, content: str, message: str) -> str:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", rel_path], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", message], check=True, capture_output=True)
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True, encoding="utf-8",
    ).stdout.strip()


def record_outcome(repo: Path, log_dir: Path, reviewers: int = 1, gate: str = "single") -> None:
    env = {**os.environ, "AICJ_REVIEW_LOG_DIR": str(log_dir)}
    result = subprocess.run(
        [sys.executable, str(SCOPE_SCRIPT), "--target", str(repo), "--outcome",
         "--reviewers", str(reviewers), "--findings", "0", "--confirmed", "0"],
        env=env,
        text=True, encoding="utf-8",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if gate != "single":
        log_file = log_dir / "code-review-gates.jsonl"
        lines = log_file.read_text(encoding="utf-8").splitlines()
        record = json.loads(lines[-1])
        record["review_gate"] = gate
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def std_input(command: str, cwd: Path) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command, "cwd": str(cwd)}}


class TestPushGate:
    @pytest.mark.parametrize("command", [
        "git status\ngit push origin main",
        "env git push origin main",
        "command git push origin main",
        "bash -c 'git push origin main'",
        "(git push origin main)",
        "git status & git push origin main",
        "git status; git push origin main",
        "env VAR=val command git push origin main",
        "exec git push origin main",
        "nohup git push origin main",
        "sudo git push origin main",
        "time git push origin main",
        "{ git push origin main; }",
        "sh -c 'git push origin main'",
        "zsh -c 'git push origin main'",
        "eval 'git push origin main'",
    ])
    def test_wrapped_and_multiline_push_is_blocked(self, tmp_path, command):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Parse.java", "class Parse {}\n", "risky")
        result = run_hook(std_input(command, clone))
        assert result.returncode == 2, result.stderr
        assert "auth/Parse.java" in result.stderr
        control = run_hook(std_input(command.replace("git push origin main", "git status"), clone))
        assert (control.returncode, control.stderr) == (0, "")

    def test_quoted_git_push_text_is_not_a_push(self, tmp_path):
        _, clone = make_repo(tmp_path)
        result = run_hook(std_input('echo "git push"', clone))
        assert (result.returncode, result.stderr) == (0, "")
        control = run_hook(std_input("unparsed git push origin main", clone))
        assert control.returncode == 2
        assert "无法解析 git push 命令" in control.stderr

    def test_unparsed_push_uses_fallback(self, tmp_path):
        _, clone = make_repo(tmp_path)
        result = run_hook(std_input("unparsed git push origin main", clone))
        assert result.returncode == 2
        assert "无法解析 git push 命令" in result.stderr
        control = run_hook(std_input('unparsed "git push origin main"', clone))
        assert (control.returncode, control.stderr) == (0, "")

    @pytest.mark.parametrize("command", [
        "git branch feature/push-notify",
        "git log origin/push-fix",
        "git checkout -b push-x",
        'git commit -m "push"',
        "git -C /tmp log origin/push-fix",
    ])
    def test_fallback_ignores_non_push_git_commands_mentioning_push(self, tmp_path, command):
        _, clone = make_repo(tmp_path)
        result = run_hook(std_input(command, clone), env={"AICJ_HOOK_MODE": "block"})
        assert (result.returncode, result.stderr) == (0, "")

    @pytest.mark.parametrize("command", [
        "unparsed git push origin main",
        "unparsed git --no-pager -c a=b push origin main",
        "unparsed /usr/bin/git -C /tmp push origin main",
        "unparsed (git push origin main)",
        "unparsed $(git push origin main)",
        "unparsed git.exe push origin main",
    ])
    def test_fallback_still_denies_unparsed_push_in_block_mode(self, tmp_path, command):
        _, clone = make_repo(tmp_path)
        result = run_hook(std_input(command, clone), env={"AICJ_HOOK_MODE": "block"})
        assert result.returncode == 2
        assert "无法解析 git push 命令" in result.stderr

    def test_new_branch_at_commit_already_on_remote_is_allowed(self, tmp_path):
        _, clone = make_repo(tmp_path)
        subprocess.run(["git", "-C", str(clone), "branch", "newbranch"], check=True, capture_output=True)
        result = run_hook(std_input("git push -u origin newbranch", clone), env={"AICJ_HOOK_MODE": "block"})
        assert (result.returncode, result.stderr) == (0, "")

    def test_new_branch_risky_push_is_blocked(self, tmp_path):
        _, clone = make_repo(tmp_path)
        subprocess.run(["git", "-C", str(clone), "switch", "-c", "feat"], check=True, capture_output=True)
        commit(clone, "auth/New.java", "class New {}\n", "risky")
        result = run_hook(std_input("git push -u origin feat", clone))
        assert result.returncode == 2, result.stderr
        assert "auth/New.java" in result.stderr
        subprocess.run(["git", "-C", str(clone), "reset", "--hard", "origin/main"], check=True, capture_output=True)
        commit(clone, "README.md", "base\nsmall edit\n", "small")
        control = run_hook(std_input("git push -u origin feat", clone))
        assert (control.returncode, control.stderr) == (0, "")

    @pytest.mark.parametrize("mode,expected", [("block", 2), ("warn", 0)])
    def test_missing_scope_in_block_mode_is_internal_error(self, tmp_path, isolated_aicj_env, mode, expected):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Missing.java", "class Missing {}\n", "risky")
        control = run_hook(std_input("git push origin main", clone), env={"AICJ_HOOK_MODE": mode})
        assert control.returncode == expected
        assert "auth/Missing.java" in control.stderr
        assert "内部异常" not in control.stderr
        claude, _ = isolated_aicj_env
        (claude / "skills/code-review/scripts/scope.py").unlink()
        result = run_hook(std_input("git push origin main", clone), env={"AICJ_HOOK_MODE": mode})
        assert result.returncode == expected, result.stderr
        assert "内部异常" in result.stderr
        assert "scope.py" in result.stderr
        if mode == "warn":
            assert "AICJ_HOOK_MODE=warn" in result.stderr

    @pytest.mark.parametrize("mode,expected", [("block", 2), ("warn", 0)])
    def test_uncomputable_new_branch_range_obeys_mode(self, tmp_path, mode, expected):
        _, clone = make_repo(tmp_path)
        result = run_hook(std_input("git push origin absent", clone), env={"AICJ_HOOK_MODE": mode})
        assert result.returncode == expected, result.stderr
        assert "无法计算" in result.stderr
        control = run_hook(std_input("git push origin main", clone), env={"AICJ_HOOK_MODE": mode})
        assert (control.returncode, control.stderr) == (0, "")
    def test_non_push_command_is_allowed(self, tmp_path):
        _, clone = make_repo(tmp_path)
        result = run_hook(std_input("git status", clone))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_zero_risk_small_change_without_record_is_allowed(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "src/component.ts", "export const a = 1;\n", "small")
        result = run_hook(std_input("git push origin main", clone))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_risky_change_without_record_is_blocked(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Policy.java", "@Transactional public class Policy {}\n", "risky")
        result = run_hook(std_input("git push origin main", clone))
        assert result.returncode == 2
        assert "auth/Policy.java" in result.stderr
        assert "[push-review-gate]" in result.stderr

    def test_recorded_worktree_branch_merge_push_is_allowed(self, tmp_path, isolated_aicj_env):
        _claude_dir, log_dir = isolated_aicj_env
        remote, clone = make_repo(tmp_path)
        # create a worktree-like task branch from main baseline
        subprocess.run(["git", "-C", str(clone), "switch", "-c", "feature/w"], check=True, capture_output=True)
        base = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "main"],
            check=True,
            capture_output=True,
            text=True, encoding="utf-8",
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(clone), "config", "branch.feature/w.fork-oid", base],
            check=True,
            capture_output=True,
        )
        commit(clone, "src/AuthService.java", "public class AuthService {}\n", "change")
        record_outcome(clone, log_dir)
        # merge into main with --no-ff and push
        subprocess.run(["git", "-C", str(clone), "switch", "main"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(clone), "merge", "--no-ff", "feature/w", "-m", "merge"],
            check=True,
            capture_output=True,
        )
        result = run_hook(std_input("git push origin main", clone))
        assert result.returncode == 0, result.stderr

    def test_recorded_then_modified_file_is_blocked(self, tmp_path, isolated_aicj_env):
        _claude_dir, log_dir = isolated_aicj_env
        _, clone = make_repo(tmp_path)
        subprocess.run(["git", "-C", str(clone), "switch", "-c", "feature/w"], check=True, capture_output=True)
        base = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "main"],
            check=True,
            capture_output=True,
            text=True, encoding="utf-8",
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(clone), "config", "branch.feature/w.fork-oid", base],
            check=True,
            capture_output=True,
        )
        commit(clone, "src/Auth.java", "public class Auth {}\n", "v1")
        # push v1 directly so the remote branch exists and the gate can compare blobs
        subprocess.run(["git", "-C", str(clone), "push", "origin", "feature/w"], check=True, capture_output=True)
        record_outcome(clone, log_dir)
        commit(clone, "src/Auth.java", "public class Auth { int x; }\n", "v2")
        result = run_hook(std_input("git push origin feature/w", clone))
        assert result.returncode == 2
        assert "src/Auth.java" in result.stderr

    def test_git_safe_with_global_c_flag_recognized(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/X.java", "class X {}\n", "x")
        result = run_hook(std_input(f"cd {clone.as_posix()} && git-safe -C {clone.as_posix()} push origin main", clone))
        assert result.returncode == 2
        assert "auth/X.java" in result.stderr

    def test_dry_run_is_allowed(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Y.java", "class Y {}\n", "y")
        result = run_hook(std_input("git push --dry-run origin main", clone))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_common_dir_mismatch_record_ignored(self, tmp_path, isolated_aicj_env):
        _claude_dir, log_dir = isolated_aicj_env
        _, clone = make_repo(tmp_path)
        subprocess.run(["git", "-C", str(clone), "switch", "-c", "feature/w"], check=True, capture_output=True)
        base = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "main"],
            check=True,
            capture_output=True,
            text=True, encoding="utf-8",
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(clone), "config", "branch.feature/w.fork-oid", base],
            check=True,
            capture_output=True,
        )
        commit(clone, "auth/Z.java", "class Z {}\n", "z")
        # push the initial commit so the remote ref exists for comparison
        subprocess.run(["git", "-C", str(clone), "push", "origin", "feature/w"], check=True, capture_output=True)
        # make a further local change; record the head blob
        commit(clone, "auth/Z.java", "class Z { int x; }\n", "z2")
        head_blob = subprocess.run(
            ["git", "-C", str(clone), "hash-object", "auth/Z.java"],
            check=True,
            capture_output=True,
            text=True, encoding="utf-8",
        ).stdout.strip()
        # forge a record that matches the head blob but uses a different common_dir
        common_dir = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "--git-common-dir"],
            check=True,
            capture_output=True,
            text=True, encoding="utf-8",
        ).stdout.strip()
        record = {
            "ts": "2026-01-01T00:00:00+00:00",
            "kind": "outcome",
            "common_dir": str(Path(common_dir).resolve().parent / "other"),
            "repo_root": str(clone),
            "branch": "feature/w",
            "base_oid": base,
            "reviewers": 1,
            "findings": 0,
            "confirmed": 0,
            "review_gate": "single",
            "reviewed_blobs": {"auth/Z.java": head_blob},
        }
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "code-review-gates.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
        result = run_hook(std_input("git push origin feature/w", clone))
        assert result.returncode == 2
        assert "auth/Z.java" in result.stderr

    def test_cwd_at_top_level_without_tool_input_cwd(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Top.java", "class Top {}\n", "top risky")
        stdin = {
            "tool_name": "Bash",
            "tool_input": {"command": "git push origin main"},
            "cwd": str(clone),
        }
        result = run_hook(stdin)
        assert result.returncode == 2
        assert "auth/Top.java" in result.stderr

    def test_cd_segment_updates_cwd(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Cd.java", "class Cd {}\n", "cd risky")
        result = run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": "cd clone && git push origin main"},
            "cwd": str(tmp_path),
        })
        assert result.returncode == 2
        assert "auth/Cd.java" in result.stderr

    def test_variable_expansion_in_minus_c_path(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Var.java", "class Var {}\n", "var risky")
        result = run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": 'R=clone; ~/.claude/bin/git-safe -C "$R" push origin main'},
            "cwd": str(tmp_path),
        })
        assert result.returncode == 2
        assert "auth/Var.java" in result.stderr

    def test_unset_variable_in_minus_c_path_denies_with_literal_path_message(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Unset.java", "class Unset {}\n", "unset risky")
        result = run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": 'git-safe -C "$UNSET" push origin main'},
            "cwd": str(tmp_path),
        })
        assert result.returncode == 2
        assert "无法解析 push 所在仓库" in result.stderr
        assert "字面绝对路径" in result.stderr

    def test_subset_gate_allows_unreviewed_small_readme_after_reviewed_merge(self, tmp_path, isolated_aicj_env):
        _claude_dir, log_dir = isolated_aicj_env
        _, clone = make_repo(tmp_path)
        subprocess.run(["git", "-C", str(clone), "switch", "-c", "feature/w"], check=True, capture_output=True)
        base = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "main"],
            check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()
        subprocess.run(
            ["git", "-C", str(clone), "config", "branch.feature/w.fork-oid", base],
            check=True, capture_output=True,
        )
        commit(clone, "src/a.txt", "a\n", "a")
        commit(clone, "src/b.txt", "b\n", "b")
        commit(clone, "src/c.txt", "c\n", "c")
        record_outcome(clone, log_dir)
        subprocess.run(["git", "-C", str(clone), "push", "origin", "feature/w"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(clone), "switch", "main"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(clone), "merge", "--no-ff", "feature/w", "-m", "merge"],
            check=True, capture_output=True,
        )
        commit(clone, "README.md", "base\nextra\n", "readme tweak")
        result = run_hook(std_input("git push origin main", clone))
        assert result.returncode == 0, result.stderr

    def test_later_record_for_same_path_covers_current_blob(self, tmp_path, isolated_aicj_env):
        _claude_dir, log_dir = isolated_aicj_env
        _, clone = make_repo(tmp_path)
        subprocess.run(["git", "-C", str(clone), "switch", "-c", "feature/w"], check=True, capture_output=True)
        base = subprocess.run(
            ["git", "-C", str(clone), "rev-parse", "main"],
            check=True, capture_output=True, text=True, encoding="utf-8").stdout.strip()
        subprocess.run(
            ["git", "-C", str(clone), "config", "branch.feature/w.fork-oid", base],
            check=True, capture_output=True,
        )
        commit(clone, "src/X.java", "class X { int v1; }\n", "v1")
        subprocess.run(["git", "-C", str(clone), "push", "origin", "feature/w"], check=True, capture_output=True)
        record_outcome(clone, log_dir)
        commit(clone, "src/X.java", "class X { int v2; }\n", "v2")
        record_outcome(clone, log_dir)
        result = run_hook(std_input("git push origin feature/w", clone))
        assert result.returncode == 0, result.stderr

    def test_push_with_tags_and_refspec_still_gated(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Tag.java", "class Tag {}\n", "tag risky")
        result = run_hook(std_input("git push origin main --tags", clone))
        assert result.returncode == 2
        assert "auth/Tag.java" in result.stderr

    def test_push_option_consumes_its_argument_remote_is_origin(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Opt.java", "class Opt {}\n", "opt risky")
        result = run_hook(std_input("git push -o ci.skip origin main", clone))
        assert result.returncode == 2
        assert "auth/Opt.java" in result.stderr

    def test_non_push_command_with_unresolvable_path_is_allowed(self, tmp_path, isolated_aicj_env):
        result = run_hook(std_input("git -C /nonexistent status", tmp_path))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_shell_cd_unset_var_and_non_git_command_is_allowed(self, tmp_path, isolated_aicj_env):
        result = run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": 'cd "$UNSET_X" && ls', "cwd": str(tmp_path)},
        })
        assert result.returncode == 0
        assert result.stderr == ""

    def test_git_minus_c_unset_var_non_push_is_allowed(self, tmp_path, isolated_aicj_env):
        result = run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": 'git -C "$UNSET_X" log', "cwd": str(tmp_path)},
        })
        assert result.returncode == 0
        assert result.stderr == ""

    def test_push_with_unresolvable_minus_c_falls_back_to_no_cwd_does_not_use_clean_cwd(self, tmp_path, isolated_aicj_env):
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Cwd.java", "class Cwd {}\n", "cwd risky")
        result = run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": 'git-safe -C "$UNSET_X" push origin main', "cwd": str(clone)},
        })
        assert result.returncode == 2
        assert "无法解析 push 所在仓库" in result.stderr

    def test_cd_tilde_relative_to_home_resolves(self, tmp_path, isolated_aicj_env, monkeypatch):
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        # ~ must expand against the shell HOME, not AICJ_CLAUDE_DIR
        monkeypatch.setenv("HOME", str(fake_home))
        _, clone = make_repo(tmp_path)
        repo_under_home = fake_home / "myrepo"
        repo_under_home.mkdir(parents=True)
        subprocess.run(["git", "clone", str(clone), str(repo_under_home / "clone")], check=True, capture_output=True)
        repo = repo_under_home / "clone"
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.test"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True, capture_output=True)
        commit(repo, "auth/Tilde.java", "class Tilde {}\n", "tilde risky")
        result = run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": "cd ~/myrepo/clone && git push origin main", "cwd": str(tmp_path)},
        })
        assert result.returncode == 2
        assert "auth/Tilde.java" in result.stderr

    def test_git_minus_c_status_does_not_change_cwd_for_later_push(self, tmp_path, isolated_aicj_env):
        dir_a = tmp_path / "a"
        dir_a.mkdir(parents=True)
        dir_b = tmp_path / "b"
        dir_b.mkdir(parents=True)
        _, repo_a = make_repo(dir_a)
        _, repo_b = make_repo(dir_b)
        commit(repo_b, "auth/RepoB.java", "class RepoB {}\n", "repo b risky")
        result = run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": f"git -C {repo_a} status; git push", "cwd": str(repo_b)},
        })
        assert result.returncode == 2
        assert "auth/RepoB.java" in result.stderr

    def test_cd_then_push_then_cd_checks_first_repo(self, tmp_path, isolated_aicj_env):
        dir_a = tmp_path / "a"
        dir_a.mkdir(parents=True)
        dir_b = tmp_path / "b"
        dir_b.mkdir(parents=True)
        _, repo_a = make_repo(dir_a)
        _, repo_b = make_repo(dir_b)
        commit(repo_a, "auth/RepoA.java", "class RepoA {}\n", "repo a risky")
        commit(repo_b, "auth/RepoB.java", "class RepoB {}\n", "repo b risky")
        result = run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": f"cd {repo_a.as_posix()} && git push && cd {repo_b.as_posix()}", "cwd": str(tmp_path)},
        })
        assert result.returncode == 2
        assert "auth/RepoA.java" in result.stderr


class TestHookMode:
    def test_warn_mode_allows_with_hint(self, tmp_path, isolated_aicj_env, monkeypatch):
        monkeypatch.setenv("AICJ_HOOK_MODE", "warn")
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Warn.java", "class Warn {}\n", "warn risky")
        result = run_hook(std_input("git push origin main", clone))
        assert result.returncode == 0
        assert "AICJ_HOOK_MODE=warn" in result.stderr
        assert "auth/Warn.java" in result.stderr  # hint carries the same deny content

    def test_default_mode_is_warn(self, tmp_path, isolated_aicj_env, monkeypatch):
        monkeypatch.delenv("AICJ_HOOK_MODE", raising=False)
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Default.java", "class Default {}\n", "default risky")
        result = run_hook(std_input("git push origin main", clone))
        assert result.returncode == 0
        assert "AICJ_HOOK_MODE=warn" in result.stderr

    def test_block_mode_denies(self, tmp_path, isolated_aicj_env, monkeypatch):
        monkeypatch.setenv("AICJ_HOOK_MODE", "block")
        _, clone = make_repo(tmp_path)
        commit(clone, "auth/Block.java", "class Block {}\n", "block risky")
        result = run_hook(std_input("git push origin main", clone))
        assert result.returncode == 2
        assert "auth/Block.java" in result.stderr
        assert "AICJ_HOOK_MODE=warn" not in result.stderr

    def test_warn_mode_also_allows_unresolvable_repo_with_hint(self, tmp_path, isolated_aicj_env, monkeypatch):
        monkeypatch.setenv("AICJ_HOOK_MODE", "warn")
        result = run_hook({
            "tool_name": "Bash",
            "tool_input": {"command": 'git-safe -C "$UNSET_X" push origin main', "cwd": str(tmp_path)},
        })
        assert result.returncode == 0
        assert "无法解析 push 所在仓库" in result.stderr
