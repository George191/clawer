.PHONY: install test test-unit test-integration test-cov lint lint-fix format format-check type-check check clean \
        run run-crawl run-% \
        etl-rds etl-ods etl-task etl-dwd etl-dws etl-dim etl-all web \
        test-etl test-web \
        docker-up docker-down docker-build docker-logs docker-shell docker-restart help

help:  ## Show this help message
	@echo "AI Collector — Available commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Install ────────────────────────────────────────────────────────────────

install:  ## Install all dependencies (runtime + dev)
	pip install -r requirements.txt

install-dev:  ## Install dev dependencies
	pip install -r requirements.txt
	pip install pytest pytest-asyncio pytest-cov ruff mypy pre-commit

# ── Testing ────────────────────────────────────────────────────────────────

test:  ## Run all tests (unit + quality)
	pytest tests/ -v

test-unit:  ## Run unit tests only (no integration)
	pytest tests/ -v -m "not integration"

test-integration:  ## Run integration tests (requires mock server)
	pytest tests/ -v -m integration

test-cov:  ## Run tests with coverage report (HTML + terminal)
	pytest tests/ --cov=app --cov-report=term --cov-report=html:htmlcov --cov-fail-under=80

test-cov-ci:  ## CI coverage (XML output for Codecov)
	pytest tests/ --cov=app --cov-report=xml --cov-fail-under=80

# ── Linting & Formatting ─────────────────────────────────────────────────

lint:  ## Run ruff linter
	ruff check app/ tests/

lint-fix:  ## Auto-fix lint issues
	ruff check --fix app/ tests/

format:  ## Format code with ruff
	ruff format app/ tests/

format-check:  ## Check formatting without modifying (CI)
	ruff format --check app/ tests/

type-check:  ## Run mypy type checking
	mypy app/ --ignore-missing-imports --no-strict-optional

check: lint format-check type-check test-unit  ## Run all checks locally

ci: clean lint format-check type-check test-cov-ci  ## Full CI pipeline (before push)

# ── Pre-commit ────────────────────────────────────────────────────────────

pre-commit-install:  ## Install pre-commit hooks
	pre-commit install

pre-commit-run:  ## Run all pre-commit hooks on all files
	pre-commit run --all-files

# ── Run ───────────────────────────────────────────────────────────────────

run:  ## Start FastAPI dev server
	uvicorn app.web.main:app --host 0.0.0.0 --port 8000 --reload

run-crawl:  ## Run crawler (all templates)
	python -m app.main

run-%:  ## Run a specific template: make run-google_patent
	python -m app.main $(subst run-,,$@)

# ── Docker ────────────────────────────────────────────────────────────────

docker-up:  ## Start all services (docker compose up -d)
	docker compose up -d

docker-down:  ## Stop all services and remove containers
	docker compose down

docker-build:  ## Build app Docker image
	docker compose build app

docker-logs:  ## Tail all Docker logs
	docker compose logs -f

docker-logs-app:  ## Tail app container logs
	docker compose logs -f app

docker-shell:  ## Open shell inside app container
	docker compose exec app /bin/bash

docker-restart: docker-down docker-up  ## Full restart (down + up)

# ── Helpers ──────────────────────────────────────────────────────────────

clean:  ## Remove cache/bytecode/temp files
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type d -name "htmlcov" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".ruff_cache" -exec rm -rf {} +
	rm -rf .coverage coverage.xml

# ── ETL Workers ──────────────────────────────────────────────────────────

etl-rds:  ## Run ETL RDS worker (Raw Data Store)
	python -m app.etl.main --layer rds

etl-ods:  ## Run ETL ODS worker (Operational Data Store)
	python -m app.etl.main --layer ods

etl-task:  ## Run ETL TASK worker (Task Scheduling)
	python -m app.etl.main --layer task

etl-dwd:  ## Run ETL DWD worker (Data Warehouse Detail)
	python -m app.etl.main --layer dwd

etl-dws:  ## Run ETL DWS worker (Data Warehouse Summary)
	python -m app.etl.main --layer dws

etl-dim:  ## Run ETL DIM worker (Dimension)
	python -m app.etl.main --layer dim

etl-all:  ## Run all ETL layers
	python -m app.etl.main --layer all

web:  ## Start FastAPI web panel
	uvicorn app.web.main:app --host 0.0.0.0 --port 8000 --reload

# ── Testing ─────────────────────────────────────────────────────────────

test-etl:  ## Run ETL pipeline tests
	python -m pytest tests/test_etl_topic_dispatch.py tests/test_ts_dwd_split.py -v

test-web:  ## Run web API tests
	python -m pytest tests/test_web_api.py -v

shell:  ## Open Python shell with project context
	python -c "import app; print('AI Collector — app loaded')"