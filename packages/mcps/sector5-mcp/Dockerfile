FROM python:3.13-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

# Install kubectl binary
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL -o /usr/local/bin/kubectl "https://dl.k8s.io/release/v1.31.0/bin/linux/${ARCH}/kubectl" \
    && chmod +x /usr/local/bin/kubectl

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy root workspace and manifests + lockfile
COPY pyproject.toml uv.lock README.md ./
COPY packages/mcps/sector5-mcp/pyproject.toml packages/mcps/sector5-mcp/README.md packages/mcps/sector5-mcp/
COPY packages/agents/lyoko/pyproject.toml packages/agents/lyoko/README.md packages/agents/lyoko/

# Install dependencies into virtual environment
RUN uv sync --package sector5-mcp --no-dev --no-install-workspace --frozen

# Copy source code
COPY packages/mcps/sector5-mcp/src/ packages/mcps/sector5-mcp/src/

# Install package into venv
RUN uv sync --package sector5-mcp --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["python", "-m", "sector5_mcp.server"]
