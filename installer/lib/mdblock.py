from __future__ import annotations

from .util import UserError

BEGIN = "<!-- aicj:begin -->"
END = "<!-- aicj:end -->"


def find_block(text: str) -> tuple[int, int] | None:
    """Span of the marker block (including one trailing newline), None when absent.

    Only exactly one BEGIN followed by exactly one END is a block; anything else is
    ambiguous and refused so user text between stray markers is never touched.
    """
    begins = text.count(BEGIN)
    ends = text.count(END)
    if begins == 0 and ends == 0:
        return None
    if begins != 1 or ends != 1:
        raise UserError(f"malformed aicj marker block ({begins} begin / {ends} end markers); fix the file by hand")
    start = text.index(BEGIN)
    stop = text.index(END)
    if stop < start:
        raise UserError("malformed aicj marker block (end marker before begin marker); fix the file by hand")
    stop += len(END)
    if text.startswith("\r\n", stop):
        stop += 2
    elif text.startswith("\n", stop):
        stop += 1
    return start, stop


def has_block(text: str) -> bool:
    return find_block(text) is not None


def build_block(reference: str) -> str:
    return f"{BEGIN}\n@{reference}\n{END}\n"


def append_block(text: str, reference: str) -> str:
    if has_block(text):
        return text
    eol = "\r\n" if "\r\n" in text else "\n"
    body = text
    if body and not body.endswith("\n"):
        body += eol
    return body + build_block(reference).replace("\n", eol)


def remove_exact(text: str, expected: str) -> tuple[str, bool]:
    """Remove the block only when its text equals `expected`."""
    span = find_block(text)
    if span is None or text[span[0] : span[1]] != expected:
        return text, False
    return text[: span[0]] + text[span[1] :], True
