#!/usr/bin/env python3
"""PreToolUse(Bash) hook: 把 git 写命令的命令词 `git` 改写为 <claude>/bin/git-safe 的绝对路径。

<claude> 取 AICJ_CLAUDE_DIR，否则从本脚本位置推导（hooks/aicj/<x>.py -> parents[2]）；
目标不存在时不改写、原样放行。git-safe 透传参数，遇 .git/index.lock 撞车自动退避重试。
只替换命令位的 `git` 一个词。用一个轻量扫描器只在顶层识别分隔符；引号、$(...)、反引号、${...}、
heredoc 正文整体跳过。扫描器不确定(引号不配对 / heredoc 无终止行等) → 不改写；
任何异常 → 无输出 exit 0 (fail-open)。已知不覆盖：env/command/sudo/xargs 等包装器后的 git、
重定向在命令词之前、case 模式里的 ')'。
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except AttributeError:
        pass


def _replacement() -> str | None:
    """Explicit interpreter invocation, or None when git-safe is missing."""
    env = os.environ.get("AICJ_CLAUDE_DIR")
    claude = Path(env).expanduser() if env else Path(__file__).resolve().parents[2]
    target = claude / "bin" / "git-safe"
    if not target.is_file():
        return None
    return f'"{sys.executable}" "{target}"'

WRITE_CMDS = frozenset((
    "add", "commit", "pull", "push", "reset", "checkout", "switch", "restore",
    "update-ref", "merge", "rebase", "cherry-pick", "stash", "rm", "mv", "tag",
    "branch", "worktree", "revert", "am", "apply",
))

# 命令位前可出现的保留字（其后紧跟的才是命令词）
KEYWORDS = frozenset(("if", "then", "else", "elif", "do", "while", "until", "!", "{", "time"))

ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
SUBCMD_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")

# git 全局选项：带独立参数 / 无参数
OPTS_WITH_ARG = frozenset((
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix",
    "--config-env", "--attr-source",
))
OPTS_NO_ARG = frozenset((
    "-p", "-P", "--paginate", "--no-pager", "--no-replace-objects", "--bare",
    "--literal-pathspecs", "--glob-pathspecs", "--noglob-pathspecs",
    "--icase-pathspecs", "--no-optional-locks", "--no-advice", "--no-lazy-fetch",
))


class Uncertain(Exception):
    pass


class Scanner:
    def __init__(self, s):
        self.s = s
        self.n = len(s)
        self.segs = [[]]      # 顶层每段的词 [(start, end), ...]
        self.pending = []     # 待消费的 heredoc [(delim, strip_tabs)]

    def new_seg(self):
        if self.segs[-1]:
            self.segs.append([])

    # ---- 不透明区段 ----
    def squote(self, i):
        j = self.s.find("'", i)
        if j < 0:
            raise Uncertain("unterminated single quote")
        return j + 1

    def ansi(self, i):
        s, n = self.s, self.n
        while i < n:
            c = s[i]
            if c == "\\":
                i += 2
            elif c == "'":
                return i + 1
            else:
                i += 1
        raise Uncertain("unterminated $'")

    def bt(self, i):
        s, n = self.s, self.n
        while i < n:
            c = s[i]
            if c == "\\":
                i += 2
            elif c == "`":
                return i + 1
            else:
                i += 1
        raise Uncertain("unterminated backtick")

    def dq(self, i):
        s, n = self.s, self.n
        while i < n:
            c = s[i]
            if c == "\\":
                i += 2
            elif c == '"':
                return i + 1
            elif c == "$" and s.startswith("(", i + 1):
                i = self.region(i + 2, False)
            elif c == "$" and s.startswith("{", i + 1):
                i = self.brace(i + 2)
            elif c == "`":
                i = self.bt(i + 1)
            else:
                i += 1
        raise Uncertain("unterminated double quote")

    def brace(self, i):
        s, n = self.s, self.n
        depth = 1
        while i < n:
            c = s[i]
            if c == "\\":
                i += 2
            elif c == "'":
                i = self.squote(i + 1)
            elif c == '"':
                i = self.dq(i + 1)
            elif c == "`":
                i = self.bt(i + 1)
            elif c == "$" and s.startswith("(", i + 1):
                i = self.region(i + 2, False)
            elif c == "{":
                depth += 1
                i += 1
            elif c == "}":
                depth -= 1
                i += 1
                if depth == 0:
                    return i
            else:
                i += 1
        raise Uncertain("unterminated ${")

    # ---- heredoc ----
    def skip_heredocs(self, i):
        s, n = self.s, self.n
        for delim, strip in self.pending:
            while True:
                if i > n:
                    raise Uncertain("heredoc not terminated")
                e = s.find("\n", i)
                line = s[i:e] if e >= 0 else s[i:]
                if strip:
                    line = line.lstrip("\t")
                if line == delim:
                    i = e + 1 if e >= 0 else n
                    break
                if e < 0:
                    raise Uncertain("heredoc not terminated")
                i = e + 1
        self.pending = []
        return i

    def heredoc_delim(self, i):
        """从 << 之后读取定界词，返回 (delim, strip_tabs, 新位置)。"""
        s, n = self.s, self.n
        strip = False
        if s.startswith("-", i):
            strip = True
            i += 1
        while i < n and s[i] in " \t":
            i += 1
        out = []
        while i < n:
            c = s[i]
            if c in " \t\n;|&()<>":
                break
            if c == "'":
                j = self.squote(i + 1)
                out.append(s[i + 1:j - 1])
                i = j
            elif c == '"':
                j = self.dq(i + 1)
                out.append(s[i + 1:j - 1])
                i = j
            elif c == "\\":
                if i + 1 < n:
                    out.append(s[i + 1])
                i += 2
            else:
                out.append(c)
                i += 1
        delim = "".join(out)
        if not delim:
            raise Uncertain("empty heredoc delimiter")
        return delim, strip, i

    # ---- 主扫描 ----
    def region(self, i, top):
        s, n = self.s, self.n
        ws = None          # 当前词起点
        depth = 0

        def flush(j):
            nonlocal ws
            if top and ws is not None:
                self.segs[-1].append((ws, j))
            ws = None

        while i < n:
            c = s[i]
            if c == "\\":
                if i + 1 >= n:
                    raise Uncertain("trailing backslash")
                if s[i + 1] != "\n" and ws is None:
                    ws = i
                i += 2
            elif c == " " or c == "\t":
                flush(i)
                i += 1
            elif c == "\n":
                flush(i)
                if top:
                    self.new_seg()
                i += 1
                if self.pending:
                    i = self.skip_heredocs(i)
            elif c == "#" and ws is None:
                j = s.find("\n", i)
                i = n if j < 0 else j
            elif c == "'":
                if ws is None:
                    ws = i
                i = self.squote(i + 1)
            elif c == '"':
                if ws is None:
                    ws = i
                i = self.dq(i + 1)
            elif c == "`":
                if ws is None:
                    ws = i
                i = self.bt(i + 1)
            elif c == "$":
                if ws is None:
                    ws = i
                nx = s[i + 1:i + 2]
                if nx == "(":
                    i = self.region(i + 2, False)
                elif nx == "{":
                    i = self.brace(i + 2)
                elif nx == "'":
                    i = self.ansi(i + 2)
                else:
                    i += 1
            elif c == ";" or c == "|" or c == "&":
                prev = s[i - 1] if i > 0 else ""
                nxt = s[i + 1:i + 2]
                is_redirect = (c == "&" and (prev in "<>" and prev != "" or nxt == ">")) or \
                              (c == "|" and prev == ">")
                if is_redirect:
                    if ws is None:
                        ws = i
                    i += 1
                else:
                    flush(i)
                    if top:
                        self.new_seg()
                    i += 1
            elif c == "(":
                if not top:
                    depth += 1
                flush(i)
                if top:
                    self.new_seg()
                i += 1
            elif c == ")":
                if not top:
                    if depth == 0:
                        return i + 1
                    depth -= 1
                flush(i)
                if top:
                    self.new_seg()
                i += 1
            elif c == "<" and s.startswith("<<", i) and not s.startswith("<<<", i):
                flush(i)
                delim, strip, i = self.heredoc_delim(i + 2)
                self.pending.append((delim, strip))
            else:
                if ws is None:
                    ws = i
                i += 1

        if not top:
            raise Uncertain("unterminated $(")
        flush(n)
        if self.pending:
            raise Uncertain("heredoc without body")
        return n


def find_rewrites(cmd):
    """返回需替换的 `git` 起点下标列表。"""
    sc = Scanner(cmd)
    sc.region(0, True)
    hits = []
    for seg in sc.segs:
        words = [(a, b, cmd[a:b]) for a, b in seg]
        if not words:
            continue
        m = len(words)
        k = 0
        while k < m and (words[k][2] in KEYWORDS or ENV_RE.match(words[k][2])):
            k += 1
        if k >= m:
            return []
        if words[k][2] != "git":
            return []
        gi = k
        j = gi + 1
        sub = None
        while j < m:
            raw = words[j][2]
            if not raw.startswith("-"):
                sub = j
                break
            if raw in OPTS_WITH_ARG:
                j += 2
            elif raw in OPTS_NO_ARG or (raw.startswith("--") and "=" in raw):
                j += 1
            else:
                break  # 未知选项：不确定，不改写
        if sub is None:
            continue
        name = words[sub][2]
        if not SUBCMD_RE.match(name) or name not in WRITE_CMDS:
            continue
        if name == "push":
            forced = False
            for _, _, raw in words[sub + 1:]:
                if raw.startswith("--"):
                    if raw.startswith("--force"):
                        forced = True
                elif raw.startswith("-"):
                    if "f" in raw[1:]:
                        forced = True
                elif raw.startswith("+"):
                    forced = True  # +refspec 即强推
            if forced:
                return []
        hits.append(words[gi][0])
    return hits


RECURSIVE_RM = re.compile(r"(?:^|[;&|(\n]\s*)rm\s+(?:-\S*\s+)*?(?:-\S*[rR]\S*|--recursive)\b")


def main():
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(event, dict) or event.get("tool_name") != "Bash":
            return 0
        tool_input = event.get("tool_input")
        if not isinstance(tool_input, dict):
            return 0
        cmd = tool_input.get("command")
        if not isinstance(cmd, str) or "git" not in cmd:
            return 0
        # 递归 rm 由 rm-to-trash-hook 改写；两个 hook 同时返回 updatedInput 时以谁为准未定义，这里让路
        if RECURSIVE_RM.search(cmd):
            return 0
        hits = find_rewrites(cmd)
        if not hits:
            return 0
        replacement = _replacement()
        if replacement is None:
            return 0
        new_cmd = cmd
        for pos in sorted(hits, reverse=True):
            new_cmd = new_cmd[:pos] + replacement + new_cmd[pos + 3:]
        updated = dict(tool_input)
        updated["command"] = new_cmd
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "updatedInput": updated,
            }
        }))
        return 0
    except Uncertain as e:
        sys.stderr.write("git-safe-rewrite: skip (%s)\n" % e)
        return 0
    except Exception as e:  # fail-open
        sys.stderr.write("git-safe-rewrite: error %r\n" % (e,))
        return 0


if __name__ == "__main__":
    sys.exit(main())
