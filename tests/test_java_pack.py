from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from installer.lib import index

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "packs/java"
DETECT = PACK / "skills/java-verify/scripts/detect_java.py"
STATIC = PACK / "skills/java-verify/scripts/static_review.py"
FIXTURES = REPO / "tests/packs/java/fixtures"
AICJ = REPO / "installer/aicj.py"


def run_detect(name: str) -> dict:
    result = subprocess.run([sys.executable, str(DETECT), str(FIXTURES / name)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def run_static(name: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(STATIC), str(FIXTURES / "static" / name)], capture_output=True, text=True, encoding="utf-8")


def test_pack_manifest_shape_and_rule_frontmatter():
    data = json.loads((PACK / "pack.json").read_text(encoding="utf-8"))
    assert data["detect"]["files"] == ["pom.xml", "build.gradle", "build.gradle.kts"]
    assert [row["target"] for row in data["entries"]["rules"]] == [
        "rules/java-spring-boot.md", "rules/java-sql-mybatis.md", "rules/java-modern.md"
    ]
    assert data["entries"]["skills"][0]["target"] == "skills/java-verify"
    for path in sorted((PACK / "rules").glob("*.md")):
        assert path.read_text(encoding="utf-8").startswith("---\npaths:\n")


def test_detect_maven_fixture_fields():
    result = run_detect("maven-multi-module")
    assert result["build_tool"] == "maven"
    assert result["wrapper"] is True
    assert result["modules"] == ["module-a"]
    assert result["java_version"] == "8"
    assert result["spring_boot_version"] == "2.7.18"
    assert result["namespace"] == "javax"
    assert result["persistence"] == ["mybatis-plus"]
    assert "-pl <module> -am" in result["commands"]["compile"]
    assert "-pl <module> -am" in result["commands"]["test"]
    assert result["commands"]["start"] == "./mvnw -pl <module> spring-boot:run"


def test_detect_gradle_kts_fixture_fields():
    result = run_detect("gradle-kts")
    assert result["build_tool"] == "gradle"
    assert result["wrapper"] is False
    assert result["modules"] == [":app", ":shared"]
    assert result["java_version"] == "21"
    assert result["spring_boot_version"] == "3.2.5"
    assert result["namespace"] == "jakarta"
    assert result["persistence"] == ["mybatis-plus"]
    assert result["commands"]["compile"] == "gradle :<module>:classes"
    assert result["commands"]["test"] == "gradle :<module>:test"
    assert result["commands"]["start"] == "gradle :<module>:bootRun"


def test_detect_no_build_is_unknown_and_exits_zero():
    result = run_detect("no-build")
    assert result == {
        "build_tool": "unknown", "wrapper": False, "modules": [], "java_version": None,
        "spring_boot_version": None, "namespace": "unknown", "persistence": [],
        "commands": {"compile": None, "test": None, "start": None,
                      "static": (f'"{Path(sys.executable).resolve()}" "{STATIC.resolve()}" <paths>' if sys.platform == "win32" else f"python3 {STATIC} <paths>")},
    }


def test_static_review_good_has_no_findings():
    result = run_static("good")
    assert result.returncode == 0
    assert "Scanned files: 3" in result.stdout
    assert "No P0/P1 deterministic findings." in result.stdout


def test_static_review_bad_hits_each_rule_and_p0_exit():
    result = run_static("bad")
    assert result.returncode == 1
    for message in (
        "possible plaintext secret",
        "MyBatis ${} requires whitelist proof",
        "no where clause",
        "@Transactional should specify rollbackFor",
        "catch block is empty or contains comments only",
        "e.printStackTrace()",
        "System.out/err",
        "private method may bypass Spring proxy",
        "legacy javax import",
    ):
        assert message in result.stdout, message


def test_static_review_jakarta_namespace_flag_is_explicit():
    result = subprocess.run([sys.executable, str(STATIC), "--namespace", "jakarta", str(FIXTURES / "static/bad/src/main/java/example/BadService.java")], capture_output=True, text=True, encoding="utf-8")
    assert "legacy javax import" in result.stdout


def test_static_review_allows_plaintext_config_but_reports_java_secret(tmp_path: Path):
    config = tmp_path / "application.yml"
    config.write_text("password: abcdefgh1234\n", encoding="utf-8")
    source = tmp_path / "Config.java"
    source.write_text('class Config { String password = "' + "abcdefgh" + '1234"; }\n', encoding="utf-8")
    result = subprocess.run([sys.executable, str(STATIC), str(tmp_path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1
    assert "Config.java" in result.stdout
    assert "application.yml" not in result.stdout


def test_static_review_reports_secrets_in_non_java_source_files(tmp_path: Path):
    (tmp_path / "a.kt").write_text('val password = "' + "Abc12345" + 'xyz"\n', encoding="utf-8")
    (tmp_path / "b.ts").write_text('const password = "' + "Abc12345" + 'xyz";\n', encoding="utf-8")
    (tmp_path / "application.yml").write_text("password: abcdefgh1234\n", encoding="utf-8")
    result = subprocess.run([sys.executable, str(STATIC), str(tmp_path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1
    assert "a.kt" in result.stdout
    assert "b.ts" in result.stdout
    assert "application.yml" not in result.stdout


def test_static_review_ignores_non_literal_secret_values_but_reports_quoted_literal(tmp_path: Path):
    (tmp_path / "ok.py").write_text('token = os.environ.get("X")\n', encoding="utf-8")
    (tmp_path / "ok.ts").write_text("const password = import.meta.env.VITE_PASSWORD\n", encoding="utf-8")
    result = subprocess.run([sys.executable, str(STATIC), str(tmp_path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0
    assert "No P0/P1" in result.stdout
    (tmp_path / "bad.py").write_text('api_token = "' + "abcdefgh" + '1234"\n', encoding="utf-8")
    result = subprocess.run([sys.executable, str(STATIC), str(tmp_path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1
    assert "bad.py" in result.stdout
    assert "ok.py" not in result.stdout
    assert "ok.ts" not in result.stdout


def test_resolve_packs_auto_detects_java_files(tmp_path: Path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    assert index.resolve_packs("auto", tmp_path, ["java"], False) == ["java"]
    (tmp_path / "pom.xml").unlink()
    (tmp_path / "build.gradle.kts").write_text("plugins {}", encoding="utf-8")
    assert index.resolve_packs("auto", tmp_path, ["java"], False) == ["java"]


def test_install_doctor_uninstall_java_pack(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    install = subprocess.run([sys.executable, str(AICJ), "install", "--home", str(home), "--source", str(REPO), "--packs", "java"], capture_output=True, text=True, encoding="utf-8")
    assert install.returncode == 0, install.stdout + install.stderr
    for name in ("java-spring-boot.md", "java-sql-mybatis.md", "java-modern.md"):
        assert (home / ".claude/rules" / name).is_file()
    assert (home / ".claude/skills/java-verify/SKILL.md").is_file()
    doctor = subprocess.run([sys.executable, str(AICJ), "doctor", "--home", str(home)], capture_output=True, text=True, encoding="utf-8")
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "MISSING" not in doctor.stdout
    uninstall = subprocess.run([sys.executable, str(AICJ), "uninstall", "--home", str(home)], capture_output=True, text=True, encoding="utf-8")
    assert uninstall.returncode == 0, uninstall.stdout + uninstall.stderr
    assert list(home.rglob("*")) == []


def test_static_review_typed_and_quoted_key_literals_and_placeholders(tmp_path: Path):
    fake = "abcdefgh" + "1234"
    (tmp_path / "a.ts").write_text("const token: string = '" + fake + "'\n", encoding="utf-8")
    (tmp_path / "b.kt").write_text('val password: String = "' + fake + '"\n', encoding="utf-8")
    (tmp_path / "c.ts").write_text('const o = {"password": "' + fake + '"}\n', encoding="utf-8")
    (tmp_path / "d.ts").write_text('const password = "${DB_' + 'PASSWORD}"\n', encoding="utf-8")
    (tmp_path / "e.ts").write_text('const token = "<your-' + 'token>"\n', encoding="utf-8")
    result = subprocess.run([sys.executable, str(STATIC), str(tmp_path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1
    for name in ("a.ts", "b.kt", "c.ts"):
        assert name in result.stdout
    for name in ("d.ts", "e.ts"):
        assert name not in result.stdout


def test_static_review_ignores_keyword_prefixed_identifiers(tmp_path: Path):
    (tmp_path / "ok.py").write_text(
        'TOKEN_URL = "https://auth.example.com/t"\n'
        'token_type = "Bearer1234"\n'
        'tokenizer = "bert-base-uncased"\n'
        'secret_name = "my-secret-name"\n',
        encoding="utf-8",
    )
    (tmp_path / "ok.ts").write_text('const labels = {"passwordLabel": "PasswordInput"}\n', encoding="utf-8")
    (tmp_path / "bad.ts").write_text('const secretKey = "' + "abcdefgh" + '1234"\n', encoding="utf-8")
    result = subprocess.run([sys.executable, str(STATIC), str(tmp_path)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1
    assert "bad.ts" in result.stdout
    assert "ok.py" not in result.stdout
    assert "ok.ts" not in result.stdout
