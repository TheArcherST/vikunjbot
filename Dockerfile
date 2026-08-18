FROM python:3.14-slim AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_SYNC=1

WORKDIR /app

RUN pip install --no-cache-dir uv==0.11.10

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

FROM dependencies AS test

RUN uv sync --frozen --all-groups
COPY tests ./tests

FROM dependencies AS runtime

RUN useradd --system --uid 10001 --create-home appuser

ENV PATH="/app/.venv/bin:$PATH"

USER appuser
