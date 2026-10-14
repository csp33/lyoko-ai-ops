FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy root workspace and manifests + lockfile
COPY pyproject.toml uv.lock README.md ./
COPY packages/mcps/sector5-mcp/pyproject.toml packages/mcps/sector5-mcp/README.md packages/mcps/sector5-mcp/
COPY packages/agents/lyoko/pyproject.toml packages/agents/lyoko/README.md packages/agents/lyoko/

# Install dependencies into virtual environment
RUN uv sync --package lyoko --no-dev --no-install-workspace --frozen

# Copy source code, migrations, and alembic configuration
COPY packages/agents/lyoko/alembic.ini packages/agents/lyoko/alembic.ini
COPY packages/agents/lyoko/migrations/ packages/agents/lyoko/migrations/
COPY packages/agents/lyoko/src/ packages/agents/lyoko/src/
RUN ln -sf /app/packages/agents/lyoko/alembic.ini /app/alembic.ini

# Install package into venv
RUN uv sync --package lyoko --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 9000

CMD ["python", "-m", "lyoko.main"]
