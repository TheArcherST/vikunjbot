.PHONY: format lint test check test-down

TEST_COMPOSE = docker compose -p vikunjbot-test -f compose.test.yaml

format:
	uv run ruff format .
	uv run ruff check --fix .
	uv run ruff format .

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	@set -e; \
	cleanup() { $(TEST_COMPOSE) down --volumes --remove-orphans; }; \
	trap cleanup EXIT; \
	$(TEST_COMPOSE) --profile test run --build --rm tests

check: lint test

test-down:
	$(TEST_COMPOSE) down --volumes --remove-orphans
