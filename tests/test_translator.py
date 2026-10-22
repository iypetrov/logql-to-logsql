from logql_to_logsql.translator import translate_logql_to_logsql


class TestTranslateLogQLToLogsQL:
    def test_returns_string(self):
        result = translate_logql_to_logsql('{job="app"}')
        assert isinstance(result, str)

    def test_basic_translation(self):
        logql = '{job="app"} |= "error"'
        result = translate_logql_to_logsql(logql)
        assert result is not None
