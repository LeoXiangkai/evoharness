from __future__ import annotations

import re
from pathlib import Path

import aicj_telemetry as tel

DOC = (Path(__file__).resolve().parents[2] / "docs" / "telemetry-fields.md").read_text(encoding="utf-8")


def _section(title: str) -> str:
    return DOC.split(title, 1)[1].split("\n## ", 1)[0]


def _rows(section: str):
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) == 2 and cells[1] not in ("kind", "fields") and not set(cells[0]) <= {"-"}:
            yield cells


def test_every_event_type_and_field_is_documented_and_nothing_extra():
    doc = {name: set(re.findall(r"[a-z_]+", re.sub(r"\([^)]*\)", "", fields)))
           for name, fields in _rows(_section("## Event types"))}
    assert set(doc) == set(tel.SCHEMA)
    for etype, spec in tel.SCHEMA.items():
        want = set(spec)
        got = doc[etype] - ({"no", "fields"} if not want else set())
        assert got == want, etype


def test_enum_values_are_documented():
    section = _section("## Event types")
    for etype, spec in tel.SCHEMA.items():
        for field, kind in spec.items():
            if isinstance(kind, tuple):
                line = next(f for n, f in _rows(section) if n == etype)
                for value in kind[1]:
                    assert value in line, (etype, field, value)


def test_envelope_fields_are_documented_and_nothing_extra():
    doc = {name for name, _ in _rows(_section("## Envelope"))}
    assert doc == set(tel.ENVELOPE)
    for value in tel.PLATFORMS:
        assert value in DOC
