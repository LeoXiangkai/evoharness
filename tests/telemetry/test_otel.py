from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from .conftest import REPO_ROOT, make_remote
from .test_team_e2e import run_aicj

sys.path.insert(0, str(REPO_ROOT / "installer"))
from lib import doctor, telemetry  # noqa: E402
from lib.util import UserError  # noqa: E402

ENDPOINT = "http://127.0.0.1:4318"
BEARER = "sk-test-secret-token-0123456789"
FROM_SETTINGS = "from-settings"
HELPER_SRC = REPO_ROOT / "core/hooks/aicj_otel_headers.py"
ENV_KEYS = {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": ENDPOINT,
    "OTEL_LOG_USER_PROMPTS": "1",
    "OTEL_LOG_ASSISTANT_RESPONSES": "1",
    "OTEL_LOG_TOOL_DETAILS": "1",
}


def _team(**extra) -> bytes:
    team = {"team_id": "team1", "collect_repo": "https://example.test/x.git", "salt": "s"}
    team.update(extra)
    return json.dumps(team).encode("utf-8")


# ---------------------------------------------------------------- validate

@pytest.mark.parametrize("raw,want", [
    ("http://127.0.0.1:4318", "http://127.0.0.1:4318"),
    ("https://OTEL.example.test:4318/", "https://otel.example.test:4318"),
    ("  http://127.0.0.1:4318//  ", "http://127.0.0.1:4318"),
])
def test_validate_accepts_and_normalizes(raw, want):
    data = telemetry.validate_team_bytes(_team(otel_endpoint=raw))
    assert json.loads(data)["otel_endpoint"] == want
    assert telemetry.otel_endpoint(data) == want


@pytest.mark.parametrize("raw", [
    "ftp://o.example.test", "http://", "o.example.test", "https://user:pw@o.example.test",
    "https://o.example.test/?a=1", "https://o.example.test/#f", "http://o.example.test:bad",
    "", "   ", "https://o .example.test", 123, None, True, ["http://o.example.test"],
])
def test_validate_rejects_naming_field_without_echo(raw):
    with pytest.raises(UserError) as err:
        telemetry.validate_team_bytes(_team(otel_endpoint=raw))
    message = str(err.value)
    assert "otel_endpoint" in message
    if isinstance(raw, str) and raw.strip():
        assert raw.strip() not in message


def test_validate_without_field():
    data = telemetry.validate_team_bytes(_team())
    assert "otel_endpoint" not in json.loads(data)
    assert telemetry.otel_endpoint(data) is None
    assert telemetry.otel_endpoint(None) is None


# ---------------------------------------------------------------- install / uninstall

def _team_file(tmp_path: Path, **extra) -> Path:
    remote = make_remote(tmp_path)
    p = tmp_path / "team-in.json"
    p.write_text(json.dumps({"team_id": "team1", "collect_repo": str(remote), "salt": "salt-abc",
                             "branch": "main", **extra}), encoding="utf-8")
    return p


def _rewrite(team: Path, drop: tuple[str, ...] = (), **extra) -> Path:
    raw = {**json.loads(team.read_text(encoding="utf-8")), **extra}
    for key in drop:
        raw.pop(key, None)
    team.write_text(json.dumps(raw), encoding="utf-8")
    return team


def _install(home, team, *extra):
    return run_aicj("install", "--packs", "none", "--team-config", str(team), *extra, home=home, source=REPO_ROOT)


def _uninstall(home, *extra):
    return run_aicj("uninstall", *extra, home=home, source=REPO_ROOT)


def _settings(home) -> dict:
    return json.loads((home / ".claude/settings.json").read_text(encoding="utf-8"))


def _write_settings(home, payload: dict) -> None:
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude/settings.json").write_text(json.dumps(payload), encoding="utf-8")


def _manifest(home) -> dict:
    return json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))


def _helper_value(home) -> str:
    return f'"{os.path.abspath(sys.executable)}" "{home / ".claude" / "hooks/aicj/aicj_otel_headers.py"}"'


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_install_writes_all_nine_keys(tmp_path, home):
    _write_settings(home, {"env": {"FOO": "bar"}})
    result = _install(home, _team_file(tmp_path, otel_endpoint=ENDPOINT + "/"))
    assert result.returncode == 0, result.stderr
    payload = _settings(home)
    assert payload["env"] == {"FOO": "bar", **ENV_KEYS}
    assert payload["otelHeadersHelper"] == _helper_value(home)
    assert (home / ".claude/hooks/aicj/aicj_otel_headers.py").is_file()
    record = _manifest(home)["otel"]
    assert set(record["env"]) == set(ENV_KEYS) and set(record["top"]) == {"otelHeadersHelper"}
    assert all("previous" not in row for row in record["env"].values())


def test_install_without_field_leaves_settings_alone(tmp_path, home):
    result = _install(home, _team_file(tmp_path))
    assert result.returncode == 0, result.stderr
    payload = _settings(home)
    assert "env" not in payload and "otelHeadersHelper" not in payload
    assert _manifest(home)["otel"] == {}


def test_install_conflict_skips_whole_block(tmp_path, home):
    _write_settings(home, {"env": {"OTEL_LOGS_EXPORTER": "console"}})
    result = _install(home, _team_file(tmp_path, otel_endpoint=ENDPOINT))
    assert result.returncode == 0, result.stderr
    payload = _settings(home)
    assert payload["env"] == {"OTEL_LOGS_EXPORTER": "console"}
    assert "otelHeadersHelper" not in payload
    assert "--on-conflict backup" in result.stdout + result.stderr
    assert _manifest(home)["otel"] == {}


def test_install_top_level_conflict_skips_whole_block(tmp_path, home):
    _write_settings(home, {"otelHeadersHelper": "my-helper"})
    result = _install(home, _team_file(tmp_path, otel_endpoint=ENDPOINT))
    assert result.returncode == 0, result.stderr
    payload = _settings(home)
    assert payload["otelHeadersHelper"] == "my-helper" and "env" not in payload


def test_install_conflict_backup_records_previous_and_uninstall_restores(tmp_path, home):
    original = {"env": {"OTEL_LOGS_EXPORTER": "console", "X": "1"}, "otelHeadersHelper": "my-helper"}
    _write_settings(home, original)
    result = _install(home, _team_file(tmp_path, otel_endpoint=ENDPOINT), "--on-conflict", "backup")
    assert result.returncode == 0, result.stderr
    payload = _settings(home)
    assert payload["env"]["OTEL_LOGS_EXPORTER"] == "otlp" and payload["otelHeadersHelper"] == _helper_value(home)
    record = _manifest(home)["otel"]
    assert record["env"]["OTEL_LOGS_EXPORTER"]["previous"] == "console"
    assert record["top"]["otelHeadersHelper"]["previous"] == "my-helper"
    assert _uninstall(home).returncode == 0
    assert _settings(home) == original


def test_uninstall_removes_block_created_file_and_helper(tmp_path, home):
    assert _install(home, _team_file(tmp_path, otel_endpoint=ENDPOINT)).returncode == 0
    assert (home / ".claude/hooks/aicj/aicj_otel_headers.py").exists()
    assert _uninstall(home).returncode == 0
    assert not (home / ".claude/hooks/aicj/aicj_otel_headers.py").exists()
    assert not (home / ".claude/settings.json").exists()


def test_uninstall_with_gateway_and_otel_leaves_no_settings_file(tmp_path, home):
    team = _team_file(tmp_path, otel_endpoint=ENDPOINT, gateway_base_url="http://127.0.0.1:8090")
    assert _install(home, team).returncode == 0
    assert _uninstall(home).returncode == 0
    assert not (home / ".claude/settings.json").exists()


def test_uninstall_otel_then_gateway_added_leaves_no_settings_file(tmp_path, home):
    team = _team_file(tmp_path, otel_endpoint=ENDPOINT)
    assert _install(home, team).returncode == 0
    assert _install(home, _rewrite(team, gateway_base_url="http://127.0.0.1:8090")).returncode == 0
    assert _uninstall(home).returncode == 0
    assert not (home / ".claude/settings.json").exists()


def test_install_backup_does_not_cover_non_string_value(tmp_path, home):
    original = {"env": {"CLAUDE_CODE_ENABLE_TELEMETRY": 1}}
    _write_settings(home, original)
    result = _install(home, _team_file(tmp_path, otel_endpoint=ENDPOINT), "--on-conflict", "backup")
    assert result.returncode == 0, result.stderr
    assert "OTel block skipped" in result.stdout + result.stderr
    assert _settings(home)["env"] == original["env"] and "otelHeadersHelper" not in _settings(home)


def test_uninstall_keeps_changed_key_with_note(tmp_path, home):
    _write_settings(home, {"theme": "dark"})
    assert _install(home, _team_file(tmp_path, otel_endpoint=ENDPOINT)).returncode == 0
    payload = _settings(home)
    payload["env"]["OTEL_LOG_TOOL_DETAILS"] = "0"
    _write_settings(home, payload)
    result = _uninstall(home)
    assert result.returncode == 0, result.stderr
    assert _settings(home) == {"theme": "dark", "env": {"OTEL_LOG_TOOL_DETAILS": "0"}}
    assert "OTEL_LOG_TOOL_DETAILS changed since install, kept" in result.stdout + result.stderr


def test_retarget_endpoint_replaces_owned_value(tmp_path, home):
    team = _team_file(tmp_path, otel_endpoint=ENDPOINT)
    assert _install(home, team).returncode == 0
    new = "http://127.0.0.1:4319"
    assert _install(home, _rewrite(team, otel_endpoint=new)).returncode == 0
    assert _settings(home)["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == new
    assert _manifest(home)["otel"]["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"]["value"] == new
    assert _uninstall(home).returncode == 0
    assert not (home / ".claude/settings.json").exists()


def test_field_dropped_keeps_block_and_still_uninstallable(tmp_path, home):
    team = _team_file(tmp_path, otel_endpoint=ENDPOINT)
    assert _install(home, team).returncode == 0
    assert _install(home, _rewrite(team, drop=("otel_endpoint",))).returncode == 0
    assert _settings(home)["env"]["OTEL_EXPORTER_OTLP_ENDPOINT"] == ENDPOINT
    assert _uninstall(home).returncode == 0
    assert not (home / ".claude/settings.json").exists()


def test_reinstall_same_is_idempotent(tmp_path, home):
    team = _team_file(tmp_path, otel_endpoint=ENDPOINT)
    assert _install(home, team).returncode == 0
    before = (home / ".claude/settings.json").read_bytes()
    assert _install(home, team).returncode == 0
    assert (home / ".claude/settings.json").read_bytes() == before


def test_install_rejects_non_object_env_before_writing(tmp_path, home):
    _write_settings(home, {"env": "oops"})
    result = _install(home, _team_file(tmp_path, otel_endpoint=ENDPOINT))
    assert result.returncode == 2
    assert not (home / ".claude/aicj/manifest.json").exists()


def test_install_never_touches_key_variables(tmp_path, home):
    _write_settings(home, {"env": {"ANTHROPIC_AUTH_TOKEN": BEARER}})
    result = _install(home, _team_file(tmp_path, otel_endpoint=ENDPOINT))
    assert result.returncode == 0, result.stderr
    assert _settings(home)["env"]["ANTHROPIC_AUTH_TOKEN"] == BEARER
    assert BEARER not in result.stdout + result.stderr
    assert BEARER not in (home / ".claude/aicj/manifest.json").read_text(encoding="utf-8")


def test_dry_run_writes_nothing(tmp_path, home):
    team = _team_file(tmp_path, otel_endpoint=ENDPOINT)
    result = run_aicj("install", "--packs", "none", "--team-config", str(team), "--dry-run",
                      home=home, source=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert list(home.rglob("*")) == []


# ---------------------------------------------------------------- helper script

def _run_helper(script: Path, env_extra: dict[str, str] | None = None):
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY")}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env, timeout=30)


@pytest.fixture
def helper(tmp_path):
    claude = tmp_path / "hhome/.claude"
    (claude / "hooks/aicj").mkdir(parents=True)
    script = claude / "hooks/aicj/aicj_otel_headers.py"
    script.write_bytes(HELPER_SRC.read_bytes())
    return script


def test_helper_prefers_process_env_and_trims(helper):
    (helper.parents[2] / "settings.json").write_text(json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": FROM_SETTINGS}}))
    out = _run_helper(helper, {"ANTHROPIC_AUTH_TOKEN": "   ", "ANTHROPIC_API_KEY": f" {BEARER} "})
    assert out.returncode == 0 and out.stderr == ""
    assert json.loads(out.stdout) == {"Authorization": f"Bearer {BEARER}"}


def test_helper_falls_back_to_settings_next_to_script(helper):
    (helper.parents[2] / "settings.json").write_text(
        json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": "", "ANTHROPIC_API_KEY": BEARER}}))
    out = _run_helper(helper)
    assert out.returncode == 0 and out.stderr == ""
    assert json.loads(out.stdout) == {"Authorization": f"Bearer {BEARER}"}


@pytest.mark.parametrize("content", [None, "not json", "[]", '{"env": "x"}', '{"env": {}}'])
def test_helper_without_key_prints_empty_object(helper, content):
    if content is not None:
        (helper.parents[2] / "settings.json").write_text(content)
    out = _run_helper(helper)
    assert out.returncode == 0 and out.stderr == ""
    assert json.loads(out.stdout) == {}


# ---------------------------------------------------------------- doctor

class _Logs(BaseHTTPRequestHandler):
    status = 200
    seen: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        type(self).seen.append((self.path, self.headers.get("Authorization"),
                                self.headers.get("Content-Type"), body))
        self.send_response(type(self).status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args):
        return


@pytest.fixture
def logs_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    handler = type("H", (_Logs,), {"seen": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handler.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield handler
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _installed_home(tmp_path, url, *, token=BEARER):
    home = tmp_path / "dhome"
    home.mkdir()
    assert _install(home, _team_file(tmp_path, otel_endpoint=url)).returncode == 0
    if token:
        payload = _settings(home)
        payload["env"]["ANTHROPIC_AUTH_TOKEN"] = token
        _write_settings(home, payload)
    return home


def _rows(home) -> list[tuple[str, str, str]]:
    rows = doctor._team_otel(home)
    assert {r[1] for r in rows} <= {"team-otel"}
    assert BEARER not in repr(rows)
    return rows


def test_doctor_no_field_has_no_rows(tmp_path):
    home = tmp_path / "h"
    (home / ".claude/aicj").mkdir(parents=True)
    (home / ".claude/aicj/team.json").write_bytes(telemetry.validate_team_bytes(_team()))
    assert doctor._team_otel(home) == []


def test_doctor_pass_sends_empty_logs_with_bearer(tmp_path, logs_server):
    home = _installed_home(tmp_path, logs_server.url)
    (status, _n, _d), = _rows(home)
    assert status == doctor.PASS
    (path, auth, ctype, body), = logs_server.seen
    assert path == "/v1/logs" and auth == f"Bearer {BEARER}"
    assert ctype == "application/json" and json.loads(body) == {"resourceLogs": []}


def test_doctor_settings_key_wins_over_shell_key(tmp_path, logs_server, monkeypatch):
    home = _installed_home(tmp_path, logs_server.url)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "shell-key-for-another-backend")
    (status, _n, _d), = _rows(home)
    assert status == doctor.PASS
    (_path, auth, _ctype, _body), = logs_server.seen
    assert auth == f"Bearer {BEARER}"


@pytest.mark.parametrize("code", [401, 403])
def test_doctor_auth_rejected(tmp_path, logs_server, code):
    logs_server.status = code
    home = _installed_home(tmp_path, logs_server.url)
    (status, _n, detail), = _rows(home)
    assert status == doctor.WARN and detail == "key 无效或已停用"


def test_doctor_other_status_reports_code(tmp_path, logs_server):
    logs_server.status = 500
    home = _installed_home(tmp_path, logs_server.url)
    (status, _n, detail), = _rows(home)
    assert status == doctor.WARN and "HTTP 500" in detail


def test_doctor_no_key_warns_and_sends_nothing(tmp_path, logs_server):
    home = _installed_home(tmp_path, logs_server.url, token="")
    (status, _n, detail), = _rows(home)
    assert status == doctor.WARN and "未找到 key" in detail
    assert logs_server.seen == []


def test_doctor_unreachable(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    home = _installed_home(tmp_path, f"http://127.0.0.1:{port}")
    (status, _n, detail), = _rows(home)
    assert status == doctor.WARN and "不可达" in detail and f"127.0.0.1:{port}" in detail


def test_doctor_drift_and_missing_helper(tmp_path, logs_server):
    home = _installed_home(tmp_path, logs_server.url)
    payload = _settings(home)
    payload["env"]["OTEL_LOGS_EXPORTER"] = "console"
    _write_settings(home, payload)
    (home / ".claude/hooks/aicj/aicj_otel_headers.py").unlink()
    rows = _rows(home)
    assert len(rows) == 1 and rows[0][0] == doctor.WARN
    assert "OTEL_LOGS_EXPORTER" in rows[0][2] and "helper 脚本缺失" in rows[0][2]
    assert logs_server.seen == []


def test_doctor_token_never_in_cli_output(tmp_path, logs_server):
    home = _installed_home(tmp_path, logs_server.url)
    result = run_aicj("doctor", home=home, source=REPO_ROOT)
    assert "team-otel" in result.stdout + result.stderr
    assert BEARER not in result.stdout + result.stderr
