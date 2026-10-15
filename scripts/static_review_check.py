#!/usr/bin/env python3
from pathlib import Path
import argparse
import re
import sys


DEFAULT_EXTS = {
    ".java",
    ".xml",
    ".yml",
    ".yaml",
    ".properties",
    ".sql",
}
SECRET_EXTS = {".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx", ".vue", ".go"}
SKIP_DIRS = {
    ".git",
    ".idea",
    ".vscode",
    ".ai-coding-java",
    ".omx",
    "node_modules",
    "target",
    "build",
    "dist",
}
# a directory holding this file contains deliberately bad samples (checker fixtures); skip it
FIXTURE_MARKER = ".static-review-fixture"
SECRET_RE = re.compile(
    r"(?i)(password|passwd|secret|token|access[_-]?key|private[_-]?key)\s*[:=]\s*['\"]?[^'\"\s]{8,}"
)
MYBATIS_DOLLAR_RE = re.compile(r"\$\{[^}]+}")
TRANSACTIONAL_RE = re.compile(r"@Transactional(?!\s*\([^)]*rollbackFor\s*=)")
WRITE_SQL_RE = re.compile(r"(?is)\b(update|delete\s+from)\b(?P<body>.*?)(;|$)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Lightweight deterministic review checks for Java enterprise projects.")
    parser.add_argument("paths", nargs="*", default=["."], help="Files or directories to scan")
    parser.add_argument("--include-docs", action="store_true", help="Also scan Markdown/template documentation")
    return parser.parse_args()


def is_fixture(path: Path) -> bool:
    return any((parent / FIXTURE_MARKER).is_file() for parent in path.parents)


def iter_files(paths: list[str], include_docs: bool):
    exts = set(DEFAULT_EXTS)
    if include_docs:
        exts.add(".md")
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            continue
        if path.is_file():
            if path.suffix in exts and not is_fixture(path):
                yield path
            continue
        for item in path.rglob("*"):
            if not item.is_file():
                continue
            if any(part in SKIP_DIRS for part in item.parts) or is_fixture(item):
                continue
            if item.suffix in exts:
                yield item


def line_no(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def add(findings: list[tuple[str, Path, int, str]], level: str, path: Path, line: int, message: str) -> None:
    findings.append((level, path, line, message))


def check_file(path: Path, findings: list[tuple[str, Path, int, str]]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="ignore")

    if path.suffix.lower() in SECRET_EXTS:
        for match in SECRET_RE.finditer(text):
            add(findings, "P0", path, line_no(text, match.start()), "possible plaintext secret or credential")

    if path.suffix == ".xml":
        for match in MYBATIS_DOLLAR_RE.finditer(text):
            add(findings, "P0", path, line_no(text, match.start()), "MyBatis ${} requires whitelist proof")

    if path.suffix in {".xml", ".sql"}:
        for match in WRITE_SQL_RE.finditer(text):
            body = match.group("body").lower()
            if " where " not in f" {body} ":
                add(findings, "P0", path, line_no(text, match.start()), "update/delete appears to have no where clause")

    if path.suffix == ".java":
        for match in TRANSACTIONAL_RE.finditer(text):
            add(findings, "P1", path, line_no(text, match.start()), "@Transactional should specify rollbackFor when used for business writes")


def main() -> int:
    args = parse_args()
    findings: list[tuple[str, Path, int, str]] = []
    scanned = 0
    for path in iter_files(args.paths, args.include_docs):
        scanned += 1
        check_file(path, findings)

    print(f"Scanned files: {scanned}")
    if not findings:
        print("No P0/P1 deterministic findings.")
        return 0

    for level, path, line, message in findings:
        print(f"{level} {path}:{line} {message}")
    return 2 if any(level == "P0" for level, *_ in findings) else 1


if __name__ == "__main__":
    sys.exit(main())
