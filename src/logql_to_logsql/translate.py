from ctypes import CDLL, Structure, c_char_p
from dataclasses import dataclass
from pathlib import Path


class _CTranslateResult(Structure):
    _fields_ = [
        ("logsql", c_char_p),
        ("kind", c_char_p),
        ("error", c_char_p),
    ]


@dataclass
class TranslateResult:
    logsql: str
    kind: str
    error: str


def translate_logql_to_logsql(logql_query: str) -> TranslateResult:
    lib_path = Path(__file__).parent / "libs" / "logql_to_logsql_go.so"
    lib = CDLL(str(lib_path))

    lib.TranslateLogQLToLogsQL.restype = _CTranslateResult
    res = lib.TranslateLogQLToLogsQL(logql_query.encode())

    return TranslateResult(
        logsql=res.logsql.decode() if res.logsql else "",
        kind=res.kind.decode() if res.kind else "",
        error=res.error.decode() if res.error else "",
    )
