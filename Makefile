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

.PHONY: test
test:
	@$(PYTHONCMD) -m pytest tests/

.PHONY: clean
clean:
	@rm -rf .venv __pycache__ src/*.egg-info
