from ctypes import CDLL, c_char_p
from pathlib import Path


def translate_logql_to_logsql(logql_query: str) -> str:
    lib_path = Path(__file__).parent / "libs" / "logql_to_logsql_go.so"
    lib = CDLL(str(lib_path))
    lib.TranslateLogQLToLogsQL.restype = c_char_p
    result = lib.TranslateLogQLToLogsQL(logql_query.encode())
    return result.decode()
