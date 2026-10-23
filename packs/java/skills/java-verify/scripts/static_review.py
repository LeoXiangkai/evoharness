#!/usr/bin/env python3
"""Deterministic Java review checks for changed files and small project trees."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

EXTENSIONS = {".java", ".xml", ".sql", ".yml", ".yaml", ".properties"}
SECRET_EXTENSIONS = {".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go"}
SKIP_DIRS = {".git", ".idea", ".vscode", ".omx", "target", "build", "dist", "node_modules", "__pycache__"}
SECRET_RE = re.compile(
    r"""(?i)(?:password|passwd|secret|token|access[_-]?key|private[_-]?key)\w*['"]?(?:(?:[ \t]*:[ \t]*[\w.<>\[\]?]+)?[ \t]*=|[ \t]*:)[ \t]*(?:'(?![$<{\[(])[^'\s]{8,}'|"(?![$<{\[(])[^"\s]{8,}")"""
)
DOLLAR_RE = re.compile(r"\$\{[^}]+}")
WRITE_RE = re.compile(r"(?is)\b(update|delete\s+from)\b(?P<body>.*?)(?:;|$)")
TX_RE = re.compile(r"@Transactional(?!\s*\([^)]*rollbackFor\s*=)")
CATCH_RE = re.compile(r"catch\s*\([^)]*\)\s*\{(?P<body>.*?)\}", re.S)
ANNOTATION_METHOD_RE = re.compile(r"(?P<annotation>@(?:Transactional|Async)\b[^\n]*)\s*(?:public|protected|private)?\s*(?P<final>final\s+)?(?:[\w<>?,.\[\]]+\s+)+(?P<name>\w+)\s*\(")


def parse_args():
    parser = argparse.ArgumentParser(description="Java static review")
    parser.add_argument("paths", nargs="*", default=["."])
    parser.add_argument("--namespace", choices=("javax", "jakarta", "unknown"), default=None)
    return parser.parse_args()


def iter_files(paths):
    seen = set()
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            continue
        candidates = [path] if path.is_file() else path.rglob("*")
        for item in candidates:
            if not item.is_file() or not (item.suffix in EXTENSIONS or item.suffix.lower() in SECRET_EXTENSIONS) or any(part in SKIP_DIRS for part in item.parts):
                continue
            if item not in seen:
                seen.add(item)
                yield item


def line_no(text, index):
    return text.count("\n", 0, index) + 1


def add(findings, level, path, line, message):
    findings.append((level, path, line, message))


def namespace_for(paths, requested):
    if requested:
        return requested
    for path in paths:
        if path.suffix == ".java" and re.search(r"^\s*import\s+jakarta\.", path.read_text(encoding="utf-8", errors="ignore"), re.M):
            return "jakarta"
    return "unknown"


def check_file(path, findings, namespace):
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() in SECRET_EXTENSIONS:
        for match in SECRET_RE.finditer(text):
            add(findings, "P0", path, line_no(text, match.start()), "possible plaintext secret or credential")
    if path.suffix == ".xml":
        for match in DOLLAR_RE.finditer(text):
            add(findings, "P0", path, line_no(text, match.start()), "MyBatis ${} requires whitelist proof")
    if path.suffix in {".xml", ".sql"}:
        for match in WRITE_RE.finditer(text):
            body = match.group("body").lower()
            if not re.search(r"\bwhere\b", body):
                add(findings, "P0", path, line_no(text, match.start()), "update/delete appears to have no where clause")
    if path.suffix != ".java":
        return
    for match in TX_RE.finditer(text):
        add(findings, "P1", path, line_no(text, match.start()), "@Transactional should specify rollbackFor when used for business writes")
    for match in CATCH_RE.finditer(text):
        body = match.group("body").strip()
        if not body or all(line.strip().startswith(("//", "/*", "*", "*/")) for line in body.splitlines() if line.strip()):
            add(findings, "P1", path, line_no(text, match.start()), "catch block is empty or contains comments only")
    for match in re.finditer(r"\be\.printStackTrace\s*\(\s*\)", text):
        add(findings, "P1", path, line_no(text, match.start()), "e.printStackTrace() should use project logging")
    if "/src/test/" not in path.as_posix() and not path.as_posix().endswith("/src/test"):
        for match in re.finditer(r"\bSystem\.(?:out|err)\s*\.", text):
            add(findings, "P1", path, line_no(text, match.start()), "non-test code should use project logging instead of System.out/err")
    for match in ANNOTATION_METHOD_RE.finditer(text):
        signature_start = match.start()
        prefix = text[max(0, signature_start):match.start("name")]
        if re.search(r"\bprivate\s+", prefix):
            add(findings, "P1", path, line_no(text, signature_start), f"{match.group('annotation').split()[0]} on private method may bypass Spring proxy")
    if namespace == "jakarta":
        for match in re.finditer(r"^\s*import\s+(javax\.(?:persistence|validation|servlet)\.[^;]+);", text, re.M):
            add(findings, "P1", path, line_no(text, match.start()), "jakarta project contains legacy javax import")


def main(argv=None):
    args = parse_args()
    paths = list(iter_files(args.paths))
    namespace = namespace_for(paths, args.namespace)
    findings = []
    for path in paths:
        check_file(path, findings, namespace)
    print(f"Scanned files: {len(paths)}")
    if not findings:
        print("No P0/P1 deterministic findings.")
        return 0
    for level, path, line, message in findings:
        print(f"{level} {path}:{line} {message}")
    print(f"Findings: {len(findings)} (P0={sum(level == 'P0' for level, *_ in findings)}, P1={sum(level == 'P1' for level, *_ in findings)})")
    return 1 if any(level == "P0" for level, *_ in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
