import pytest
from logql_to_logsql.translate import translate_logql_to_logsql

CASES = [
    pytest.param(
        '{collector="otel-collector"}',
        '{collector="otel-collector"}',
        id="basic_selector",
    ),
    pytest.param(
        '{collector="otel-collector"} |= "POST"',
        '{collector="otel-collector"} "POST"',
        id="line_contains",
    ),
    pytest.param(
        '{collector="otel-collector"} != "GET"',
        '{collector="otel-collector"} -"GET"',
        id="line_not_contains",
    ),
    pytest.param(
        '{collector="otel-collector"} |~ "GET|POST"',
        '{collector="otel-collector"} ~"GET|POST"',
        id="line_regexp",
    ),
    pytest.param(
        '{collector="otel-collector"} | products > 5',
        '{collector="otel-collector"} products:>5',
        id="label_filter",
    ),
    pytest.param(
        '{collector="otel-collector"} | json | trace_id!=""',
        '{collector="otel-collector"} | unpack_json | filter -trace_id:=""',
        id="json_and_label",
    ),
    pytest.param(
        '{collector="otel-collector"} | json duration="duration"',
        '{collector="otel-collector"} | unpack_json fields (duration)',
        id="json_extract_field",
    ),
    pytest.param(
        '{collector="otel-collector"} | json duration_ms="duration"',
        '{collector="otel-collector"} | unpack_json fields (duration) | format "<duration>" as duration_ms | delete duration',
        id="json_extract_rename",
    ),
    pytest.param(
        '{collector="otel-collector"} | logfmt | products >= 10',
        '{collector="otel-collector"} | unpack_logfmt | filter products:>=10',
        id="logfmt_and_label",
    ),
    pytest.param(
        '{collector="otel-collector"} | logfmt status="status"',
        '{collector="otel-collector"} | unpack_logfmt fields (status)',
        id="logfmt_extract_field",
    ),
    pytest.param(
        '{collector="otel-collector"} | logfmt status_code="status"',
        '{collector="otel-collector"} | unpack_logfmt fields (status) | format "<status>" as status_code | delete status',
        id="logfmt_extract_rename",
    ),
    pytest.param(
        '{collector="otel-collector"} | drop span_id, trace_id',
        '{collector="otel-collector"} | delete span_id, trace_id',
        id="drop_labels",
    ),
    pytest.param(
        '{collector="otel-collector"} | drop trace_id=~"^abc"',
        '{collector="otel-collector"} | format if (trace_id:~"^abc") "" as trace_id',
        id="drop_labels_conditional",
    ),
    pytest.param(
        'rate({collector="otel-collector"}[5m])',
        '{collector="otel-collector"} _time:5m | stats by (_stream) rate() as value',
        id="rate",
    ),
    pytest.param(
        'count_over_time({collector="otel-collector"}[5m])',
        '{collector="otel-collector"} _time:5m | stats by (_stream) count() as value',
        id="count_over_time",
    ),
    pytest.param(
        'sum(rate({collector="otel-collector"}[5m]))',
        '{collector="otel-collector"} _time:5m | stats rate() as value',
        id="sum_rate",
    ),
    pytest.param(
        'topk(5, sum by (severity) (rate({collector="otel-collector"}[5m])))',
        '{collector="otel-collector"} _time:5m | stats by (severity) rate() as value | first 5 (value desc)',
        id="topk_rate_by_label",
    ),
    pytest.param(
        '{app="nginx"} |= "error"',
        '{app="nginx"} "error"',
        id="line_contains_nginx",
    ),
    pytest.param(
        '|= "error"',
        '{} "error"',
        id="no_stream_selector",
    ),
    pytest.param(
        '{app="nginx"} | json | trace_id="abcdef"',
        '{app="nginx"} | unpack_json | filter trace_id:=abcdef',
        id="json_and_label_eq",
    ),
    pytest.param(
        '{app="nginx"} | drop foo=~"bar"',
        '{app="nginx"} | format if (foo:~"bar") "" as foo',
        id="drop_labels_conditional_nginx",
    ),
    pytest.param(
        '{app="nginx"} | keep foo=~"bar"',
        '{app="nginx"} | format if (foo:~"bar") "<foo>" as foo',
        id="keep_labels_conditional",
    ),
    pytest.param(
        '{app="nginx"} | json duration="duration"',
        '{app="nginx"} | unpack_json fields (duration)',
        id="json_extract_field_nginx",
    ),
    pytest.param(
        '{app="nginx"} | json latency="duration"',
        '{app="nginx"} | unpack_json fields (duration) | format "<duration>" as latency | delete duration',
        id="json_extract_rename_nginx",
    ),
    pytest.param(
        '{app="nginx"} | logfmt duration="duration"',
        '{app="nginx"} | unpack_logfmt fields (duration)',
        id="logfmt_extract_field_nginx",
    ),
    pytest.param(
        'rate({app="nginx"}[5m])',
        '{app="nginx"} _time:5m | stats by (_stream) rate() as value',
        id="rate_nginx",
    ),
    pytest.param(
        'sum(rate({app="nginx"}[5m]))',
        '{app="nginx"} _time:5m | stats rate() as value',
        id="sum_rate_nginx",
    ),
    pytest.param(
        'count_over_time({app="nginx"}[5m])',
        '{app="nginx"} _time:5m | stats by (_stream) count() as value',
        id="count_over_time_nginx",
    ),
    pytest.param(
        'sum(count_over_time({app="nginx"}[5m]))',
        '{app="nginx"} _time:5m | stats count() as value',
        id="sum_count_over_time",
    ),
    pytest.param(
        'sum by (severity) (rate({app="nginx"}[5m]))',
        '{app="nginx"} _time:5m | stats by (severity) rate() as value',
        id="sum_rate_by_label",
    ),
    pytest.param(
        'topk(5, sum by (severity) (rate({app="nginx"}[5m])))',
        '{app="nginx"} _time:5m | stats by (severity) rate() as value | first 5 (value desc)',
        id="topk_rate_by_label_nginx",
    ),
    pytest.param(
        'rate({app="nginx"}[5m] offset 1h)',
        '{app="nginx"} _time:5m offset 1h | stats by (_stream) rate() as value',
        id="rate_with_offset",
    ),
    pytest.param(
        'avg_over_time({app="nginx"} | unwrap duration [5m])',
        '{app="nginx"} _time:5m | stats by (_stream) avg(duration) as value',
        id="avg_over_time_unwrap",
    ),
]


class TestTranslateLogQLToLogsQL:
    @pytest.mark.parametrize("logql,expected", CASES)
    def test_translation(self, logql, expected):
        result = translate_logql_to_logsql(logql)
        assert result.error == ""
        assert result.logsql == expected
