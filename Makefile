.DEFAULT_GOAL := venv

SHELL = /usr/bin/env bash -o pipefail
.SHELLFLAGS = -ec

PYTHONCMD ?= .venv/bin/python
PIPCMD ?= .venv/bin/pip
SRC_ROOT := $(shell git rev-parse --show-toplevel)

.PHONY: venv
venv:
	@python -m venv .venv
	@$(PIPCMD) install -r requirements.txt
	@$(PIPCMD) install -e .

.PHONY: build
build:
	$(eval GO_DIR := $(SRC_ROOT)/src/libs/logql-to-logsql-go)
	$(eval LIBS_OUT := $(SRC_ROOT)/src/logql_to_logsql/libs)
	$(eval SO_NAME := logql_to_logsql_go_$(shell go env GOOS)_$(shell go env GOARCH).so)
	@cd $(GO_DIR) && go mod download
	@cd $(GO_DIR) && CGO_ENABLED=1 go build -buildmode=c-shared -o $(LIBS_OUT)/$(SO_NAME) .
	@rm -f $(LIBS_OUT)/$(basename $(SO_NAME)).h

.PHONY: test
test:
	@$(PYTHONCMD) -m pytest tests/

.PHONY: clean
clean:
	@rm -rf .venv __pycache__ src/*.egg-info
	@rm -f src/logql_to_logsql/libs/*.so src/logql_to_logsql/libs/*.h
