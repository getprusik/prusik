"""The one reader for a Bash command, as bash reads it (one-bash-lexer).

`parse(text)` makes one forward pass and returns a tree: a script is a list of pipelines, a pipeline
a list of stages, a stage a simple command or a compound command. Every check in `prusik.gate`
reads that tree, so quoting, heredocs and nesting have one meaning everywhere.

It imports `re` only, because the hook's stdlib allowlist is pinned, and it knows nothing of the
gate. It raises only `RecursionError`, past `MAX_DEPTH` levels of nested code. Input bash would
reject is read on: an unterminated quote or substitution ends at the end of the text, and the text
after its opener is also read as code; a token with no place in the grammar is a word.

Compound commands (`( )`, `{ }`, `if`, loops, `case`, functions) nest on an explicit stack, so their
depth is bounded by memory alone. Only code strings (`$( )`, backticks, `<( )`, `${ }`) recurse.
"""

from __future__ import annotations

import re

MAX_DEPTH = 32                      # nested code strings; past it a parse raises RecursionError
LITERAL_DOLLAR = "\x00"             # `'$S'` or `\$S`: no expansion at all
QUOTED_DOLLAR = "\x01"              # `"$S"`: expands to one word

_MAX_SUBSCRIPT = 256                # bounds an assignment's subscript
_MAX_GLOB_GROUP = 512               # bounds the look-ahead for an extglob group's `)`
_NOT_DELIMITER = r"(?![^\s;&|()<>\\]|\\[^\n])"

_BLANKS = re.compile(r"(?:[ \t]|\\\n)+")
_PLAIN = re.compile(r"[^ \t\n;&|()<>\\'\"`$]+")
_DQ_PLAIN = re.compile(r'[^"\\$`]+')
_HEREDOC_PLAIN = re.compile(r"[^\\$`]+")
_PARAM_PLAIN = re.compile(r"""[^}'"`$\\]+""")
_ARITH_PLAIN = re.compile(r"""[^()'"`$\\]+""")
_PAREN_RUN = re.compile(r"\(+")
_BACKTICK_BODY = re.compile(r"[^`\\]*(?:\\.[^`\\]*)*", re.S)
_BACKTICK_ESCAPE = re.compile(r"\\([`\\$])")
_BACKTICK_ESCAPE_DQ = re.compile(r'\\([`\\$"])')
_ANSI_BODY = re.compile(r"[^'\\]*(?:\\.[^'\\]*)*", re.S)
_JOINED_LINE = re.compile(r"(?<!\\)((?:\\\\)*)\\\n")
_ASSIGNMENT = re.compile(rf"[A-Za-z_]\w*(?:\[[^\]\n]{{0,{_MAX_SUBSCRIPT}}}\])?\+?=")
_REDIRECT = re.compile(r"(?:[0-9]+|\{[A-Za-z_]\w*\})?(&>>|&>|>>|>\||>&|<>|<<<|<<-|<<|<&|>|<)")
_COPY = re.compile(rf"(?:[0-9]+-?|-){_NOT_DELIMITER}")
_RESERVED = re.compile(
    rf"(?:if|then|elif|else|fi|while|until|do|done|for|select|case|esac|function|time|coproc|!"
    rf"|\{{|\}}|\[\[){_NOT_DELIMITER}")
_FUNCTION = re.compile(r"[A-Za-z_][\w.:+@%-]*[ \t]*\([ \t]*\)")
_IN = re.compile(rf"in{_NOT_DELIMITER}")
_ESAC = re.compile(rf"esac{_NOT_DELIMITER}")
_DO = re.compile(rf"do{_NOT_DELIMITER}")
_TEST_END = re.compile(rf"\]\]{_NOT_DELIMITER}")
_TIME_FLAG = re.compile(rf"-p{_NOT_DELIMITER}")
_EMPTY_PARENS = re.compile(r"\([ \t]*\)")

_CLOSERS = frozenset(("then", "elif", "else", "fi", "do", "done", "esac", "}"))
_OPENERS = frozenset(("if", "while", "until", "for", "select", "case", "{", "[[", "function"))
_SKIPPED_WORDS = frozenset(("time", "coproc", "!"))
_END_OF_CASE_ARM = (";;&", ";;", ";&")


class Word:
    __slots__ = ("pos", "text", "bare", "scripts", "end")

    def __init__(self, pos: int, text: str, bare: bool, scripts: list[Script], end: int) -> None:
        self.pos = pos
        self.text = text
        self.bare = bare
        self.scripts = scripts
        self.end = end              # where the word stops in the text it was read from


class Heredoc:
    __slots__ = ("body", "quoted", "closed", "span", "scripts", "spans")

    def __init__(self, quoted: bool) -> None:
        self.body = ""
        self.quoted = quoted
        self.closed = False
        self.span: tuple[int, int] = (0, 0)
        self.scripts: list[Script] = []
        self.spans: list[str] = []      # the raw `$( )` and backtick spans of an unquoted body


class Redirect:
    __slots__ = ("pos", "op", "target", "heredoc")

    def __init__(self, pos: int, op: str, target: Word | None, heredoc: Heredoc | None) -> None:
        self.pos = pos
        self.op = op
        self.target = target
        self.heredoc = heredoc


class Assignment:
    __slots__ = ("pos", "name", "append", "array", "value")

    def __init__(self, pos: int, name: str, append: bool, array: bool, value: Word) -> None:
        self.pos = pos
        self.name = name
        self.append = append
        self.array = array
        self.value = value


class Simple:
    __slots__ = ("pos", "assignments", "words", "redirects")

    def __init__(self, pos: int, assignments: list[Assignment], words: list[Word],
                 redirects: list[Redirect]) -> None:
        self.pos = pos
        self.assignments = assignments
        self.words = words
        self.redirects = redirects


class Compound:
    __slots__ = ("pos", "kind", "name", "bodies", "words", "redirects")

    def __init__(self, pos: int, kind: str, name: str | None = None) -> None:
        self.pos = pos
        self.kind = kind
        self.name = name
        self.bodies: list[Script] = []
        self.words: list[Word] = []
        self.redirects: list[Redirect] = []


class Pipeline:
    __slots__ = ("stages", "background")

    def __init__(self, stages: list[Simple | Compound], background: bool) -> None:
        self.stages = stages
        self.background = background


class Script:
    __slots__ = ("pipelines", "text")

    def __init__(self, pipelines: list[Pipeline], text: str) -> None:
        self.pipelines = pipelines
        self.text = text


def parse(text: str, depth: int = 0) -> Script:
    """The tree of `text`. `depth` counts the code strings it sits inside; past `MAX_DEPTH` this
    raises `RecursionError`, which is the only thing it raises."""
    if depth > MAX_DEPTH:
        raise RecursionError(f"shell code nested past {MAX_DEPTH} levels")
    return _Parser(text, depth).script()


def iter_commands(script: Script):
    """Every stage under `script` in source order, on an explicit stack. Yields
    `("enter", compound, pipeline, index)` and `("leave", compound, pipeline, index)` around a
    compound's bodies, `("simple", simple, pipeline, index)`, and `("pipeline", pipeline)` once a
    pipeline's stages are done. It does not descend into the scripts nested in words or heredocs;
    the caller does that, and counts the depth."""
    todo: list[tuple] = [("script", script)]
    while todo:
        item = todo.pop()
        kind = item[0]
        if kind == "script":
            for pipeline in reversed(item[1].pipelines):
                todo.append(("pipeline", pipeline))
        elif kind == "pipeline":
            pipeline = item[1]
            todo.append(("done", pipeline))
            for index in range(len(pipeline.stages) - 1, -1, -1):
                todo.append(("stage", pipeline, index))
        elif kind == "stage":
            pipeline, index = item[1], item[2]
            stage = pipeline.stages[index]
            if isinstance(stage, Simple):
                yield ("simple", stage, pipeline, index)
            else:
                yield ("enter", stage, pipeline, index)
                todo.append(("leave", stage, pipeline, index))
                for body in reversed(stage.bodies):
                    todo.append(("script", body))
        elif kind == "done":
            yield ("pipeline", item[1])
        else:
            yield item


class _Frame:
    """An open compound command (or an unclosed heredoc's body) on the explicit stack. It holds the
    context the compound sits in, so the body can be read in a fresh one."""

    __slots__ = ("kind", "comp", "pipes", "stages", "phase", "skip", "heredoc")

    def __init__(self, kind: str, comp: Compound | None = None,
                 heredoc: Heredoc | None = None) -> None:
        self.kind = kind
        self.comp = comp
        self.pipes: list[Pipeline] = []
        self.stages: list[Simple | Compound] = []
        self.phase = ""
        self.skip = False            # a `for` header holds no list, so its first body is dropped
        self.heredoc = heredoc


class _Ctx:
    __slots__ = ("pipes", "stages", "stack", "after_pipe")

    def __init__(self) -> None:
        self.pipes: list[Pipeline] = []
        self.stages: list[Simple | Compound] = []
        self.stack: list[_Frame] = []
        self.after_pipe = False


def _odd_backslashes(line: str) -> bool:
    return (len(line) - len(line.rstrip("\\"))) % 2 == 1


class _Parser:
    __slots__ = ("text", "n", "i", "depth", "pending", "unclosed", "sub")

    def __init__(self, text: str, depth: int) -> None:
        self.text = text
        self.n = len(text)
        self.i = 0
        self.depth = depth
        self.pending: list[tuple[Heredoc, str, bool]] = []
        self.unclosed: list[Heredoc] = []
        self.sub = False             # reading the commands of a `$( )`: a `)` can end them

    def script(self) -> Script:
        return self._list(False)

    # --- small helpers ----------------------------------------------------------------------
    def _skip(self) -> int:
        m = _BLANKS.match(self.text, self.i)
        if m:
            self.i = m.end()
        return self.i

    def _code(self, text: str) -> Script:
        """`text` read as code of its own, one level deeper."""
        return parse(text, self.depth + 1)

    def _enter(self) -> None:
        if self.depth >= MAX_DEPTH:
            raise RecursionError(f"shell code nested past {MAX_DEPTH} levels")
        self.depth += 1

    def _sub(self, scripts: list[Script]) -> None:
        """Read the commands of a `$( )`, `<( )` or `>( )` whose opener ends at `self.i`, in place.
        Leaves `self.i` past its `)`."""
        self._enter()
        scripts.append(self._list(True))
        self.depth -= 1

    # --- words --------------------------------------------------------------------------------
    def _word(self) -> Word:
        text, n = self.text, self.n
        i = start = self.i
        out: list[str] = []
        scripts: list[Script] = []
        while i < n:
            m = _PLAIN.match(text, i)
            if m:
                out.append(m.group())
                i = m.end()
                continue
            c = text[i]
            if c == "\\":
                nxt = text[i + 1:i + 2]
                if nxt == "\n":
                    i += 2
                elif not nxt:
                    out.append("\\")
                    i += 1
                else:
                    out.append(LITERAL_DOLLAR if nxt == "$" else nxt)
                    i += 2
            elif c == "'":
                end = text.find("'", i + 1)
                if end < 0:
                    out.append(text[i + 1:].replace("$", LITERAL_DOLLAR))
                    scripts.append(self._code(text[i + 1:]))
                    i = n
                else:
                    out.append(text[i + 1:end].replace("$", LITERAL_DOLLAR))
                    i = end + 1
            elif c == '"':
                i = self._dquote(i, out, scripts)
            elif c == "$":
                i = self._dollar(i, out, scripts, False)
            elif c == "`":
                i = self._backtick(i, out, scripts, False)
            elif c in "<>" and text[i + 1:i + 2] == "(":
                self.i = i + 2
                self._sub(scripts)
                out.append(text[i:self.i])
                i = self.i
            elif c == "(" and i > start and text[i - 1] in "?*+@!":
                end = self._group_end(i)
                if end < 0:
                    break
                out.append(text[i:end])
                i = end
            else:
                break
        self.i = i
        joined = "".join(out)
        quoted_first = text.startswith(("'", '"', '$"', "$'"), start)
        marked_first = joined[:1] in (LITERAL_DOLLAR, QUOTED_DOLLAR) and joined != ""
        return Word(start, joined, not (quoted_first or marked_first), scripts, i)

    def _group_end(self, i: int) -> int:
        """Index past the `)` closing the extglob group opened at `i`, or -1. An extglob group
        holds no blank, and the look-ahead is bounded."""
        text = self.text
        stop = min(self.n, i + _MAX_GLOB_GROUP)
        depth = 0
        while i < stop:
            c = text[i]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
            elif c in " \t\n;&<>":
                return -1
            elif c == "\\":
                i += 1
            i += 1
        return -1

    def _dquote(self, i: int, out: list[str], scripts: list[Script]) -> int:
        """Read the double-quoted string opened at `i`; its text goes to `out`, its code to
        `scripts`. Returns the index past the closing quote."""
        text, n = self.text, self.n
        buf: list[str] = []
        j = i + 1
        while j < n:
            m = _DQ_PLAIN.match(text, j)
            if m:
                buf.append(m.group())
                j = m.end()
                continue
            c = text[j]
            if c == '"':
                out.append("".join(buf))
                return j + 1
            if c == "\\":
                nxt = text[j + 1:j + 2]
                if nxt in ('"', "\\", "`"):
                    buf.append(nxt)
                elif nxt == "$":
                    buf.append(LITERAL_DOLLAR)
                elif nxt == "\n":
                    pass
                elif not nxt:
                    buf.append("\\")
                else:
                    buf.append("\\" + nxt)
                j += 2
            elif c == "$":
                j = self._dollar(j, buf, scripts, True)
            else:
                j = self._backtick(j, buf, scripts, True)
        out.append("".join(buf))
        scripts.append(self._code(text[i + 1:]))
        return n

    def _dollar(self, i: int, out: list[str], scripts: list[Script], dq: bool) -> int:
        """Read the `$` at `i`: a substitution, an expansion, arithmetic or a plain variable."""
        text = self.text
        mark = QUOTED_DOLLAR if dq else "$"
        nxt = text[i + 1:i + 2]
        if nxt == "(":
            if text.startswith("((", i + 1):
                found: list[Script] = []
                end = self._arith_end(i + 3, found)
                if end >= 0:
                    out.append(mark + text[i + 1:end])
                    scripts.extend(found)
                    return end
            self.i = i + 2
            self._sub(scripts)
            out.append(mark + text[i + 1:self.i])
            return self.i
        if nxt == "{":
            end = self._param(i + 2, scripts, dq)
            out.append(mark + text[i + 1:end])
            return end
        if nxt == "$":
            out.append(mark + LITERAL_DOLLAR)
            return i + 2
        if not dq and nxt == "'":
            m = _ANSI_BODY.match(text, i + 2)
            assert m is not None
            out.append(m.group().replace("$", LITERAL_DOLLAR))
            if text[m.end():m.end() + 1] == "'":
                return m.end() + 1
            scripts.append(self._code(text[i + 2:]))
            return self.n
        if not dq and nxt == '"':
            return self._dquote(i + 1, out, scripts)
        out.append(mark)
        return i + 1

    def _param(self, i: int, scripts: list[Script], dq: bool) -> int:
        """Index past the `}` closing the `${` whose opener ends at `i`. Quotes and substitutions
        inside are read; unterminated, it runs to the end and its text is also read as code."""
        text, n = self.text, self.n
        self._enter()
        start = i
        while i < n:
            m = _PARAM_PLAIN.match(text, i)
            if m:
                i = m.end()
                continue
            c = text[i]
            if c == "}":
                self.depth -= 1
                return i + 1
            if c == "\\":
                i += 2
            elif c == "'" and not dq:
                end = text.find("'", i + 1)
                i = n if end < 0 else end + 1
            elif c == '"':
                i = self._dquote(i, [], scripts)
            elif c == "$":
                i = self._dollar(i, [], scripts, False)
            elif c == "`":
                i = self._backtick(i, [], scripts, False)
            else:
                i += 1
        self.depth -= 1
        scripts.append(self._code(text[start:]))
        return n

    def _backtick(self, i: int, out: list[str], scripts: list[Script], dq: bool) -> int:
        """Read the backtick span at `i`. Its code is its own string, one level deeper, with one
        level of escapes off."""
        text = self.text
        m = _BACKTICK_BODY.match(text, i + 1)
        assert m is not None
        end = m.end()
        stop = end + 1 if text[end:end + 1] == "`" else self.n
        unescape = _BACKTICK_ESCAPE_DQ if dq else _BACKTICK_ESCAPE
        scripts.append(self._code(unescape.sub(r"\1", m.group())))
        out.append(text[i:stop])
        return stop

    def _arith_end(self, i: int, scripts: list[Script]) -> int:
        """Index past the `))` closing an arithmetic expression that starts at `i`, the code in it
        added to `scripts`. -1 if its first unmatched `)` is not doubled (so it was nested
        subshells); the end of the text if it never closes."""
        text, n = self.text, self.n
        depth = 0
        while i < n:
            m = _ARITH_PLAIN.match(text, i)
            if m:
                i = m.end()
                continue
            c = text[i]
            if c == "(":
                m = _PAREN_RUN.match(text, i)
                assert m is not None
                depth += m.end() - i
                i = m.end()
            elif c == ")":
                if depth:
                    depth -= 1
                    i += 1
                elif text[i + 1:i + 2] == ")":
                    return i + 2
                else:
                    return -1
            elif c == "\\":
                i += 2
            elif c == "$":
                i = self._dollar(i, [], scripts, True)
            elif c == "`":
                i = self._backtick(i, [], scripts, True)
            elif c == '"':
                i = self._dquote(i, [], scripts)
            else:
                end = text.find("'", i + 1)
                i = n if end < 0 else end + 1
        return n

    # --- heredocs -----------------------------------------------------------------------------
    def _newline(self) -> None:
        """Consume the newline at `self.i`; it is where the bodies of the line's heredocs begin."""
        self.i += 1
        if self.pending:
            self._bodies()

    def _bodies(self) -> None:
        """Fill each pending heredoc from `self.i`, in order. A body that never closes is left
        where it is, so the pass reads it as code, and is reported in `self.unclosed`."""
        text, n = self.text, self.n
        batch, self.pending = self.pending, []
        p = self.i
        for heredoc, delimiter, dash in batch:
            pos, closing, after = p, -1, n
            while pos < n:
                nl = text.find("\n", pos)
                line = text[pos:n if nl < 0 else nl]
                while not heredoc.quoted and nl >= 0 and _odd_backslashes(line):
                    nl2 = text.find("\n", nl + 1)
                    line = line[:-1] + text[nl + 1:n if nl2 < 0 else nl2]
                    nl = nl2
                following = n if nl < 0 else nl + 1
                if (line.lstrip("\t") if dash else line) == delimiter:
                    closing, after = pos, following
                    break
                pos = following
            if closing < 0:
                heredoc.body = text[p:]
                heredoc.span = (p, n)
                self.unclosed.append(heredoc)
                continue
            heredoc.body = text[p:closing]
            heredoc.closed = True
            heredoc.span = (p, after)
            if not heredoc.quoted:
                joined = _JOINED_LINE.sub(r"\1", heredoc.body)
                heredoc.scripts, heredoc.spans = _Parser(joined, self.depth)._scan_body()
            p = after
        self.i = p

    def _scan_body(self) -> tuple[list[Script], list[str]]:
        """The code in an unquoted heredoc body: its `$( )` and backtick spans, quotes being plain
        text there and a backslash keeping a `$` or backtick from running."""
        text, n = self.text, self.n
        scripts: list[Script] = []
        spans: list[str] = []
        i = 0
        while i < n:
            m = _HEREDOC_PLAIN.match(text, i)
            if m:
                i = m.end()
                continue
            c = text[i]
            start = i
            if c == "\\":
                i += 2
            elif c == "$":
                i = self._dollar(i, [], scripts, True)
                if text.startswith("$(", start):
                    spans.append(text[start:i])
            else:
                i = self._backtick(i, [], scripts, True)
                spans.append(text[start:i])
        return scripts, spans

    # --- redirects and simple commands ----------------------------------------------------------
    def _redirect(self, m: re.Match[str]) -> Redirect:
        text = self.text
        pos, op = m.start(), m.group(1)
        self.i = m.end()
        if op in ("<<", "<<-"):
            self._skip()
            start = self.i
            word = self._word()
            if self.i == start:
                return Redirect(pos, op, None, None)
            raw = text[start:self.i].replace("\\\n", "")
            heredoc = Heredoc(any(ch in raw for ch in "'\"\\"))
            delimiter = word.text.replace(LITERAL_DOLLAR, "$").replace(QUOTED_DOLLAR, "$")
            self.pending.append((heredoc, delimiter, op == "<<-"))
            return Redirect(pos, op, word, heredoc)
        self._skip()
        if op in (">&", "<&"):
            copy = _COPY.match(text, self.i)
            if copy:
                self.i = copy.end()
                return Redirect(pos, op, None, None)
        start = self.i
        word = self._word()
        return Redirect(pos, op, word if self.i > start else None, None)

    def _assignment(self, m: re.Match[str]) -> Assignment:
        text, n = self.text, self.n
        pos = self.i
        raw = m.group()
        append = raw.endswith("+=")
        array = "[" in raw
        name = raw[:raw.index("[")] if array else raw[:len(raw) - (2 if append else 1)]
        self.i = m.end()
        if text[self.i:self.i + 1] != "(":
            return Assignment(pos, name, append, array, self._word())
        start = self.i
        self.i += 1
        scripts: list[Script] = []
        while True:
            j = self._skip()
            if j >= n:
                break
            c = text[j]
            if c == "\n":
                self._newline()
            elif c == "#":
                end = text.find("\n", j)
                self.i = n if end < 0 else end
            elif c == ")":
                self.i = j + 1
                break
            else:
                word = self._word()
                if self.i == j:
                    self.i = j + 1
                scripts += word.scripts
        return Assignment(pos, name, append, True, Word(start, text[start:self.i], True, scripts, self.i))

    def _simple(self, ctx: _Ctx) -> None:
        text, n = self.text, self.n
        start = self.i
        assignments: list[Assignment] = []
        words: list[Word] = []
        redirects: list[Redirect] = []
        while True:
            i = self._skip()
            if i >= n:
                break
            c = text[i]
            if c in "\n;|(" or (c == ")" and self._closes(ctx)):
                break
            if c == "&" and text[i + 1:i + 2] != ">":
                break
            if c == ")":
                words.append(Word(i, ")", True, [], i + 1))
                self.i = i + 1
                continue
            if c == "#":
                end = text.find("\n", i)
                self.i = n if end < 0 else end
                continue
            if c in "0123456789{&<>" and not text.startswith(("<(", ">("), i):
                m = _REDIRECT.match(text, i)
                if m:
                    redirects.append(self._redirect(m))
                    continue
            if not words:
                am = _ASSIGNMENT.match(text, i)
                if am:
                    assignments.append(self._assignment(am))
                    continue
            word = self._word()
            if self.i == i:
                self.i = i + 1
                continue
            words.append(word)
        if assignments or words or redirects:
            ctx.stages.append(Simple(start, assignments, words, redirects))
            ctx.after_pipe = False

    def _trailing_redirects(self, comp: Compound) -> None:
        text = self.text
        while True:
            i = self._skip()
            if i < self.n and text[i] in "0123456789{&<>" and not text.startswith(
                    ("<(", ">("), i):
                m = _REDIRECT.match(text, i)
                if m:
                    comp.redirects.append(self._redirect(m))
                    continue
            return

    # --- the list machine ---------------------------------------------------------------------
    def _list(self, sub: bool) -> Script:
        """Read commands from `self.i` to the end of the text, or, for `sub`, to the `)` that ends a
        substitution. Heredocs still pending at the end get an empty body, as in bash."""
        text, n = self.text, self.n
        outer, self.pending = self.pending, []
        outer_sub, self.sub = self.sub, sub
        ctx = _Ctx()
        while True:
            if self.unclosed:
                # Bash takes the rest of the text as the body; the gate reads it as code too, in a
                # frame of its own, so a command later on is still a command.
                first = self.unclosed[0]
                self.unclosed = []
                self._push(ctx, _Frame("body", heredoc=first))
            i = self._skip()
            if i >= n:
                break
            top = ctx.stack[-1] if ctx.stack else None
            if top is not None and top.phase == "pattern":
                self._pattern(ctx, top)
                continue
            c = text[i]
            if c == "#":
                end = text.find("\n", i)
                self.i = n if end < 0 else end
            elif c == "\n":
                self.i = i
                self._newline()
                if not ctx.after_pipe:
                    self._flush(ctx)
            elif c == ";":
                top = self._real_top(ctx)
                arm = next((op for op in _END_OF_CASE_ARM if text.startswith(op, i)), None)
                if top is not None and top.kind == "case" and top.phase == "body" and arm:
                    self.i = i + len(arm)
                    self._peel(ctx)
                    self._end_arm(ctx, top)
                else:
                    self.i = i + 1
                    self._flush(ctx)
            elif c == "&" and text[i + 1:i + 2] != ">":
                if text[i + 1:i + 2] == "&":
                    self.i = i + 2
                    self._flush(ctx)
                else:
                    self.i = i + 1
                    self._flush(ctx, True)
            elif c == "|":
                if text[i + 1:i + 2] == "|":
                    self.i = i + 2
                    self._flush(ctx)
                else:
                    self.i = i + (2 if text[i + 1:i + 2] == "&" else 1)
                    ctx.after_pipe = True
            elif c == ")" and self._closes(ctx):
                self.i = i + 1
                top = self._real_top(ctx)
                if top is not None and top.kind == "subshell":
                    self._peel(ctx)
                    self._end_body(ctx, top)
                    self._close(ctx, top)
                else:
                    break
            else:
                self._command(ctx, i)
        self._unwind(ctx)
        for heredoc, _delimiter, _dash in self.pending:
            heredoc.span = (self.i, self.i)
        self.pending = outer
        self.sub = outer_sub
        return Script(ctx.pipes, text)

    def _closes(self, ctx: _Ctx) -> bool:
        """True if a `)` here ends something: a subshell, or the substitution being read. Any
        other `)` has no place, and is a word."""
        top = self._real_top(ctx)
        return self.sub or (top is not None and top.kind == "subshell")

    def _command(self, ctx: _Ctx, i: int) -> None:
        """Read the construct that starts at `i`, a command position: a compound command, a
        function, a reserved word, or a simple command."""
        text = self.text
        if ctx.stages and not ctx.after_pipe:
            self._flush(ctx)           # a command after a compound, with no operator between
        top = ctx.stack[-1] if ctx.stack else None
        reserved = _RESERVED.match(text, i)
        word = reserved.group() if reserved else ""
        if top is not None and top.kind == "function" and top.phase == "await" and not (
                text[i] == "(" or word in _OPENERS):
            self._abandon_function(ctx, top)
        if text[i] == "(":
            if text.startswith("((", i):
                found: list[Script] = []
                end = self._arith_end(i + 2, found)
                if end >= 0:
                    closed = end >= i + 4 and text[end - 2:end] == "))"
                    comp = Compound(i, "arith")
                    comp.words.append(Word(i + 2, text[i + 2:end - 2 if closed else end], True,
                                           found, end))
                    self.i = end
                    self._finish(ctx, comp)
                    return
            self.i = i + 1
            self._push(ctx, _Frame("subshell", Compound(i, "subshell")))
            return
        if reserved:
            self.i = reserved.end()
            if word in _SKIPPED_WORDS:
                if word != "!":
                    j = self._skip()
                    if _TIME_FLAG.match(text, j):
                        self.i = j + 2
                return
            if word in _CLOSERS:
                self._close_with(ctx, word)     # with no place, it is dropped
                return
            self._open(ctx, word, i)
            return
        function = _FUNCTION.match(text, i)
        if function:
            name = text[i:function.end()].split("(")[0].strip()
            self.i = function.end()
            self._push_function(ctx, i, name)
            return
        before = self.i
        self._simple(ctx)
        if self.i == before:
            self.i += 1

    def _open(self, ctx: _Ctx, word: str, pos: int) -> None:
        """A compound command opens at the reserved word `word`, whose end is `self.i`."""
        text = self.text
        if word in ("if", "while", "until"):
            self._push(ctx, _Frame(word, Compound(pos, word)))
        elif word == "{":
            self._push(ctx, _Frame("group", Compound(pos, "group")))
        elif word in ("for", "select"):
            self._for_header(ctx, word, pos)
        elif word == "case":
            comp = Compound(pos, "case")
            self._skip()
            start = self.i
            subject = self._word()
            if self.i > start:
                comp.words.append(subject)
            self._after_blanks_and_newlines()
            if _IN.match(text, self.i):
                self.i += 2
            frame = _Frame("case", comp)
            frame.phase = "pattern"
            self._push(ctx, frame)
        elif word == "function":
            self._skip()
            start = self.i
            name = self._word()
            j = self._skip()
            m = _EMPTY_PARENS.match(text, j)
            if m:
                self.i = m.end()
            self._push_function(ctx, pos, name.text if self.i > start else None)
        else:
            self._test(ctx, pos)

    def _after_blanks_and_newlines(self) -> None:
        text, n = self.text, self.n
        while True:
            j = self._skip()
            if j < n and text[j] == "\n":
                self._newline()
            else:
                return

    def _for_header(self, ctx: _Ctx, kind: str, pos: int) -> None:
        text = self.text
        comp = Compound(pos, kind)
        j = self._skip()
        if text.startswith("((", j):
            found: list[Script] = []
            end = self._arith_end(j + 2, found)
            if end >= 0:
                closed = end >= j + 4 and text[end - 2:end] == "))"
                comp.words.append(Word(j + 2, text[j + 2:end - 2 if closed else end], True, found,
                                       end))
                self.i = end
        else:
            name = self._word()
            comp.name = name.text or None
            saved = self.i
            self._after_blanks_and_newlines()
            if _IN.match(text, self.i):
                self.i += 2
                while True:
                    k = self._skip()
                    if k >= self.n or text[k] in "\n;)#":
                        break
                    word = self._word()
                    if self.i == k:
                        break
                    comp.words.append(word)
            else:
                self.i = saved
        frame = _Frame(kind, comp)
        frame.skip = True
        self._push(ctx, frame)

    def _test(self, ctx: _Ctx, pos: int) -> None:
        """`[[ ... ]]`: its operands are words whose substitutions run."""
        text, n = self.text, self.n
        comp = Compound(pos, "test")
        while True:
            j = self._skip()
            if j >= n:
                break
            c = text[j]
            if c == "\n":
                self._newline()
            elif _TEST_END.match(text, j):
                self.i = j + 2
                break
            elif c in "()<>&|;":
                self.i = j + (2 if text[j:j + 2] in ("&&", "||") else 1)
            else:
                word = self._word()
                if self.i == j:
                    self.i = j + 1
                    continue
                comp.words.append(word)
        self._finish(ctx, comp)

    def _push_function(self, ctx: _Ctx, pos: int, name: str | None) -> None:
        frame = _Frame("function", Compound(pos, "function", name))
        frame.phase = "await"
        self._push(ctx, frame)

    def _abandon_function(self, ctx: _Ctx, frame: _Frame) -> None:
        """A function header with no compound command after it: it holds nothing."""
        assert frame.comp is not None
        ctx.stack.pop()
        ctx.pipes, ctx.stages = frame.pipes, frame.stages
        ctx.stages.append(frame.comp)
        ctx.after_pipe = False

    # --- frames -------------------------------------------------------------------------------
    def _push(self, ctx: _Ctx, frame: _Frame) -> None:
        frame.pipes, frame.stages = ctx.pipes, ctx.stages
        ctx.pipes, ctx.stages = [], []
        ctx.stack.append(frame)
        ctx.after_pipe = False

    def _flush(self, ctx: _Ctx, background: bool = False) -> None:
        """End the pipeline being read."""
        if ctx.stages:
            ctx.pipes.append(Pipeline(ctx.stages, background))
            ctx.stages = []
        ctx.after_pipe = False

    def _end_body(self, ctx: _Ctx, frame: _Frame) -> None:
        """The list being read is a body of the compound on `frame`."""
        self._flush(ctx)
        if frame.skip:
            frame.skip = False
        elif frame.comp is not None:
            frame.comp.bodies.append(Script(ctx.pipes, self.text))
        ctx.pipes, ctx.stages = [], []

    def _close(self, ctx: _Ctx, frame: _Frame) -> None:
        """Pop `frame`, and the compound it holds becomes a stage of the pipeline it sits in."""
        ctx.stack.pop()
        ctx.pipes, ctx.stages = frame.pipes, frame.stages
        assert frame.comp is not None
        self._finish(ctx, frame.comp)

    def _finish(self, ctx: _Ctx, comp: Compound) -> None:
        """`comp` is complete: its redirects follow, and it is the body of a function waiting for
        one, or a stage."""
        while True:
            self._trailing_redirects(comp)
            top = ctx.stack[-1] if ctx.stack else None
            if top is not None and top.kind == "function" and top.phase == "await":
                assert top.comp is not None
                top.comp.bodies.append(Script([Pipeline([comp], False)], self.text))
                ctx.stack.pop()
                ctx.pipes, ctx.stages = top.pipes, top.stages
                comp = top.comp
                continue
            ctx.stages.append(comp)
            ctx.after_pipe = False
            return

    def _peel(self, ctx: _Ctx) -> None:
        """Close the bodies of unclosed heredocs on top of the stack, so a closing word reaches
        the compound under them."""
        while ctx.stack and ctx.stack[-1].kind == "body":
            self._pop_body(ctx)

    def _pop_body(self, ctx: _Ctx) -> None:
        frame = ctx.stack.pop()
        self._flush(ctx)
        assert frame.heredoc is not None
        frame.heredoc.scripts = [Script(ctx.pipes, self.text)]
        ctx.pipes, ctx.stages = frame.pipes, frame.stages

    def _unwind(self, ctx: _Ctx) -> None:
        """The text ended: close everything still open."""
        while ctx.stack:
            frame = ctx.stack[-1]
            if frame.kind == "body":
                self._pop_body(ctx)
            elif frame.kind == "case" and frame.phase == "pattern":
                self._close(ctx, frame)
            elif frame.kind == "function":
                self._abandon_function(ctx, frame)
            else:
                self._end_body(ctx, frame)
                self._close(ctx, frame)
        self._flush(ctx)

    def _real_top(self, ctx: _Ctx) -> _Frame | None:
        """The compound on top of the stack, under any unclosed heredoc bodies."""
        for frame in reversed(ctx.stack):
            if frame.kind != "body":
                return frame
        return None

    def _close_with(self, ctx: _Ctx, word: str) -> bool:
        """Apply the closing reserved word `word` (already consumed) to the compound it closes or
        continues. False if it has no place, which drops it."""
        top = self._real_top(ctx)
        if top is None:
            return False
        kind = top.kind
        if kind == "if":
            ends, closes = word in ("then", "elif", "else", "fi"), word == "fi"
        elif kind in ("while", "until", "for", "select"):
            ends, closes = word in ("do", "done"), word == "done"
        elif kind == "group":
            ends = closes = word == "}"
        elif kind == "case" and top.phase == "body":
            ends = closes = word == "esac"
        else:
            return False
        if not ends:
            return False
        self._peel(ctx)
        if kind == "case":
            self._end_arm(ctx, top)
        else:
            self._end_body(ctx, top)
        if closes:
            self._close(ctx, top)
        return True

    def _end_arm(self, ctx: _Ctx, frame: _Frame) -> None:
        """The command list of a `case` arm ends."""
        self._flush(ctx)
        assert frame.comp is not None
        frame.comp.bodies.append(Script(ctx.pipes, self.text))
        ctx.pipes, ctx.stages = [], []
        frame.phase = "pattern"

    def _pattern(self, ctx: _Ctx, frame: _Frame) -> None:
        """Read what stands between the arms of a `case`: the next arm's patterns up to their
        `)`, or the `esac` that ends it."""
        text, n = self.text, self.n
        assert frame.comp is not None
        while True:
            i = self._skip()
            if i >= n:
                return
            c = text[i]
            if c == "\n":
                self._newline()
            elif c == "#":
                end = text.find("\n", i)
                self.i = n if end < 0 else end
            elif c == ";":
                self.i = i + 1
            else:
                break
        if _ESAC.match(text, i):
            self.i = i + 4
            self._close(ctx, frame)
            return
        if c == "(":
            self.i = i + 1
        while True:
            j = self._skip()
            word = self._word()
            moved = self.i > j
            if moved:
                frame.comp.words.append(word)
            k = self._skip()
            c = text[k:k + 1]
            if c == "|":
                self.i = k + 1
            elif c == ")":
                self.i = k + 1
                break
            elif k >= n:
                break
            elif not moved:
                self.i = k + 1
        frame.phase = "body"
