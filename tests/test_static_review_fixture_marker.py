import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "static_review_check.py"
BAD_SQL = "update t_user set name = 'x';\n"


def run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, paths)], text=True, encoding="utf-8", capture_output=True)


def test_unmarked_bad_sample_is_blocked(tmp_path):
    sample = tmp_path / "bad.sql"
    sample.write_text(BAD_SQL)
    result = run(sample)
    assert result.returncode == 2
    assert "no where clause" in result.stdout


def test_marked_fixture_dir_is_skipped_for_files_and_dirs(tmp_path):
    fixtures = tmp_path / "fixtures"
    (fixtures / "nested").mkdir(parents=True)
    (fixtures / ".static-review-fixture").write_text("")
    sample = fixtures / "nested" / "bad.sql"
    sample.write_text(BAD_SQL)
    for target in (sample, tmp_path):
        result = run(target)
        assert result.returncode == 0
        assert "Scanned files: 0" in result.stdout


def test_plaintext_config_is_allowed_but_java_logic_is_blocked(tmp_path):
    config = tmp_path / "application.yml"
    config.write_text("password: abcdefgh1234\n", encoding="utf-8")
    source = tmp_path / "Service.java"
    source.write_text('class Service { String password = "' + "abcdefgh" + '1234"; }\n', encoding="utf-8")
    result = run(tmp_path)
    assert result.returncode == 2
    assert "Service.java" in result.stdout
    assert "application.yml" not in result.stdout


def test_non_java_source_secrets_are_blocked_but_config_stays_allowed(tmp_path):
    (tmp_path / "a.kt").write_text('val password = "' + "Abc12345" + 'xyz"\n', encoding="utf-8")
    (tmp_path / "b.ts").write_text('const password = "' + "Abc12345" + 'xyz";\n', encoding="utf-8")
    (tmp_path / "application.yml").write_text("password: abcdefgh1234\n", encoding="utf-8")
    result = run(tmp_path)
    assert result.returncode == 2
    assert "a.kt" in result.stdout
    assert "b.ts" in result.stdout
    assert "application.yml" not in result.stdout


def test_non_literal_values_are_not_secrets_but_quoted_literal_is(tmp_path):
    (tmp_path / "ok.py").write_text('token = os.environ.get("X")\n', encoding="utf-8")
    (tmp_path / "ok.ts").write_text("const password = import.meta.env.VITE_PASSWORD\n", encoding="utf-8")
    result = run(tmp_path)
    assert result.returncode == 0
    assert "No P0/P1" in result.stdout
    (tmp_path / "bad.py").write_text('api_token = "' + "abcdefgh" + '1234"\n', encoding="utf-8")
    result = run(tmp_path)
    assert result.returncode == 2
    assert "bad.py" in result.stdout
    assert "ok.py" not in result.stdout
    assert "ok.ts" not in result.stdout


def test_typed_and_quoted_key_literals_are_blocked_but_placeholders_are_not(tmp_path):
    fake = "abcdefgh" + "1234"
    (tmp_path / "a.ts").write_text("const token: string = '" + fake + "'\n", encoding="utf-8")
    (tmp_path / "b.kt").write_text('val password: String = "' + fake + '"\n', encoding="utf-8")
    (tmp_path / "c.ts").write_text('const o = {"password": "' + fake + '"}\n', encoding="utf-8")
    (tmp_path / "d.ts").write_text('const password = "${DB_' + 'PASSWORD}"\n', encoding="utf-8")
    (tmp_path / "e.ts").write_text('const token = "<your-' + 'token>"\n', encoding="utf-8")
    result = run(tmp_path)
    assert result.returncode == 2
    for name in ("a.ts", "b.kt", "c.ts"):
        assert name in result.stdout
    for name in ("d.ts", "e.ts"):
        assert name not in result.stdout


def test_keyword_prefixed_identifiers_are_not_secrets(tmp_path):
    (tmp_path / "ok.py").write_text(
        'TOKEN_URL = "https://auth.example.com/t"\n'
        'token_type = "Bearer1234"\n'
        'tokenizer = "bert-base-uncased"\n'
        'secret_name = "my-secret-name"\n',
        encoding="utf-8",
    )
    (tmp_path / "ok.ts").write_text('const labels = {"passwordLabel": "PasswordInput"}\n', encoding="utf-8")
    (tmp_path / "bad.ts").write_text('const secretKey = "' + "abcdefgh" + '1234"\n', encoding="utf-8")
    result = run(tmp_path)
    assert result.returncode == 2
    assert "bad.ts" in result.stdout
    assert "ok.py" not in result.stdout
    assert "ok.ts" not in result.stdout
