package logql_to_logsql_go

type QueryKind string

const (
	QueryKindLogs  QueryKind = "logs"
	QueryKindStats QueryKind = "stats"
)

type QueryInfo struct {
	Kind   QueryKind
	LogsQL string
}
