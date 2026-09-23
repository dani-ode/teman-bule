SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
UV := uv
COMPOSE := docker compose --env-file .env --env-file .env.deploy

.PHONY: help lock sync check lint typecheck test audit compose-check infra-up down build migrate up
help:
	@printf '%s\n' 'Preparation: lock sync check audit compose-check infra-up down' 'Foundation only: lint typecheck test build migrate up' 'Read .blueprint/toolchain.md before running live integrations.'
lock:
	$(UV) pip compile requirements.txt --python-version 3.12 --generate-hashes --quiet -o requirements.lock
	$(UV) pip compile requirements-dev.txt --python-version 3.12 --constraint requirements.lock --generate-hashes --quiet -o requirements-dev.lock
sync:
	$(UV) venv --python 3.12 --allow-existing .venv
	$(UV) pip sync --python .venv/bin/python --require-hashes requirements-dev.lock
check:
	$(UV) pip check --python .venv/bin/python
	.venv/bin/yamllint -c .yamllint.yaml compose.yaml .github/workflows/quality.yml
	$(MAKE) --dry-run help >/dev/null
lint:
	test -d src
	.venv/bin/ruff check src tests
typecheck:
	test -d src
	.venv/bin/mypy src
test:
	test -d tests
	.venv/bin/pytest -m 'not live'
audit:
	.venv/bin/pip-audit -r requirements.lock --require-hashes --disable-pip
compose-check:
	$(COMPOSE) config --quiet
infra-up: compose-check
	$(COMPOSE) up -d --wait postgres redis
down:
	$(COMPOSE) down
build: compose-check
	test -f src/temanbule/api/main.py
	test -f alembic.ini
	test -d migrations
	$(COMPOSE) --profile application build
migrate:
	$(COMPOSE) --profile maintenance run --rm migrate
up:
	$(COMPOSE) --profile application up -d
