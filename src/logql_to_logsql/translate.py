from ctypes import CDLL


def translate_logql_to_logsql(logql_query: str) -> str:
    logql_to_logsql_go_lib = CDLL("./logql_to_logsql_go.so")
    return logql_to_logsql_go_lib.TranslateLogQLToLogsQL(logql_query)
