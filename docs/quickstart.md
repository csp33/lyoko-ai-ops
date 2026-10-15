# Quick Start Guide

This guide walks you through setting up and running `lyoko-ai-ops`.

---

## Prerequisites

- **[Docker](https://docs.docker.com/get-docker/) & Docker Compose**: Recommended for running the complete stack (PostgreSQL + pgvector, sector5-mcp, LYOKO).
- **[Python 3.13+](https://www.python.org/downloads/) & [`uv`](https://docs.astral.sh/uv/)**: Required only if developing or running locally without Docker.
- **OpenAI API Key**: For LLM reasoning and dense vector embeddings (`text-embedding-3-small`).
- **`kubectl`**: Configured with a valid cluster context (or in-cluster `ServiceAccount`).

---

## Method 1: Docker Compose (Recommended)

The easiest way to run the full platform is with Docker Compose. This automatically spins up PostgreSQL (with pgvector), the `sector5-mcp` gateway, and the `lyoko` remediation agent.

### 1. Clone & Configure

```bash
# Clone the repository
git clone https://github.com/csp33/lyoko-ai-ops.git
cd lyoko-ai-ops

# Create and configure environment file
cp .env.example .env
```

Edit `.env` to supply at minimum your `OPENAI_API_KEY`:

```ini
OPENAI_API_KEY=sk-your-openai-api-key-here
OPENAI_MODEL=gpt-4o-mini
OPENAI_DIAGNOSE_MODEL=gpt-4.1
OPENAI_USE_RESPONSES_API=true
```

### 2. Start the Stack

```bash
# Build and launch all services in the background
docker compose up -d
```

This launches:
- **`postgres`**: PostgreSQL database with `pgvector` extension on port `5432`.
- **`sector5-mcp`**: Tool Gateway on `http://localhost:8000/mcp`.
- **`lyoko`**: Autonomous Remediation Agent on `http://localhost:9000`.

### 3. Verify Health

```bash
# Check LYOKO agent health endpoint
curl http://localhost:9000/health
# Response: {"status":"ok"}

# View real-time logs
docker compose logs -f
```

To stop the stack:
```bash
docker compose down
```

---

## Method 2: Local Development (Python & `uv`)

For local code development and debugging:

### 1. Install Dependencies

```bash
# Deterministically synchronize workspace virtual environment
uv sync

# Configure your environment
cp .env.example .env
```

### 2. Start PostgreSQL

Ensure PostgreSQL with `pgvector` is running locally (e.g., via `docker compose up -d postgres`).

### 3. Run Services

In separate terminal windows:

```bash
# Terminal 1: Tool Gateway
uv run --package sector5-mcp python -m sector5_mcp.server

# Terminal 2: Remediation Agent
uv run --package lyoko python -m lyoko.main
```

### 4. Run Test Suite & Quality Checks

```bash
# Run pytest test suite across all workspace packages
uv run pytest

# Run Ruff linter and formatter checks
uv run ruff check .
```

---

## Configuration Reference

Key environment variables in `.env`:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `OPENAI_API_KEY` | `""` | OpenAI API key for LLM diagnosis and embeddings. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Cheaper model for chat, remediation, verification, and domain specialists. |
| `OPENAI_DIAGNOSE_MODEL` | `gpt-4.1` | Stronger model for the diagnose phase (synthesis + go/no-go). Empty falls back to `OPENAI_MODEL`. |
| `OPENAI_USE_RESPONSES_API` | `true` | Use the OpenAI Responses API (`/v1/responses`) instead of Chat Completions. |
| `MCP_HOST` / `MCP_PORT` | `0.0.0.0` / `8000` | Gateway listening interface and HTTP port. |
| `LYOKO_HOST` / `LYOKO_PORT` | `0.0.0.0` / `9000` | Agent webhook receiver host and port. |
| `POSTGRES_HOST` / `POSTGRES_DB` | `localhost` / `lyoko` | PostgreSQL host and database name. |
| `TELEGRAM_ENABLED` | `false` | Enable Telegram operator assistant and HITL approvals. |
| `TELEGRAM_BOT_TOKEN` | `""` | Telegram Bot token from `@BotFather`. |
| `HASS_ENABLED` / `HASS_URL` | `true` / `""` | Home Assistant upstream MCP configuration. |
| `UNIFI_ENABLED` / `UNIFI_URL` | `true` / `""` | UniFi Network controller upstream MCP configuration. |
| `GRAFANA_ENABLED` / `GRAFANA_URL` | `true` / `""` | Grafana / Prometheus upstream MCP configuration. |
| `GITHUB_ENABLED` | `true` | GitHub MCP provider for GitOps PR automation. |

---

## Related Guides

- [System Architecture & Incident Lifecycle](architecture.md)
- [Security & Guardrails](security-guardrails.md)
- [`sector5-mcp` Gateway Documentation](../packages/mcps/sector5-mcp/README.md)
- [`lyoko` Agent Documentation](../packages/agents/lyoko/README.md)
