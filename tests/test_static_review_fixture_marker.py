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
    source.write_text('class Service { String password = "abcdefgh1234"; }\n', encoding="utf-8")
    result = run(tmp_path)
    assert result.returncode == 2
    assert "Service.java" in result.stdout
    assert "application.yml" not in result.stdout
