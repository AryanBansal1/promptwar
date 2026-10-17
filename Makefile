# Saathi developer commands. On Windows use Git Bash, or run the commands directly.
PY ?= python

.PHONY: install run test lint format typecheck audit check

install:
	$(PY) -m pip install -r requirements-dev.txt

run:
	$(PY) -m uvicorn app.main:create_app --factory --reload --reload-include "*.js" --reload-include "*.css" --reload-include "*.html" --reload-include "*.json" --port 8080

test:
	$(PY) -m ruff check app tests
	$(PY) -m mypy app
	$(PY) -m pytest --cov --cov-report=term --cov-fail-under=85

lint:
	$(PY) -m ruff check app tests
	$(PY) -m ruff format --check app tests

format:
	$(PY) -m ruff format app tests
	$(PY) -m ruff check --fix app tests

typecheck:
	$(PY) -m mypy app

audit:
	$(PY) -m pip_audit -r requirements.txt

check: lint typecheck test audit
