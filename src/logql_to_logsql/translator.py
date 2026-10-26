"""LogQL to LogsQL translator, ported from the reference Go implementation."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class QueryKind(str, Enum):
    LOGS = "logs"
    STATS = "stats"


@dataclass
class QueryInfo:
    kind: QueryKind
    logsql: str


class TranslationError(ValueError):
    pass


def translate_logql_to_logsql(logql: str) -> str:
    """Translate a LogQL query string to a LogsQL query string."""
    return _translate(logql).logsql


def translate(logql: str) -> QueryInfo:
    """Translate a LogQL query string and return a QueryInfo with kind and logsql."""
    return _translate(logql)


def _translate(logql: str) -> QueryInfo:
    q = logql.strip()
    if not q:
        raise TranslationError("logql query is required")
    if q.startswith("|"):
        q = "{} " + q

    tokens = _tokenize(q)
    parser = _Parser(tokens)
    expr = parser.parse_expr()

    if isinstance(expr, (_RangeAggExpr, _VectorAggExpr)):
        logsql = _translate_sample_expr(expr)
        return QueryInfo(kind=QueryKind.STATS, logsql=logsql)
    if isinstance(expr, (_MatchersExpr, _PipelineExpr)):
        b = _Builder()
        b.add_log_selector(expr)
        return QueryInfo(kind=QueryKind.LOGS, logsql=b.string())

    raise TranslationError(f"unsupported LogQL expression type: {type(expr).__name__}")


# ---------------------------------------------------------------------------
# AST nodes
# ---------------------------------------------------------------------------

@dataclass
class _Matcher:
    name: str
    op: str   # =, !=, =~, !~
    value: str


@dataclass
class _MatchersExpr:
    matchers: list[_Matcher]


@dataclass
class _PipelineExpr:
    matchers: list[_Matcher]
    stages: list


@dataclass
class _LineFilterStage:
    # list of (op, value) — op is |=, !=, |~, !~
    filters: list[tuple[str, str]]


@dataclass
class _LabelFilterStage:
    expr: "_LabelFilter"


@dataclass
class _ParserStage:
    op: str           # json, logfmt, regexp, pattern, unpack
    param: str = ""


@dataclass
class _LogfmtParserStage:
    # plain `| logfmt` with no expressions
    pass


@dataclass
class _JSONExprStage:
    # | json alias="field", ...
    exprs: list[tuple[str, str]]   # (identifier, expression)


@dataclass
class _LogfmtExprStage:
    # | logfmt alias="field", ...
    exprs: list[tuple[str, str]]


@dataclass
class _DropStage:
    items: list[str]   # raw items as they appear


@dataclass
class _KeepStage:
    items: list[str]


@dataclass
class _LineFmtStage:
    value: str


@dataclass
class _LabelFmtStage:
    formats: list[tuple[str, str, bool]]   # (name, value, is_rename)


@dataclass
class _UnwrapExpr:
    identifier: str
    op: str = ""


@dataclass
class _LogRangeExpr:
    selector: "_MatchersExpr | _PipelineExpr"
    interval: str
    offset: str = ""
    unwrap: Optional[_UnwrapExpr] = None


@dataclass
class _RangeAggExpr:
    op: str
    log_range: _LogRangeExpr
    params: Optional[float] = None


@dataclass
class _Grouping:
    without: bool
    groups: list[str]

    def singleton(self) -> bool:
        return not self.without and len(self.groups) == 0

    def noop(self) -> bool:
        return not self.without and self.groups == ["_stream"]


@dataclass
class _VectorAggExpr:
    op: str
    inner: "_RangeAggExpr | _VectorAggExpr"
    grouping: Optional[_Grouping] = None
    params: Optional[int] = None


# Label filter AST

@dataclass
class _StringLabelFilter:
    matcher: _Matcher


@dataclass
class _NumericLabelFilter:
    name: str
    op: str   # =, !=, >, >=, <, <=
    value: str


@dataclass
class _BinaryLabelFilter:
    left: "_LabelFilter"
    right: "_LabelFilter"
    and_: bool


_LabelFilter = "_StringLabelFilter | _NumericLabelFilter | _BinaryLabelFilter"


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r'(?P<duration>[0-9]+(?:ms|[smhdwy])(?:[0-9]+(?:ms|[smhdwy]))*)'
    r'|(?P<number>[0-9]+(?:\.[0-9]+)?(?:e[+-]?[0-9]+)?)'
    r'|(?P<quoted>"(?:[^"\\]|\\.)*"|`[^`]*`)'
    r'|(?P<op>!=|=~|!~|>=|<=|[|!~<>=,(){}[\]+\-*/])'
    r'|(?P<word>[a-zA-Z_][a-zA-Z0-9_.:-]*)'
    r'|(?P<ws>\s+)',
    re.ASCII,
)


def _tokenize(s: str) -> list[str]:
    tokens = []
    pos = 0
    while pos < len(s):
        m = _TOKEN_RE.match(s, pos)
        if not m:
            raise TranslationError(f"unexpected character at position {pos}: {s[pos]!r}")
        pos = m.end()
        if m.lastgroup == "ws":
            continue
        tokens.append(m.group())
    return tokens


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

class _Parser:
    def __init__(self, tokens: list[str]):
        self._t = tokens
        self._i = 0

    def _peek(self) -> Optional[str]:
        return self._t[self._i] if self._i < len(self._t) else None

    def _next(self) -> str:
        tok = self._t[self._i]
        self._i += 1
        return tok

    def _expect(self, val: str) -> str:
        tok = self._next()
        if tok != val:
            raise TranslationError(f"expected {val!r}, got {tok!r}")
        return tok

    def _at_end(self) -> bool:
        return self._i >= len(self._t)

    # -- top-level --

    def parse_expr(self):
        # Could be vector agg (sum, topk, ...) or range agg (rate, ...) or log selector
        tok = self._peek()
        if tok == "(":
            # parenthesized expression: (rate(...)) or (sum(...))
            self._next()
            expr = self.parse_expr()
            self._expect(")")
            return expr
        if tok in ("sum", "avg", "min", "max", "count", "topk", "bottomk"):
            return self._parse_vector_agg()
        if tok in ("rate", "count_over_time", "avg_over_time", "sum_over_time",
                   "min_over_time", "max_over_time", "quantile_over_time", "bytes_rate",
                   "bytes_over_time", "absent_over_time"):
            return self._parse_range_agg()
        return self._parse_log_selector()

    def _parse_vector_agg(self):
        op = self._next()
        params = None
        grouping = None

        # `by`/`without` can appear before the opening paren: sum by (label) (...)
        if self._peek() in ("by", "without"):
            grouping = self._parse_grouping()

        self._expect("(")
        # topk/bottomk have a numeric first param
        if op in ("topk", "bottomk"):
            params = int(self._next())
            self._expect(",")
        # optional `by (...)` or `without (...)` before the inner expr
        if self._peek() in ("by", "without"):
            grouping = self._parse_grouping()
        inner = self.parse_expr()
        self._expect(")")
        # grouping can also appear after the closing paren
        if self._peek() in ("by", "without"):
            grouping = self._parse_grouping()
        return _VectorAggExpr(op=op, inner=inner, grouping=grouping, params=params)

    def _parse_grouping(self) -> _Grouping:
        tok = self._next()   # by / without
        without = tok == "without"
        self._expect("(")
        groups = []
        while self._peek() != ")":
            groups.append(self._next())
            if self._peek() == ",":
                self._next()
        self._expect(")")
        return _Grouping(without=without, groups=groups)

    def _parse_range_agg(self):
        op = self._next()
        params = None
        self._expect("(")
        # quantile_over_time has a numeric first param
        if op == "quantile_over_time":
            params = float(self._next())
            self._expect(",")
        log_range = self._parse_log_range()
        self._expect(")")
        return _RangeAggExpr(op=op, log_range=log_range, params=params)

    def _parse_log_range(self) -> _LogRangeExpr:
        # selector [interval] optional unwrap optional offset
        sel = self._parse_log_selector()
        # optional unwrap before interval
        unwrap = None
        if self._peek() == "|":
            # peek further
            saved = self._i
            self._next()  # consume |
            if self._peek() == "unwrap":
                self._next()  # consume unwrap
                identifier = self._next()
                unwrap = _UnwrapExpr(identifier=identifier)
            else:
                self._i = saved
        self._expect("[")
        interval = self._next()
        self._expect("]")
        offset = ""
        if self._peek() == "offset":
            self._next()
            offset = self._next()
        return _LogRangeExpr(selector=sel, interval=interval, offset=offset, unwrap=unwrap)

    def _parse_log_selector(self):
        matchers = self._parse_stream_selector()
        stages = []
        while self._peek() == "|" or self._peek() in ("!=",):
            # line filters can start with != (negative)
            if self._peek() == "!=":
                stages.append(self._parse_line_filter_stage())
                continue
            self._next()  # consume |
            nxt = self._peek()
            if nxt in ("=", "~"):
                # |= or |~  — put the | back conceptually; handle as line filter
                stages.append(self._parse_line_filter_after_pipe())
            elif nxt == "!":
                stages.append(self._parse_line_filter_after_pipe())
            elif nxt == "json":
                stages.append(self._parse_json_stage())
            elif nxt == "logfmt":
                stages.append(self._parse_logfmt_stage())
            elif nxt == "regexp":
                self._next()
                param = self._parse_string()
                stages.append(_ParserStage(op="regexp", param=param))
            elif nxt == "pattern":
                self._next()
                param = self._parse_string()
                stages.append(_ParserStage(op="pattern", param=param))
            elif nxt == "unpack":
                self._next()
                stages.append(_ParserStage(op="unpack"))
            elif nxt == "drop":
                stages.append(self._parse_drop_stage())
            elif nxt == "keep":
                stages.append(self._parse_keep_stage())
            elif nxt == "line_format":
                self._next()
                val = self._parse_string()
                stages.append(_LineFmtStage(value=val))
            elif nxt == "label_format":
                stages.append(self._parse_label_fmt_stage())
            elif nxt == "decolorize":
                self._next()
                stages.append(_ParserStage(op="decolorize"))
            elif nxt == "unwrap":
                # unwrap inside log selector (for range agg parsing)
                self._next()
                identifier = self._next()
                stages.append(_UnwrapExpr(identifier=identifier))
            else:
                # label filter
                stages.append(_LabelFilterStage(expr=self._parse_label_filter()))
        if not stages:
            return _MatchersExpr(matchers=matchers)
        return _PipelineExpr(matchers=matchers, stages=stages)

    def _parse_stream_selector(self) -> list[_Matcher]:
        self._expect("{")
        matchers = []
        while self._peek() != "}":
            name = self._next()
            op = self._next()   # =, !=, =~, !~
            value = self._parse_string()
            matchers.append(_Matcher(name=name, op=op, value=value))
            if self._peek() == ",":
                self._next()
        self._expect("}")
        return matchers

    def _parse_line_filter_stage(self) -> _LineFilterStage:
        """Parse a leading != line filter (negative without pipe)."""
        op = self._next()   # !=
        value = self._parse_string()
        return _LineFilterStage(filters=[(op, value)])

    def _parse_line_filter_after_pipe(self) -> _LineFilterStage:
        """Parse =, ~, != after a | has already been consumed."""
        tok = self._next()   # =, ~, or !
        if tok == "=":
            value = self._parse_string()
            return _LineFilterStage(filters=[("|=", value)])
        elif tok == "~":
            value = self._parse_string()
            return _LineFilterStage(filters=[("|~", value)])
        elif tok == "!":
            nxt = self._next()   # = or ~
            value = self._parse_string()
            op = "!=" if nxt == "=" else "!~"
            return _LineFilterStage(filters=[(op, value)])
        raise TranslationError(f"unexpected line filter token: {tok!r}")

    def _parse_json_stage(self):
        self._next()  # json
        # check for expression list: | json alias="field", ...
        if self._peek() and self._peek() not in ("|", "}", "]", ")") and self._peek() != "offset":
            exprs = self._parse_label_extraction_exprs()
            return _JSONExprStage(exprs=exprs)
        return _ParserStage(op="json")

    def _parse_logfmt_stage(self):
        self._next()  # logfmt
        if self._peek() and self._peek() not in ("|", "}", "]", ")") and self._peek() != "offset":
            exprs = self._parse_label_extraction_exprs()
            return _LogfmtExprStage(exprs=exprs)
        return _LogfmtParserStage()

    def _parse_label_extraction_exprs(self) -> list[tuple[str, str]]:
        """Parse alias="field", alias2="field2", ..."""
        exprs = []
        while True:
            identifier = self._next()
            self._expect("=")
            value = self._parse_string()
            exprs.append((identifier, value))
            if self._peek() == ",":
                self._next()
            else:
                break
        return exprs

    def _parse_drop_stage(self) -> _DropStage:
        self._next()  # drop
        items = self._parse_label_list()
        return _DropStage(items=items)

    def _parse_keep_stage(self) -> _KeepStage:
        self._next()  # keep
        items = self._parse_label_list()
        return _KeepStage(items=items)

    def _parse_label_list(self) -> list[str]:
        """Parse comma-separated label names or matchers like name=~"val"."""
        items = []
        while self._peek() and self._peek() not in ("|", "}", "]", ")"):
            # collect tokens until next comma or pipe
            start = self._i
            name = self._next()
            if self._peek() in ("=", "!=", "=~", "!~"):
                op = self._next()
                val = self._parse_string()
                items.append(f'{name}{op}"{val}"')
            else:
                items.append(name)
            if self._peek() == ",":
                self._next()
            else:
                break
        return items

    def _parse_label_fmt_stage(self) -> _LabelFmtStage:
        self._next()  # label_format
        formats = []
        while self._peek() and self._peek() not in ("|", "}", "]", ")"):
            dst = self._next()
            self._expect("=")
            src_or_tmpl = self._parse_string()
            # If src matches a bare label name it's a rename, otherwise a template
            is_rename = _is_bare_field_name(src_or_tmpl)
            formats.append((dst, src_or_tmpl, is_rename))
            if self._peek() == ",":
                self._next()
            else:
                break
        return _LabelFmtStage(formats=formats)

    def _parse_label_filter(self):
        # name op value, possibly chained with and/or (,)
        left = self._parse_label_filter_single()
        while self._peek() == ",":
            self._next()
            right = self._parse_label_filter_single()
            left = _BinaryLabelFilter(left=left, right=right, and_=True)
        return left

    def _parse_label_filter_single(self):
        name = self._next()
        op = self._next()   # =, !=, =~, !~, >, >=, <, <=
        if op in (">", ">=", "<", "<=", "=="):
            value = self._next()
            return _NumericLabelFilter(name=name, op=op, value=value)
        value = self._parse_string()
        return _StringLabelFilter(matcher=_Matcher(name=name, op=op, value=value))

    def _parse_string(self) -> str:
        tok = self._next()
        if tok.startswith('"'):
            return tok[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        if tok.startswith('`'):
            return tok[1:-1]
        return tok


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class _Builder:
    def __init__(self):
        self._parts: list[str] = []
        self._has_pipe = False

    def string(self) -> str:
        return " ".join(self._parts).strip()

    def _add_pipe(self, pipe: str):
        self._has_pipe = True
        self._parts.append("| " + pipe)

    def _add_filter(self, f: str):
        f = f.strip()
        if not f:
            return
        if self._has_pipe:
            self._add_pipe("filter " + f)
        else:
            self._parts.append(f)

    def add_log_selector(self, expr, extra_filters: list[str] = None):
        if isinstance(expr, _MatchersExpr):
            self._parts.append(_render_stream_selector(expr.matchers))
            for f in (extra_filters or []):
                self._add_filter(f)
        elif isinstance(expr, _PipelineExpr):
            self._parts.append(_render_stream_selector(expr.matchers))
            for f in (extra_filters or []):
                self._add_filter(f)
            for stage in expr.stages:
                self._add_stage(stage)
        else:
            raise TranslationError(f"unsupported log selector type: {type(expr).__name__}")

    def _add_stage(self, stage):
        if isinstance(stage, _LineFilterStage):
            for op, value in stage.filters:
                self._add_filter(_translate_line_filter(op, value))
        elif isinstance(stage, _LabelFilterStage):
            f = _translate_label_filter(stage.expr)
            self._add_filter(f)
        elif isinstance(stage, _ParserStage):
            pipe = _translate_parser_stage(stage)
            self._add_pipe(pipe)
        elif isinstance(stage, _LogfmtParserStage):
            self._add_pipe("unpack_logfmt")
        elif isinstance(stage, _JSONExprStage):
            for pipe in _translate_label_extraction_parser("unpack_json", stage.exprs):
                self._add_pipe(pipe)
        elif isinstance(stage, _LogfmtExprStage):
            for pipe in _translate_label_extraction_parser("unpack_logfmt", stage.exprs):
                self._add_pipe(pipe)
        elif isinstance(stage, _DropStage):
            _translate_drop(self, stage.items)
        elif isinstance(stage, _KeepStage):
            _translate_keep(self, stage.items)
        elif isinstance(stage, _LineFmtStage):
            pattern = _convert_loki_template(stage.value)
            self._add_pipe("format " + _quote_string(pattern))
        elif isinstance(stage, _LabelFmtStage):
            renames = []
            for name, value, is_rename in stage.formats:
                if is_rename:
                    renames.append(f"{_quote_field(value)} as {_quote_field(name)}")
                else:
                    pattern = _convert_loki_template(value)
                    self._add_pipe("format " + _quote_string(pattern) + " as " + _quote_field(name))
            if renames:
                self._add_pipe("rename " + ", ".join(renames))
        else:
            raise TranslationError(f"unsupported pipeline stage: {type(stage).__name__}")


# ---------------------------------------------------------------------------
# Translation helpers
# ---------------------------------------------------------------------------

def _render_stream_selector(matchers: list[_Matcher]) -> str:
    if not matchers:
        return "{}"
    parts = []
    for m in matchers:
        parts.append(f'{m.name}{m.op}"{m.value}"')
    return "{" + ",".join(parts) + "}"


def _translate_line_filter(op: str, value: str) -> str:
    if op == "|=":
        return _quote_string(value)
    elif op == "!=":
        return "!" + _quote_string(value)
    elif op == "|~":
        return "~" + _quote_string(value)
    elif op == "!~":
        return "NOT ~" + _quote_string(value)
    raise TranslationError(f"unsupported line filter op: {op!r}")


def _translate_label_filter(f) -> str:
    if isinstance(f, _StringLabelFilter):
        return _translate_matcher(f.matcher)
    elif isinstance(f, _NumericLabelFilter):
        return _translate_scalar_filter(f.name, f.op, f.value)
    elif isinstance(f, _BinaryLabelFilter):
        left = _translate_label_filter(f.left)
        right = _translate_label_filter(f.right)
        op = " AND " if f.and_ else " OR "
        return f"({left}{op}{right})"
    raise TranslationError(f"unsupported label filter: {type(f).__name__}")


def _translate_matcher(m: _Matcher) -> str:
    name = _quote_field(m.name)
    if m.op == "=":
        return f"{name}:={_quote_scalar(m.value)}"
    elif m.op == "!=":
        return f"-{name}:={_quote_scalar(m.value)}"
    elif m.op == "=~":
        return f"{name}:~{_quote_string(m.value)}"
    elif m.op == "!~":
        return f"-{name}:~{_quote_string(m.value)}"
    raise TranslationError(f"unsupported matcher op: {m.op!r}")


def _translate_scalar_filter(name: str, op: str, value: str) -> str:
    n = _quote_field(name)
    v = _quote_scalar(value)
    mapping = {
        "=": f"{n}:={v}",
        "==": f"{n}:={v}",
        "!=": f"-{n}:={v}",
        ">": f"{n}:>{v}",
        ">=": f"{n}:>={v}",
        "<": f"{n}:<{v}",
        "<=": f"{n}:<={v}",
    }
    if op not in mapping:
        raise TranslationError(f"unsupported scalar filter op: {op!r}")
    return mapping[op]


def _translate_parser_stage(s: _ParserStage) -> str:
    if s.op in ("json", "unpack"):
        return "unpack_json"
    elif s.op == "logfmt":
        return "unpack_logfmt"
    elif s.op == "regexp":
        return "extract_regexp " + _quote_string(s.param)
    elif s.op == "pattern":
        return "extract " + _quote_string(s.param)
    elif s.op == "decolorize":
        return "decolorize"
    raise TranslationError(f"unsupported parser op: {s.op!r}")


def _translate_label_extraction_parser(pipe: str, exprs: list[tuple[str, str]]) -> list[str]:
    if not exprs:
        return [pipe]

    # Build ordered unique expression list
    expr_order = []
    expr_seen: set[str] = set()
    expr_to_ids: dict[str, list[str]] = {}
    keep_expr: set[str] = set()

    for identifier, expression in exprs:
        if not expression:
            raise TranslationError("empty json/logfmt extraction expression isn't supported")
        if not _is_simple_extraction_field(expression):
            raise TranslationError("complex json/logfmt extraction expressions aren't supported yet")
        if expression not in expr_seen:
            expr_seen.add(expression)
            expr_order.append(expression)
        expr_to_ids.setdefault(expression, []).append(identifier)
        if identifier == expression:
            keep_expr.add(expression)

    fields = [_quote_field(e) for e in expr_order]
    pipes = [pipe + " fields (" + ", ".join(fields) + ")"]

    seen_formats: set[str] = set()
    for expr in expr_order:
        for id_ in expr_to_ids[expr]:
            if id_ == expr:
                continue
            key = expr + "\x00" + id_
            if key in seen_formats:
                continue
            seen_formats.add(key)
            pattern = "<" + expr + ">"
            pipes.append("format " + _quote_string(pattern) + " as " + _quote_field(id_))

    drop = [_quote_field(e) for e in expr_order if e not in keep_expr]
    if drop:
        pipes.append("delete " + ", ".join(drop))

    return pipes


def _translate_drop(b: _Builder, items: list[str]):
    pending_names = []

    def flush_names():
        if pending_names:
            b._add_pipe("delete " + ", ".join(pending_names))
            pending_names.clear()

    for item in items:
        item = item.strip()
        if not item:
            continue
        if any(c in item for c in ("=", "!", "~")):
            flush_names()
            m = _parse_label_matcher_str(item)
            cond = _translate_matcher_to_cond(m)
            b._add_pipe(f"format if ({cond}) \"\" as {_quote_field(m.name)}")
        else:
            pending_names.append(item)

    flush_names()


def _translate_keep(b: _Builder, items: list[str]):
    unconditional = []
    conditional: list[_Matcher] = []

    for item in items:
        item = item.strip()
        if not item:
            continue
        if any(c in item for c in ("=", "!", "~")):
            conditional.append(_parse_label_matcher_str(item))
        else:
            unconditional.append(item)

    if unconditional or conditional:
        name_seen: set[str] = set()
        keep_names = []
        for name in unconditional:
            if name not in name_seen:
                name_seen.add(name)
                keep_names.append(name)
        for m in conditional:
            if m.name not in name_seen:
                name_seen.add(m.name)
                keep_names.append(m.name)
        if keep_names:
            b._add_pipe("keep " + ", ".join(keep_names))

    for m in conditional:
        cond = _translate_matcher_to_cond(m)
        pattern = "<" + m.name + ">"
        b._add_pipe(f"format if ({cond}) {_quote_string(pattern)} as {_quote_field(m.name)}")


def _parse_label_matcher_str(s: str) -> _Matcher:
    """Parse a single label matcher like name=~"value" from a string."""
    for op in ("=~", "!~", "!=", "="):
        if op in s:
            idx = s.index(op)
            name = s[:idx].strip()
            value = s[idx + len(op):].strip().strip('"')
            return _Matcher(name=name, op=op, value=value)
    raise TranslationError(f"cannot parse label matcher: {s!r}")


def _translate_matcher_to_cond(m: _Matcher) -> str:
    name = _quote_field(m.name)
    if m.op == "=":
        return f"{name}:={_quote_scalar(m.value)}"
    elif m.op == "!=":
        return f"-{name}:={_quote_scalar(m.value)}"
    elif m.op == "=~":
        return f"{name}:~{_quote_string(m.value)}"
    elif m.op == "!~":
        return f"-{name}:~{_quote_string(m.value)}"
    raise TranslationError(f"unsupported matcher op: {m.op!r}")


# ---------------------------------------------------------------------------
# Metric expression translation
# ---------------------------------------------------------------------------

def _translate_sample_expr(expr) -> str:
    if isinstance(expr, _RangeAggExpr):
        return _translate_range_agg(expr, grouping=None)
    if isinstance(expr, _VectorAggExpr):
        return _translate_vector_agg(expr)
    raise TranslationError(f"unsupported metric expression: {type(expr).__name__}")


def _translate_vector_agg(e: _VectorAggExpr) -> str:
    if e.op == "sum":
        if not isinstance(e.inner, _RangeAggExpr):
            raise TranslationError("only sum(<range_aggregation>) is supported for now")
        # sum() with no grouping → singleton (no by clause)
        grouping = e.grouping if e.grouping is not None else _Grouping(without=False, groups=[])
        return _translate_range_agg(e.inner, grouping=grouping)
    if e.op in ("topk", "bottomk"):
        inner = _translate_sample_expr(e.inner)
        if e.grouping and not e.grouping.singleton():
            raise TranslationError("topk/bottomk with grouping isn't supported yet")
        order = "value desc" if e.op == "topk" else "value"
        return f"{inner} | first {e.params} ({order})"
    raise TranslationError(f"unsupported vector aggregation: {e.op!r}")


def _translate_range_agg(e: _RangeAggExpr, grouping: Optional[_Grouping]) -> str:
    b = _Builder()

    time_filter = _time_filter_from_log_range(e.log_range)
    extra = [time_filter] if time_filter else []
    b.add_log_selector(e.log_range.selector, extra_filters=extra)

    # by groups
    if grouping is None:
        by = ["_stream"]
    elif grouping.singleton():
        by = []
    elif grouping.noop():
        by = ["_stream"]
    else:
        by = grouping.groups

    stats = _range_agg_to_stats_pipe(e)

    if by:
        b._add_pipe(f"stats by ({', '.join(by)}) {stats}")
    else:
        b._add_pipe(f"stats {stats}")

    return b.string()


def _time_filter_from_log_range(r: _LogRangeExpr) -> str:
    f = f"_time:{r.interval}"
    if r.offset:
        f += f" offset {r.offset}"
    return f


def _range_agg_to_stats_pipe(e: _RangeAggExpr) -> str:
    op = e.op
    if op == "rate":
        if e.log_range.unwrap:
            raise TranslationError("rate(...| unwrap ...) isn't supported yet")
        return "rate() as value"
    if op == "count_over_time":
        if e.log_range.unwrap:
            raise TranslationError("count_over_time(...| unwrap ...) isn't supported yet")
        return "count() as value"
    if op in ("avg_over_time", "sum_over_time", "min_over_time", "max_over_time"):
        if not e.log_range.unwrap:
            raise TranslationError(f"{op} without unwrap isn't supported")
        field = _quote_field(e.log_range.unwrap.identifier)
        fn_map = {
            "avg_over_time": "avg",
            "sum_over_time": "sum",
            "min_over_time": "min",
            "max_over_time": "max",
        }
        return f"{fn_map[op]}({field}) as value"
    if op == "quantile_over_time":
        if not e.log_range.unwrap or e.params is None:
            raise TranslationError("quantile_over_time requires unwrap and quantile parameter")
        field = _quote_field(e.log_range.unwrap.identifier)
        return f"quantile({e.params}, {field}) as value"
    raise TranslationError(f"unsupported range aggregation: {op!r}")


# ---------------------------------------------------------------------------
# String / quoting utilities
# ---------------------------------------------------------------------------

_LOKI_TEMPLATE_VAR_RE = re.compile(r'\{\{\s*\.\s*([a-zA-Z0-9_.:-]+)\s*\}\}')


def _convert_loki_template(s: str) -> str:
    return _LOKI_TEMPLATE_VAR_RE.sub(r'<\1>', s)


def _quote_string(s: str) -> str:
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _quote_scalar(s: str) -> str:
    if not s:
        return '""'
    if _is_bare_scalar(s):
        return s
    return _quote_string(s)


def _quote_field(name: str) -> str:
    if not name:
        return '""'
    if _is_bare_field_name(name):
        return name
    return _quote_string(name)


def _is_bare_field_name(s: str) -> bool:
    return bool(s) and bool(re.fullmatch(r'[a-zA-Z0-9_.]+', s))


def _is_bare_scalar(s: str) -> bool:
    return bool(s) and bool(re.fullmatch(r'[a-zA-Z0-9_.:/\-]+', s))


def _is_simple_extraction_field(s: str) -> bool:
    return bool(s) and bool(re.fullmatch(r'[a-zA-Z0-9_.\-]+', s))
