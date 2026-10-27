# logql-to-logsql

A Python library that converts Loki's LogQL to VictoriaLogs's LogsQL.

## Overview

This library is based on the official [VictoriaLogs documentation for converting Loki queries to VictoriaLogs queries](https://docs.victoriametrics.com/victorialogs/logql-to-logsql/index.html).
It was inspired by the [example Go implementation](https://github.com/VictoriaMetrics-Community/logql-to-logsql/blob/main/lib/logsql) provided by the VictoriaMetrics team.
It supports a subset of the LogsQL language.

## Installation

```bash
pip install logql-to-logsql
```

## Usage

```python
from logql_to_logsql.translate import TranslateError, TranslateResult, translate_logql_to_logsql


def main():
    try:
        result: TranslateResult = translate_logql_to_logsql('{app="nginx"} |= "error"')
        print(result.logsql)  # {app="nginx"} "error"
        print(result.kind)    # logs
    except TranslateError as e:
        print(f"translation failed: {e}")


if __name__ == '__main__':
    main()
```

## Supported Features

### Query Types

- Log queries
- Stats queries

### Pipeline Stages

- `|=`, `!=`, `|~`, `!~`
- `| json`, `| logfmt`, `| regexp`, `| pattern`
- `| drop`, `| keep`
- `| line_format`, `| label_format`

### Metric Functions

- `rate`, `count_over_time`
- `sum`, `topk`, `bottomk`
- `avg_over_time`, `min_over_time`, `max_over_time`
- `quantile_over_time`
