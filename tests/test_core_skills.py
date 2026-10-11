"""Coverage for skills shipped in core: frontmatter shape and reference integrity."""
from __future__ import annotations

import re
from pathlib import Path

from conftest import REPO

CORE = REPO / "core"
SKILLS = CORE / "skills"
# Categories whose markdown may cross-reference each other by rules/refs/agents paths.
LINK_DIRS = ("rules", "refs", "agents", "skills")
# Only core markdown files link by these relative paths; templates is checked by
# test_core_component.test_global_template_only_references_shipped_files.
LINK_RE = re.compile(r"(?<![\w/])((?:rules|refs|agents|skills)/[\w./-]+\.(?:md))\b")


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out[key.strip()] = value.strip()
    return out


def test_every_core_skill_has_matching_name_and_description() -> None:
    skills = sorted(p for p in SKILLS.iterdir() if p.is_dir())
    assert skills, "core ships no skills"
    for skill in skills:
        doc = skill / "SKILL.md"
        assert doc.is_file(), f"{skill.name}: missing SKILL.md"
        meta = _frontmatter(doc)
        assert meta.get("name") == skill.name, f"{doc}: name must equal directory name"
        assert meta.get("description"), f"{doc}: description must not be empty"


def test_core_markdown_internal_references_resolve() -> None:
    docs = [p for d in LINK_DIRS for p in (CORE / d).rglob("*.md")]
    assert docs, "core has no markdown to check"
    broken: list[str] = []
    for path in docs:
        text = path.read_text(encoding="utf-8")
        for ref in LINK_RE.findall(text):
            # resolve like a markdown reader: relative to the referencing file
            # first (skill-local rules/*.md), then against the core root.
            local = (path.parent / ref).resolve()
            if not local.is_file() and not (CORE / ref).is_file():
                broken.append(f"{path.relative_to(REPO)} -> {ref}")
    assert broken == [], "broken core references:\n" + "\n".join(broken)


def test_flow_stage_gates_status_and_tiers() -> None:
    text = (SKILLS / "flow" / "SKILL.md").read_text(encoding="utf-8")
    for gate in ("门1", "门2", "门3", "门4"):
        assert gate in text, f"flow must reference {gate}"
    for column in ("出口门", "证据"):
        assert f"| {column} |" in text, f"flow stage table must have {column} column"
    for status in ("通过", "未过", "未记录"):
        assert status in text, f"flow must offer gate status {status}"
    assert re.search(r"未记录[^。\n]{0,60}不等同(?:于)?通过", text), (
        "flow must state that 未记录 does not mean passed"
    )
    assert "规模分档" in text, "flow must document gate tiers by task size"
    assert "小改" in text and "门1 轻量" in text, "flow must list tier gate sets"
    assert "不得强拉完整流程" in text, "flow must keep the no-forced-full-flow rule"
    pipeline = (SKILLS / "sdd-pipeline" / "SKILL.md").read_text(encoding="utf-8")
    for gate in ("门1", "门2", "门3", "门4"):
        assert re.search(rf"^## {gate} ·", pipeline, re.M), (
            f"sdd-pipeline must define a section for {gate} that flow cites"
        )
    lowered = text.lower()
    for forbidden in ("ledger", "台账", "~/.claude"):
        assert forbidden not in lowered, f"flow must not reference {forbidden}"
