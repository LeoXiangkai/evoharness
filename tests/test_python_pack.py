from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from installer.lib import index

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "packs/python"
DETECT = PACK / "skills/python-verify/scripts/detect_python.py"
STATIC = PACK / "skills/python-verify/scripts/static_review.py"
FIXTURES = REPO / "tests/packs/python/fixtures"
AICJ = REPO / "installer/aicj.py"


def run_detect(name: str) -> dict:
    result = subprocess.run([sys.executable, str(DETECT), str(FIXTURES / name)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def run_static(name: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STATIC), str(FIXTURES / "static" / name)], capture_output=True, text=True, encoding="utf-8")


def test_pack_manifest_shape_and_rule_frontmatter():
    data = json.loads((PACK / "pack.json").read_text(encoding="utf-8"))
    assert data["detect"]["files"] == ["pyproject.toml", "requirements.txt", "requirements*.txt", "setup.py", "setup.cfg"]
    assert [row["target"] for row in data["entries"]["rules"]] == [
        "rules/python-core.md", "rules/python-web.md"
    ]
    assert data["entries"]["skills"][0]["target"] == "skills/python-verify"
    for path in sorted((PACK / "rules").glob("*.md")):
        content = path.read_text(encoding="utf-8")
        assert content.startswith("---\npaths:\n")
        assert len(content.encode("utf-8")) <= 4096


def test_detect_fastapi_uv_fixture_fields():
    result = run_detect("fastapi-uv")
    assert result["package_manager"] == "uv"
    assert result["python_requires"] == ">=3.11"
    assert result["frameworks"] == ["fastapi"]
    assert result["orm"] == ["sqlalchemy"]
    assert result["migrations"] == "alembic"
    assert result["pydantic_major"] == 2
    assert result["tools"] == {"ruff": True, "mypy": True, "pyright": False, "pytest": True}
    assert result["entry"] == "app.main:app"
    assert result["commands"]["lint"] == "uv run ruff check ."
    assert result["commands"]["format_check"] == "uv run ruff format --check ."
    assert result["commands"]["type_check"] == "uv run mypy ."
    assert result["commands"]["test"] == "uv run pytest"
    assert result["commands"]["start"] == "uv run uvicorn app.main:app"


def test_detect_flask_poetry_fixture_fields():
    result = run_detect("flask-poetry")
    assert result["package_manager"] == "poetry"
    assert result["frameworks"] == ["flask"]
    assert result["orm"] == ["peewee"]
    assert result["migrations"] is None
    assert result["pydantic_major"] is None
    assert result["tools"] == {"ruff": True, "mypy": False, "pyright": False, "pytest": True}
    assert result["entry"] == "app:app"
    assert result["commands"]["start"] == "poetry run flask --app app:app run"


def test_detect_django_pip_fixture_fields():
    result = run_detect("django-pip")
    assert result["package_manager"] == "pip"
    assert result["frameworks"] == ["django"]
    assert result["orm"] == ["django-orm"]
    assert result["migrations"] == "django"
    assert result["tools"] == {"ruff": False, "mypy": False, "pyright": False, "pytest": True}
    assert result["commands"]["test"] == "python -m pytest"
    assert result["commands"]["start"] == "MISSING"


def test_detect_no_project_defaults_without_failure():
    result = run_detect("no-project")
    assert result == {
        "commands": {"format_check": "MISSING", "lint": "MISSING", "start": "MISSING", "static": "MISSING", "test": "MISSING", "type_check": "MISSING"},
        "entry": None,
        "frameworks": [],
        "migrations": None,
        "orm": [],
        "package_manager": "unknown",
        "pydantic_major": None,
        "python_requires": None,
        "tools": {"mypy": False, "pyright": False, "pytest": False, "ruff": False},
    }


def test_static_review_good_has_no_findings():
    result = run_static("good")
    assert result.returncode == 0
    assert "Scanned files:" in result.stdout
    assert "No P0/P1 deterministic findings." in result.stdout


def test_static_review_bad_hits_each_rule_and_p0_exit():
    result = run_static("bad")
    assert result.returncode == 1
    for message in (
        "bare except",
        "exception handler swallows",
        "mutable default argument",
        "SQL string construction",
        "possible plaintext secret",
        "open() should be used inside with",
        "print() in non-test code",
    ):
        assert message in result.stdout, message


def test_static_review_syntax_error_is_reported_without_crash():
    result = subprocess.run([sys.executable, str(STATIC), str(FIXTURES / "static/bad/syntax_error.py")], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0
    assert "parse failed" in result.stdout


def test_static_review_allows_plaintext_config_but_reports_python_secret(tmp_path: Path):
    config = tmp_path / "application.yml"
    config.write_text("password: abcdefgh1234\n", encoding="utf-8")
    source = tmp_path / "settings.py"
    source.write_text('password = "abcdefgh1234"\n', encoding="utf-8")
    result = subprocess.run([sys.executable, str(STATIC), str(tmp_path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1
    assert "settings.py" in result.stdout
    assert "application.yml" not in result.stdout


def test_resolve_packs_auto_detects_python_files(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    assert index.resolve_packs("auto", tmp_path, ["python"], False) == ["python"]
    (tmp_path / "pyproject.toml").unlink()
    (tmp_path / "requirements-dev.txt").write_text("pytest\n", encoding="utf-8")
    assert index.resolve_packs("auto", tmp_path, ["python"], False) == ["python"]


def test_install_doctor_uninstall_python_pack(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    install = subprocess.run([sys.executable, str(AICJ), "install", "--home", str(home), "--source", str(REPO), "--packs", "python"], capture_output=True, text=True, encoding="utf-8")
    assert install.returncode == 0, install.stdout + install.stderr
    for name in ("python-core.md", "python-web.md"):
        assert (home / ".claude/rules" / name).is_file()
    assert (home / ".claude/skills/python-verify/SKILL.md").is_file()
    doctor = subprocess.run([sys.executable, str(AICJ), "doctor", "--home", str(home)], capture_output=True, text=True, encoding="utf-8")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "MISSING" not in doctor.stdout
    uninstall = subprocess.run([sys.executable, str(AICJ), "uninstall", "--home", str(home)], capture_output=True, text=True, encoding="utf-8")
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    assert list(home.rglob("*")) == []


def test_python_pack_has_no_forbidden_terms_or_venv_commands():
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in PACK.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    for term in ("gpt-", "claude", "openai", "python -m venv", "virtualenv", "conda create", "/Users/"):
        assert term.lower() not in text.lower(), term
    assert not re.search(r"\b(?:GPT|Claude|OpenAI)\b", text, re.I)
