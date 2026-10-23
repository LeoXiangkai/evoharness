from __future__ import annotations

from pathlib import Path

from conftest import run_sanitize


def make_tree(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_clean_tree_passes(tmp_path: Path) -> None:
    make_tree(tmp_path, {"core/rules/ok.md": "# nothing personal\n"})
    result = run_sanitize(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "Summary: 0 sanitize issue(s)" in result.stdout


def test_builtin_patterns_each_fail(tmp_path: Path) -> None:
    cases = {
        "core/a.md": "path is /Users/alice/project/x\n",
        "core/b.md": "service at 127.0.0.1:8317\n",
        "core/c.md": "run worker-cc --model opus\n",
        "core/d.md": "key sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ012345\n",
        "core/e.md": "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456\n",
        "core/f.md": "aws AKIAIOSFODNN7EXAMPLE1\n",
        "core/g.md": "export API_TOKEN=super-secret-value\n",
        "core/h.md": "-----BEGIN RSA PRIVATE KEY-----\n",
    }
    make_tree(tmp_path, cases)
    result = run_sanitize(tmp_path)
    assert result.returncode == 1
    for name in (
        "personal-abs-path",
        "loopback-port",
        "private-executor",
        "secret-key-sk",
        "secret-key-ghp",
        "secret-key-akia",
        "secret-token-assign",
        "secret-private-key",
    ):
        assert name in result.stdout, name


def test_home_paths_also_caught(tmp_path: Path) -> None:
    make_tree(tmp_path, {"packs/p/rule.md": "see /home/bob/data\n"})
    result = run_sanitize(tmp_path)
    assert result.returncode == 1
    assert "personal-abs-path" in result.stdout


def test_extra_patterns_from_env_file(tmp_path: Path) -> None:
    make_tree(tmp_path, {"core/rule.md": "mentions personname-example by name\n"})
    extra = tmp_path / "extra.txt"
    extra.write_text("# personal names\npersonname-example\n", encoding="utf-8")

    result = run_sanitize(tmp_path, extra=extra)
    assert result.returncode == 1
    assert "extra:2" in result.stdout

    result = run_sanitize(tmp_path)
    assert result.returncode == 0, result.stdout


def test_executor_examples_whitelist(tmp_path: Path) -> None:
    files = {
        "adapters/executor/examples/run.md": "calls worker-cc --model sonnet\n",
        "core/rule.md": "calls worker-cc --model sonnet\n",
    }
    make_tree(tmp_path, files)
    result = run_sanitize(tmp_path)
    assert result.returncode == 1
    lines = [line for line in result.stdout.splitlines() if line.startswith("FAIL")]
    assert len(lines) == 1
    assert "core/rule.md" in lines[0]
    assert "adapters/executor/examples/" not in lines[0]


def test_repo_tree_itself_is_clean() -> None:
    from conftest import REPO

    result = run_sanitize(REPO)
    assert result.returncode == 0, result.stdout


def test_new_patterns_each_hit_their_own_rule(tmp_path: Path) -> None:
    cases = {
        "secret-key-sk": ["key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWx\n", "key sk-proj-AbCdEfGhIjKlMnOpQrSt_UvWx\n"],
        "secret-token-assign": [
            'export GITEE_TOKEN: "' + "abcdefgh" + 'ijk"\n',
            "CLIENT_SECRET=abcdefgh1234\n",
            "DB_PASSWORD=hunter2hunter2\n",
            '{"OPENAI_API_KEY": "abcdefghijkl"}\n',
            "OPENAI_API_KEY: abcdefghijkl\n",
        ],
        "personal-abs-path": ["cd /Users/alice\n", "see /home/bob.\n", "/Users/alice)\n"],
        "personal-win-path": ["C:\\Users\\alice\\docs\n", "D:/Users/alice/docs\n"],
        "personal-home-dir": ["cd ~/AI_Content/develop/x\n", "ls ~/workspace-private/notes\n"],
    }
    for rule, lines in cases.items():
        for number, line in enumerate(lines):
            root = tmp_path / f"{rule}-{number}"
            make_tree(root, {"core/x.md": line})
            result = run_sanitize(root)
            assert result.returncode == 1, (rule, line, result.stdout)
            assert rule in result.stdout, (rule, line, result.stdout)


def test_documented_config_dirs_and_placeholders_do_not_false_positive(tmp_path: Path) -> None:
    text = (
        "rules live in ~/.claude/rules and ~/.codex/hooks.json\n"
        "use /Users/<name>/project as the shape\n"
        "TOKEN=$TOKEN_FROM_ENV\n"
        "API_KEY: <your key here>\n"
        "PASSWORD=short\n"
        "see ~/project/src for the layout\n"
        "ask-sk-short is not a key: sk-abc\n"
    )
    make_tree(tmp_path, {"core/ok.md": text})
    result = run_sanitize(tmp_path)
    assert result.returncode == 0, result.stdout


def test_non_utf8_file_is_warned_not_failed_and_not_silent(tmp_path: Path) -> None:
    make_tree(tmp_path, {"core/ok.md": "clean\n"})
    (tmp_path / "core/blob.bin").write_bytes(b"\xff\xfe\x00 /Users/alice/x")
    result = run_sanitize(tmp_path)
    assert result.returncode == 0, result.stdout
    assert "WARN core/blob.bin: not valid UTF-8" in result.stdout
    assert "Summary: 0 sanitize issue(s)" in result.stdout


def test_pycache_is_not_reported(tmp_path: Path) -> None:
    make_tree(tmp_path, {"installer/ok.py": "x = 1\n"})
    cache = tmp_path / "installer/__pycache__"
    cache.mkdir()
    (cache / "ok.cpython-314.pyc").write_bytes(b"\xff\x00")
    result = run_sanitize(tmp_path)
    assert result.returncode == 0
    assert "WARN" not in result.stdout
