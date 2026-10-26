package main

/*
#include <stdlib.h>
*/
import "C"

func main() {}

//export TranslateLogQLToLogsQL
func TranslateLogQLToLogsQL(query *C.char) *C.char {
	result, err := translateLogQLToLogsQL(C.GoString(query))
	if err != nil {
		return C.CString("")
	}
	return C.CString(result.LogsQL)
}
