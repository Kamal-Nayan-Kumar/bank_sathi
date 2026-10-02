# Card Sathi — local commands.
#
# Targets are ordered so a newcomer can run them top to bottom and end up with
# a working system. `make setup` -> `make data` -> `make dev`.

PY := .venv/bin/python
PIP := uv pip
VENV := .venv

.DEFAULT_GOAL := help
.PHONY: help setup data data-force test lint dev api web clean eval check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv, install the backend, install the frontend
	@test -d $(VENV) || uv venv --python 3.11
	$(PIP) install -e ".[dev]"
	cd frontend && npm install
	@echo "Done. Next: make data"

data: ## Generate the catalogue, profiles and ground truth, then ingest
	$(PY) scripts/build_data.py --cards 120 --profiles 300

data-force: ## Rebuild every generated artefact from the seed
	$(PY) scripts/build_data.py --cards 120 --profiles 300 --force

test: ## Run the test suite
	$(PY) -m pytest

test-fast: ## Run the suite, stopping at the first failure
	$(PY) -m pytest -x --tb=short

lint: ## Check formatting and common mistakes
	$(PY) -m ruff check backend scripts evaluation

check: lint test ## Lint and test

eval: ## Run the evaluation harness and write evaluation/results.md
	$(PY) evaluation/run_eval.py --limit 40

eval-fast: ## Evaluation without any API calls, for CI
	$(PY) evaluation/run_eval.py --limit 25 --skip-llm

dev: ## Run the API and the frontend together (Ctrl-C stops both)
	@echo "API      http://localhost:8000"
	@echo "Frontend http://localhost:5173"
	@trap 'kill 0' EXIT INT TERM; \
		$(MAKE) api & \
		$(MAKE) web & \
		wait

api: ## Run the API only
	$(PY) -m uvicorn app.main:app --reload --port 8000 --app-dir backend

web: ## Run the frontend only
	cd frontend && npm run dev

build-web: ## Build the frontend for production
	cd frontend && npm run build

clean: ## Remove caches and the local SQLite database
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	rm -rf .ruff_cache frontend/dist
	@echo "Note: data/card_sathi.sqlite3 left alone; delete it by hand to reset."
