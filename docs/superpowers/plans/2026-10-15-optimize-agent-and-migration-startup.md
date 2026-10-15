# Optimize Agent Pod and Migration Job Startup Implementation Plan

## Problem Summary
In production Kubernetes (`homelab-aiops`), both `homelab-aiops-agent` and the `agent-migration` Helm hook Job exhibit severe startup latency and probe failures:
1. **Linux CFS Quota Throttling**: The pod CPU limit is set to `500m` (agent) and `200m` (migration Job). In cgroups v2 (`/sys/fs/cgroup/cpu.stat`), the agent pod registered over **25.5 seconds** of throttled CPU (`throttled_usec: 25,497,404`) during cold startup. Python module parsing, AST construction, Pydantic type registration, and LangGraph compiling are CPU-bound operations that get severely throttled.
2. **Missing Bytecode Pre-compilation**: `packages/agents/lyoko/Dockerfile` does not enable `UV_COMPILE_BYTECODE=1` nor does it run `python -m compileall`. Python must parse and compile raw `.py` files across all 192 packages on container boot under a throttled CPU core.
3. **Eager & Redundant App/Graph Initialization**: `create_app()` at import time compiles a full LangGraph graph and builds chat connectors, which are immediately discarded and recreated with PostgreSQL checkpointer inside `lifespan`.
4. **Eager Alembic/SQLAlchemy Imports & Misconfigured Path**: `memory_repository.py` eagerly imports `alembic.command` and `alembic.config.Config` at module level (~1.85s import penalty), and its default path search points to `.../src/alembic.ini` instead of the actual `alembic.ini` location, logging warnings and wasting cycles.
5. **Migration Job Resource Starvation**: The `agent-migration` Job runs on `requests.cpu: 50m, limits.cpu: 200m` with uncompiled bytecode, causing it to take 30+ seconds to run simple migrations.

---

## Proposed Changes

### 1. `packages/agents/lyoko/Dockerfile`
- Add `ENV UV_COMPILE_BYTECODE=1` to ensure all installed packages in the virtual environment are compiled to `.pyc` during `docker build`.
- Add `--compile-bytecode` flag to `uv sync`.
- Run `RUN python -m compileall -q /app/packages/agents/lyoko/src` to compile application source code.

### 2. `packages/agents/lyoko/src/lyoko/infrastructure/db/memory_repository.py`
- Move `from alembic import command` and `from alembic.config import Config` inside `run_migrations()` method so importing `memory_repository` is instantaneous and does not pull in Alembic, Mako, and SQLAlchemy DDL modules.
- Fix `alembic_ini_path` resolution to look in `Path(__file__).resolve().parents[4] / "alembic.ini"`, `/app/packages/agents/lyoko/alembic.ini`, and `/app/alembic.ini`.

### 3. `packages/agents/lyoko/src/lyoko/infrastructure/api/server.py` & `main.py`
- Avoid redundant LangGraph compilation and `TelegramConnector` initialization in `create_app()`. `create_app()` prepares the FastAPI routes, middlewares, and lightweight managers; `lifespan` handles graph compilation and connector startup.
- Ensure `main.py` logs startup progression clearly.

### 4. Helm Chart (`charts/homelab-aiops/values.yaml` in `k8s-at-home-charts`)
- Update `agent.resources`:
  - `requests.cpu`: `200m` (from `100m`)
  - `requests.memory`: `256Mi`
  - `limits.cpu`: `2000m` (from `500m`, allowing burst CPU during startup)
  - `limits.memory`: `1024Mi` (from `512Mi`)
- Update `agent.startupProbe`:
  - `initialDelaySeconds`: `10` (from `5`, preventing premature unhealthy probe warnings)
  - `periodSeconds`: `5`
  - `failureThreshold`: `30`
- Update `agent.migration.resources`:
  - `requests.cpu`: `200m` (from `50m`)
  - `requests.memory`: `256Mi` (from `64Mi`)
  - `limits.cpu`: `1000m` (from `200m`, allowing fast migration execution)
  - `limits.memory`: `512Mi` (from `256Mi`)

---

## Verification Plan

### Automated Tests
1. Run `uv run pytest` to ensure all 591 tests continue to pass.
2. Run `uv run pytest tests/test_clean_architecture.py` to ensure Clean Architecture boundaries and module size limits are strictly respected.
3. Verify `import lyoko.infrastructure.db.memory_repository` no longer imports `alembic`.
4. Verify `create_app()` startup time reduction via local benchmark.

### Live/Cluster Verification
1. Review git diff on `k8s-at-home-charts` and `homelab-aiops`.
