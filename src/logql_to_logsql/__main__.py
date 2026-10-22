from logql_to_logsql.translator import translate_logql_to_logsql


def main():
    result = translate_logql_to_logsql('{job="app"} |= "error"')
    print(result)


if __name__ == "__main__":
    main()
