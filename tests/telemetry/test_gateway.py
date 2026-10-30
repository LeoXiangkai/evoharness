from __future__ import annotations

import json
import socket
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

KEY = "ANTHROPIC_BASE_URL"
GATEWAY = "http://127.0.0.1:8090"
BEARER = "sk-test-secret-token-0123456789"


def _team(**extra) -> bytes:
    team = {"team_id": "team1", "collect_repo": "https://example.test/x.git", "salt": "s"}
    team.update(extra)
    return json.dumps(team).encode("utf-8")


# ---------------------------------------------------------------- validate

@pytest.mark.parametrize("raw,want", [
    ("http://gw.example.test", "http://gw.example.test"),
    ("https://gw.example.test:8443/", "https://gw.example.test:8443"),
    ("https://GW.example.test/api//", "https://gw.example.test/api"),
    ("  http://127.0.0.1:8090/  ", "http://127.0.0.1:8090"),
])
def test_validate_accepts_and_normalizes(raw, want):
    out = json.loads(telemetry.validate_team_bytes(_team(gateway_base_url=raw)))
    assert out["gateway_base_url"] == want


@pytest.mark.parametrize("raw", [
    "ftp://gw.example.test", "http://", "gw.example.test", "https://user:pw@gw.example.test",
    "https://user@gw.example.test", "https://gw.example.test/?a=1", "https://gw.example.test/#frag",
    "https://gw.example.test?", "http://gw.example.test:notaport", "", "   ", "https://gw .example.test",
    123, None, ["https://gw.example.test"], True,
])
def test_validate_rejects_without_echoing_value(raw):
    with pytest.raises(UserError) as err:
        telemetry.validate_team_bytes(_team(gateway_base_url=raw))
    message = str(err.value)
    assert "gateway_base_url" in message
    if isinstance(raw, str) and raw.strip():
        assert raw.strip() not in message
    assert "pw@" not in message


def test_validate_without_field_is_unchanged():
    out = json.loads(telemetry.validate_team_bytes(_team()))
    assert "gateway_base_url" not in out
    assert telemetry.gateway_url(telemetry.validate_team_bytes(_team())) is None


# ---------------------------------------------------------------- install / uninstall

def _team_file(tmp_path: Path, **extra) -> Path:
    remote = make_remote(tmp_path)
    p = tmp_path / "team-in.json"
    p.write_text(json.dumps({"team_id": "team1", "collect_repo": str(remote), "salt": "salt-abc",
                             "branch": "main", **extra}), encoding="utf-8")
    return p


def _retarget(team: Path, **extra) -> Path:
    team.write_text(json.dumps({**json.loads(team.read_text(encoding="utf-8")), **extra}), encoding="utf-8")
    return team


def _install(home, team, *extra):
    return run_aicj("install", "--packs", "none", "--team-config", str(team), *extra, home=home, source=REPO_ROOT)


def _uninstall(home, *extra):
    return run_aicj("uninstall", *extra, home=home, source=REPO_ROOT)


def _settings(home) -> dict:
    return json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))


def _write_settings(home, payload: dict) -> None:
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "settings.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


def test_install_writes_env_and_keeps_other_env(tmp_path, home):
    _write_settings(home, {"env": {"FOO": "bar"}})
    team = _team_file(tmp_path, gateway_base_url=GATEWAY + "/")
    result = _install(home, team)
    assert result.returncode == 0, result.stderr
    env = _settings(home)["env"]
    assert env == {"FOO": "bar", KEY: GATEWAY}
    manifest = json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))
    assert manifest["gateway"] == {"value": GATEWAY}


def test_install_without_gateway_field_leaves_env_alone(tmp_path, home):
    result = _install(home, _team_file(tmp_path))
    assert result.returncode == 0, result.stderr
    assert "env" not in _settings(home)
    manifest = json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))
    assert manifest["gateway"] == {}


def test_install_same_value_is_idempotent_and_owned(tmp_path, home):
    _write_settings(home, {"env": {KEY: GATEWAY}})
    team = _team_file(tmp_path, gateway_base_url=GATEWAY)
    assert _install(home, team).returncode == 0
    before = (home / ".claude/settings.json").read_bytes()
    assert _install(home, team).returncode == 0
    manifest = json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))
    assert manifest["gateway"]["value"] == GATEWAY
    assert manifest["gateway"]["previous"] == GATEWAY
    assert _settings(home)["env"] == {KEY: GATEWAY}
    assert (home / ".claude/settings.json").read_bytes() == before


def test_install_conflict_skips_and_keeps_original(tmp_path, home):
    _write_settings(home, {"env": {KEY: "https://other.example.test"}})
    result = _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY))
    assert result.returncode == 0, result.stderr
    assert _settings(home)["env"][KEY] == "https://other.example.test"
    assert "differs; rerun with --on-conflict backup" in result.stdout + result.stderr
    manifest = json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))
    assert manifest["gateway"] == {}


def test_install_conflict_backup_overwrites_and_records_previous(tmp_path, home):
    _write_settings(home, {"env": {KEY: "https://other.example.test", "X": "1"}})
    result = _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY), "--on-conflict", "backup")
    assert result.returncode == 0, result.stderr
    assert _settings(home)["env"] == {KEY: GATEWAY, "X": "1"}
    manifest = json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))
    assert manifest["gateway"] == {"value": GATEWAY, "previous": "https://other.example.test"}


def test_install_dry_run_writes_nothing(tmp_path, home):
    team = _team_file(tmp_path, gateway_base_url=GATEWAY)
    result = run_aicj("install", "--packs", "none", "--team-config", str(team), "--dry-run",
                      home=home, source=REPO_ROOT)
    assert result.returncode == 0, result.stderr
    assert list(home.rglob("*")) == []


def test_install_never_touches_key_variables(tmp_path, home):
    _write_settings(home, {"env": {"ANTHROPIC_AUTH_TOKEN": BEARER, "ANTHROPIC_API_KEY": "k2"}})
    result = _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY))
    assert result.returncode == 0, result.stderr
    env = _settings(home)["env"]
    assert env["ANTHROPIC_AUTH_TOKEN"] == BEARER and env["ANTHROPIC_API_KEY"] == "k2"
    assert BEARER not in result.stdout + result.stderr
    assert BEARER not in (home / ".claude/aicj/manifest.json").read_text(encoding="utf-8")


def test_install_rejects_non_object_env_before_writing(tmp_path, home):
    _write_settings(home, {"env": "oops"})
    result = _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY))
    assert result.returncode == 2
    assert not (home / ".claude/aicj/manifest.json").exists()


def test_uninstall_removes_value_and_empty_env(tmp_path, home):
    _write_settings(home, {"theme": "dark"})
    assert _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY)).returncode == 0
    assert _uninstall(home).returncode == 0
    assert _settings(home) == {"theme": "dark"}


def test_uninstall_keeps_other_env_keys(tmp_path, home):
    _write_settings(home, {"env": {"FOO": "bar"}})
    assert _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY)).returncode == 0
    assert _uninstall(home).returncode == 0
    assert _settings(home) == {"env": {"FOO": "bar"}}


def test_uninstall_restores_previous_value(tmp_path, home):
    _write_settings(home, {"env": {KEY: "https://other.example.test"}})
    assert _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY), "--on-conflict", "backup").returncode == 0
    assert _uninstall(home).returncode == 0
    assert _settings(home) == {"env": {KEY: "https://other.example.test"}}


def test_uninstall_keeps_value_changed_by_user(tmp_path, home):
    assert _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY)).returncode == 0
    payload = _settings(home)
    payload["env"][KEY] = "https://mine.example.test"
    _write_settings(home, payload)
    result = _uninstall(home)
    assert result.returncode == 0, result.stderr
    assert _settings(home)["env"][KEY] == "https://mine.example.test"
    assert "changed since install" in result.stdout + result.stderr


def test_uninstall_dry_run_keeps_env(tmp_path, home):
    assert _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY)).returncode == 0
    assert _uninstall(home, "--dry-run").returncode == 0
    assert _settings(home)["env"][KEY] == GATEWAY


# ---------------------------------------------------------------- doctor

class _Usage(BaseHTTPRequestHandler):
    status = 200
    body = b"{}"
    seen: list[str] = []

    def do_GET(self):  # noqa: N802
        type(self).seen.append(f"{self.path}|{self.headers.get('Authorization')}")
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *args):
        return


@pytest.fixture
def usage_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    handler = type("H", (_Usage,), {"seen": []})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    handler.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield handler
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _usage(daily=(0.5, 5), weekly=(None, None), monthly=(1.0, 1000), remaining=10):
    sub = {"daily_usage_usd": daily[0], "daily_limit_usd": daily[1], "weekly_usage_usd": weekly[0],
           "weekly_limit_usd": weekly[1], "monthly_usage_usd": monthly[0], "monthly_limit_usd": monthly[1]}
    return json.dumps({"isValid": True, "remaining": remaining, "subscription": sub, "unit": "USD"}).encode()


def _doctor_home(tmp_path, url, *, env=None):
    home = tmp_path / "dhome"
    claude = home / ".claude"
    (claude / "aicj").mkdir(parents=True)
    (claude / "aicj" / "team.json").write_text(
        json.dumps({"team_id": "team1", "collect_repo": "https://example.test/x.git", "salt": "s",
                    "gateway_base_url": url}), encoding="utf-8")
    (claude / "settings.json").write_text(json.dumps({"env": {KEY: url, **(env or {})}}), encoding="utf-8")
    return home


def _tree(home: Path) -> dict[str, bytes]:
    return {p.relative_to(home).as_posix(): p.read_bytes() for p in home.rglob("*") if p.is_file()}


def _run(home) -> list[tuple[str, str, str]]:
    before = _tree(home)
    rows = doctor._gateway(home)
    assert _tree(home) == before, "doctor must not write files"
    assert {r[1] for r in rows} == {"team-gateway"}
    assert BEARER not in repr(rows)
    return rows


def test_doctor_no_gateway_field_has_no_rows(tmp_path):
    home = tmp_path / "h"
    (home / ".claude/aicj").mkdir(parents=True)
    (home / ".claude/aicj/team.json").write_bytes(telemetry.validate_team_bytes(_team()))
    assert doctor._gateway(home) == []


def test_doctor_pass_shows_amounts(tmp_path, usage_server):
    usage_server.body = _usage(daily=(0.5, 5), monthly=(1.234, 1000))
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (status, _name, detail), = _run(home)
    assert status == doctor.PASS
    assert detail == "今日 $0.50/$5.00，本月 $1.23/$1000.00"
    assert usage_server.seen == [f"/v1/usage|Bearer {BEARER}"]


def test_doctor_daily_exhausted(tmp_path, usage_server):
    usage_server.body = _usage(daily=(0.00408, 0.004), monthly=(0.00408, 1000), remaining=0)
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (status, _n, detail), = _run(home)
    assert status == doctor.WARN
    assert "额度已用完（今日）" in detail and "$0.00/$0.00" in detail


def test_doctor_monthly_exhausted(tmp_path, usage_server):
    usage_server.body = _usage(daily=(1, 5), monthly=(1000, 1000), remaining=0)
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (status, _n, detail), = _run(home)
    assert status == doctor.WARN and "额度已用完（本月）" in detail and "本月 $1000.00/$1000.00" in detail


def test_doctor_remaining_zero_without_tier_hit(tmp_path, usage_server):
    usage_server.body = _usage(daily=(1, 5), monthly=(1, 10), remaining=0)
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (status, _n, detail), = _run(home)
    assert status == doctor.WARN and "额度已用完" in detail


def test_doctor_null_limit_tier_is_hidden(tmp_path, usage_server):
    usage_server.body = _usage(weekly=(2, None))
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (status, _n, detail), = _run(home)
    assert status == doctor.PASS
    assert detail == "今日 $0.50/$5.00，本月 $1.00/$1000.00"


@pytest.mark.parametrize("status", [401, 403])
def test_doctor_auth_rejected(tmp_path, usage_server, status):
    usage_server.status = status
    usage_server.body = b'{"error":"nope"}'
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (st, _n, detail), = _run(home)
    assert st == doctor.WARN and detail == "key 无效或已停用"


def test_doctor_invalid_code_in_body(tmp_path, usage_server):
    usage_server.status = 200
    usage_server.body = b'{"error":{"code":"INVALID_API_KEY"}}'
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (st, _n, detail), = _run(home)
    assert st == doctor.WARN and detail == "key 无效或已停用"


def test_doctor_unreachable_shows_host_only(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    for name in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    home = _doctor_home(tmp_path, f"http://127.0.0.1:{port}", env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (st, _n, detail), = _run(home)
    assert st == doctor.WARN and detail == f"网关不可达：127.0.0.1:{port}"


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}", b'{"subscription":"x"}',
                                  b'{"subscription":{"daily_limit_usd":"5","daily_usage_usd":1}}'])
def test_doctor_unparseable(tmp_path, usage_server, body):
    usage_server.body = body
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (st, _n, detail), = _run(home)
    assert st == doctor.WARN and detail == "无法解析额度"


def test_doctor_without_token(tmp_path, usage_server):
    home = _doctor_home(tmp_path, usage_server.url)
    (st, _n, detail), = _run(home)
    assert st == doctor.WARN and "ANTHROPIC_AUTH_TOKEN" in detail
    assert usage_server.seen == []


def test_doctor_token_from_process_env(tmp_path, usage_server, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", BEARER)
    usage_server.body = _usage()
    home = _doctor_home(tmp_path, usage_server.url)
    (st, _n, _d), = _run(home)
    assert st == doctor.PASS
    assert usage_server.seen == [f"/v1/usage|Bearer {BEARER}"]


def test_doctor_settings_token_wins_over_api_key(tmp_path, usage_server):
    usage_server.body = _usage()
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER, "ANTHROPIC_API_KEY": "other"})
    _run(home)
    assert usage_server.seen == [f"/v1/usage|Bearer {BEARER}"]


def test_doctor_base_url_mismatch_shows_host_only(tmp_path, usage_server):
    usage_server.body = _usage()
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    secret_url = "https://api.example.test/v1/path-with-secret"
    (home / ".claude/settings.json").write_text(
        json.dumps({"env": {KEY: secret_url, "ANTHROPIC_AUTH_TOKEN": BEARER}}), encoding="utf-8")
    rows = _run(home)
    assert rows[0][0] == doctor.WARN and rows[0][2].startswith("未接入团队网关")
    assert "api.example.test" in rows[0][2] and "path-with-secret" not in rows[0][2]
    assert rows[1][0] == doctor.PASS


def test_doctor_does_not_follow_redirect_with_token(tmp_path, usage_server):
    other = type("O", (_Usage,), {"seen": []})
    server = HTTPServer(("127.0.0.1", 0), other)
    front = None
    threads = [threading.Thread(target=server.serve_forever, daemon=True)]
    threads[0].start()
    try:
        class Redirect(_Usage):
            def do_GET(self):  # noqa: N802
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{server.server_address[1]}/v1/usage")
                self.end_headers()
        front = HTTPServer(("127.0.0.1", 0), Redirect)
        threads.append(threading.Thread(target=front.serve_forever, daemon=True))
        threads[1].start()
        home = _doctor_home(tmp_path, f"http://127.0.0.1:{front.server_address[1]}",
                            env={"ANTHROPIC_AUTH_TOKEN": BEARER})
        (st, _n, detail), = _run(home)
        assert st == doctor.WARN and detail == "无法解析额度（HTTP 302）"
        assert other.seen == []
    finally:
        for srv in (front, server):
            if srv is not None:
                srv.shutdown()
                srv.server_close()
        for t in threads:
            t.join(timeout=5)


def test_doctor_remaining_negative_is_unlimited(tmp_path, usage_server):
    usage_server.body = _usage(daily=(1, 5), monthly=(1, 10), remaining=-1)
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (status, _n, _d), = _run(home)
    assert status == doctor.PASS


def test_doctor_remaining_zero_warns(tmp_path, usage_server):
    usage_server.body = _usage(daily=(1, 5), monthly=(1, 10), remaining=0)
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (status, _n, detail), = _run(home)
    assert status == doctor.WARN and "额度已用完（剩余额度）" in detail


def test_doctor_http_400_is_not_invalid_key(tmp_path, usage_server):
    usage_server.status = 400
    usage_server.body = b'{"error":{"type":"invalid_request_error"}}'
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (st, _n, detail), = _run(home)
    assert st == doctor.WARN and detail == "无法解析额度（HTTP 400）"


def test_doctor_deeply_nested_body_does_not_crash(tmp_path, usage_server):
    usage_server.body = b"[" * 200000
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (st, _n, detail), = _run(home)
    assert st == doctor.WARN and detail == "无法解析额度"


def test_doctor_huge_integer_limit_does_not_crash(tmp_path, usage_server):
    usage_server.body = (b'{"remaining":1,"subscription":{"daily_limit_usd":' + b"9" * 400
                         + b',"daily_usage_usd":1}}')
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (st, _n, detail), = _run(home)
    assert st == doctor.WARN and detail == "无法解析额度"


def test_doctor_uppercase_url_counts_as_connected(tmp_path, usage_server):
    usage_server.body = _usage()
    home = _doctor_home(tmp_path, usage_server.url, env={"ANTHROPIC_AUTH_TOKEN": BEARER})
    (home / ".claude/settings.json").write_text(
        json.dumps({"env": {KEY: usage_server.url.upper() + "/", "ANTHROPIC_AUTH_TOKEN": BEARER}}), encoding="utf-8")
    rows = _run(home)
    assert [r[0] for r in rows] == [doctor.PASS]


# ---------------------------------------------------------------- lifecycle edge cases

def _manifest(home) -> dict:
    return json.loads((home / ".claude/aicj/manifest.json").read_text(encoding="utf-8"))


def test_adopted_value_stays_after_uninstall(tmp_path, home):
    _write_settings(home, {"env": {KEY: GATEWAY}})
    assert _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY)).returncode == 0
    assert _uninstall(home).returncode == 0
    assert _settings(home)["env"][KEY] == GATEWAY


def test_uppercase_equivalent_value_is_adopted_without_write(tmp_path, home):
    original = "HTTP://127.0.0.1:8090/"
    _write_settings(home, {"env": {KEY: original}})
    assert _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY)).returncode == 0
    assert _settings(home)["env"][KEY] == original
    assert _manifest(home)["gateway"] == {"value": GATEWAY, "previous": original}
    assert _uninstall(home).returncode == 0
    assert _settings(home)["env"][KEY] == original


def test_reinstall_with_changed_url_replaces_owned_and_uninstall_removes(tmp_path, home):
    team = _team_file(tmp_path, gateway_base_url=GATEWAY)
    assert _install(home, team).returncode == 0
    new_url = "http://127.0.0.1:8091"
    assert _install(home, _retarget(team, gateway_base_url=new_url)).returncode == 0
    assert _settings(home)["env"][KEY] == new_url
    assert _manifest(home)["gateway"]["value"] == new_url
    assert _uninstall(home).returncode == 0
    assert not (home / ".claude/settings.json").exists()


def test_reinstall_with_changed_url_restores_original_on_uninstall(tmp_path, home):
    original = "https://other.example.test"
    _write_settings(home, {"env": {KEY: original}})
    team = _team_file(tmp_path, gateway_base_url=GATEWAY)
    assert _install(home, team, "--on-conflict", "backup").returncode == 0
    new_url = "http://127.0.0.1:8091"
    assert _install(home, _retarget(team, gateway_base_url=new_url)).returncode == 0
    assert _settings(home)["env"][KEY] == new_url
    assert _manifest(home)["gateway"] == {"value": new_url, "previous": original}
    assert _uninstall(home).returncode == 0
    assert _settings(home)["env"][KEY] == original


def test_interrupted_reinstall_keeps_gateway_record(tmp_path, home, monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "installer"))
    from lib import engine, manifest as mf
    assert _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY)).returncode == 0
    old = mf.load(home)
    assert old.gateway == {"value": GATEWAY}
    journal = engine.Journal(home, old, enabled=True)
    journal.meta = mf.Manifest(gateway=dict(old.gateway))
    journal.flush(mf.IN_PROGRESS)
    saved = mf.load(home)
    assert saved.state == mf.IN_PROGRESS and saved.gateway == {"value": GATEWAY}


def test_uninstall_deletes_settings_file_it_created(tmp_path, home):
    assert not (home / ".claude/settings.json").exists()
    assert _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY)).returncode == 0
    assert (home / ".claude/settings.json").exists()
    assert _uninstall(home).returncode == 0
    assert not (home / ".claude/settings.json").exists()


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_existing_value_is_overwritten_without_backup_flag(tmp_path, home, blank):
    _write_settings(home, {"env": {KEY: blank}})
    result = _install(home, _team_file(tmp_path, gateway_base_url=GATEWAY))
    assert result.returncode == 0, result.stderr
    assert _settings(home)["env"][KEY] == GATEWAY
    assert _manifest(home)["gateway"] == {"value": GATEWAY}


def test_non_string_existing_value_skips_then_backup_overwrites(tmp_path, home):
    _write_settings(home, {"env": {KEY: 123}})
    team = _team_file(tmp_path, gateway_base_url=GATEWAY)
    result = _install(home, team)
    assert result.returncode == 0, result.stderr
    assert _settings(home)["env"][KEY] == 123
    assert "differs; rerun with --on-conflict backup" in result.stdout + result.stderr
    assert _install(home, team, "--on-conflict", "backup").returncode == 0
    assert _settings(home)["env"][KEY] == GATEWAY
    assert _manifest(home)["gateway"] == {"value": GATEWAY}


@pytest.mark.parametrize("raw", ["https://gw.example.test/a;x=1/b", "https://gw.example.test/a/b;x"])
def test_validate_rejects_semicolon_params(raw):
    with pytest.raises(UserError):
        telemetry.validate_team_bytes(_team(gateway_base_url=raw))


def test_same_gateway_helper():
    assert telemetry.same_gateway("HTTPS://GW.EXAMPLE.TEST/", "https://gw.example.test")
    assert not telemetry.same_gateway(5, "https://gw.example.test")
    assert not telemetry.same_gateway("not a url", "https://gw.example.test")
