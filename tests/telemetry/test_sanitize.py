from __future__ import annotations

import json

import aicj_telemetry as tel

TS = "2026-10-28T01:02:03Z"
PHONE = "138" + "00138000"
EMAIL = "bob" + "@" + "corp.example"
SK = "sk-" + "abcdefgh12345678"
GHP = "ghp_" + "abcdefgh12345678"
AKIA = "AKIA" + "ABCDEFGH12345678"
POSIX_HOME = "/Users/" + "alice/work/proj"
WIN_HOME = "C:\\Users\\" + "alice\\work\\proj"
LOCAL = "127.0.0" + ".1:8317"


def base(etype, **kw):
    ev = {"schema": 1, "type": etype, "ts": TS, "event_id": "ab" * 8, "team": "team1", "user": "cd" * 8}
    ev.update(kw)
    return ev


def test_valid_event_passes_and_drops_unknown_keys():
    out = tel.sanitize(base("hook_gate", hook="pre_bash", event="deny", mode="warn",
                            rule_tag="git-safe", extra_key="x", cmd="rm -rf /"))
    assert out["hook"] == "pre_bash" and out["event"] == "deny"
    assert "extra_key" not in out and "cmd" not in out


def test_unknown_type_and_bad_schema_dropped():
    assert tel.sanitize(base("nope")) is None
    assert tel.sanitize(base("correction", schema=2)) is None
    assert tel.sanitize("text") is None
    assert tel.sanitize(None) is None


def test_missing_required_envelope_dropped():
    ev = base("correction")
    del ev["user"]
    assert tel.sanitize(ev) is None


def test_sensitive_values_removed_from_every_field_kind():
    secrets = [PHONE, EMAIL, SK, GHP, AKIA, POSIX_HOME, WIN_HOME, LOCAL, "prompt: fix my code",
               "def f():\n    return 1"]
    for s in secrets:
        out = tel.sanitize(base("hook_gate", hook=s, rule_tag=s, tool=s,
                                event="deny", mode="warn"))
        blob = json.dumps(out)
        assert out is not None
        for bad in ("fix my code", "def f", "Users", "127.0.0", PHONE, EMAIL, SK, GHP, AKIA):
            assert bad not in blob
        for key in ("hook", "rule_tag", "tool"):
            assert key not in out


def test_sensitive_user_and_team_drop_event():
    assert tel.sanitize(base("correction", user=EMAIL)) is None
    assert tel.sanitize(base("correction", team=PHONE)) is None


def test_tokens_list_rejects_bad_members_wholesale():
    assert tel.sanitize(base("install", command="install", ok=True, packs=["java", "py"]))["packs"] == ["java", "py"]
    out = tel.sanitize(base("install", command="install", ok=True, packs=["java", POSIX_HOME]))
    assert "packs" not in out


def test_types_enforced():
    out = tel.sanitize(base("test_run", passed=True, failed=-1, skipped="3", errors=2, green="yes"))
    assert out["errors"] == 2
    for k in ("passed", "failed", "skipped", "green"):
        assert k not in out


def test_enum_and_date_and_ts():
    out = tel.sanitize(base("lesson_card", card="cd" * 6, level="shout", scope_kind="path",
                            status="active", created="2026-13-40"))
    assert "level" not in out and "created" not in out and out["scope_kind"] == "path"
    assert tel.sanitize(base("correction", ts="2026-10-28T01:02:03+00:00")) is None


def test_hash_fields_hex_only():
    out = tel.sanitize(base("correction", repo="not-hex!", task="ab" * 6))
    assert "repo" not in out and out["task"] == "ab" * 6


def test_windows_paths_never_survive_in_free_text_fields():
    out = tel.sanitize(base("verify_probe", overall=WIN_HOME, layers=["unit"], failed_layers=[WIN_HOME]))
    assert "overall" not in out and "failed_layers" not in out and out["layers"] == ["unit"]


def test_user_hash_containing_mobile_digits_is_kept_but_free_form_user_rejected():
    digits = "138" + "12345678"
    hashed = digits + "abcde"
    ok = tel.sanitize(base("correction", user=hashed))
    assert ok is not None and ok["user"] == hashed
    anon = tel.sanitize(base("correction", user="anon-" + hashed))
    assert anon is not None and anon["user"] == "anon-" + hashed
    for bad in ("u1", digits, "ABCDEF0123456789", "anon-xyz", "cd" * 9, hashed + "\n"):
        assert tel.sanitize(base("correction", user=bad)) is None


def _scrubbed(**kw):
    return json.dumps(tel.sanitize(base("hook_gate", hook="h", event="deny", mode="warn", **kw)))


def test_phone_with_country_code_is_redacted():
    digits = "138" + "1234" + "5678"
    for p in ("+86" + digits, "86" + digits, "+86 " + digits, "+86-" + digits):
        out = _scrubbed(tool="t" + p)
        assert digits not in out and "1234" not in out, p


def test_phone_with_0086_prefix_is_redacted():
    digits = "138" + "1234" + "5678"
    for p in ("00" + "86" + digits, "0086-" + digits, "0086 " + digits):
        out = _scrubbed(tool="t" + p)
        assert digits not in out and "1234" not in out, p


def test_more_credential_formats_are_redacted():
    secrets = ["AIza" + "SyA1234567890abcdefghij", "glpat-" + "abcdefgh1234", "sk_" + "live_abcdefgh1234",
               "sk_" + "test_abcdefgh1234", "rk_" + "live_abcdefgh1234", "xoxb" + "-12345678-abcdef",
               "xoxp" + "-12345678-abcdef"]
    for s in secrets:
        out = _scrubbed(tool="v" + s)
        assert s[6:] not in out, s


def test_unknown_layer_and_rule_tag_are_hashed_known_pass(claude, team):
    ident = {"user": "cd" * 8, "install": "ab" * 4}
    odd = "ali" + "ce"
    ev = {"hook": "h", "event": "deny", "mode": "warn"}
    rt = tel._build(team, ident, "hook_gate", {**ev, "rule_tag": odd}, TS, "ef" * 8, None, None)
    assert rt["rule_tag"].startswith("x-") and odd not in json.dumps(rt)
    ok = tel._build(team, ident, "hook_gate", {**ev, "rule_tag": "push-review-gate"}, TS, "ef" * 8, None, None)
    assert ok["rule_tag"] == "push-review-gate"
    bob = "bo" + "bby"
    vp = tel._build(team, ident, "verify_probe", {"overall": "ok", "layers": ["e2e", bob], "failed_layers": [bob]},
                    TS, "ef" * 8, None, None)
    assert vp["layers"][0] == "e2e" and vp["layers"][1].startswith("x-")
    assert vp["failed_layers"][0] == vp["layers"][1] and bob not in json.dumps(vp)
