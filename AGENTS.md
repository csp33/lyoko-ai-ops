# AGENTS.md — LYOKO AIOps

Project context, architectural standards, and operating rules for AI coding agents and contributors working in `lyoko-ai-ops`.

---

## 1. Project Overview

`lyoko-ai-ops` is a modular, open-source LYOKO AIOps & autonomous operations platform designed for Kubernetes homelab environments and smart infrastructure.

The platform consists of two primary systems within a Python `uv` monorepo:

1. **`sector5-mcp` (Tool Gateway & Safety Harness)**:
   - Built on **FastMCP** (Model Context Protocol).
   - Serves as a unified tool aggregator providing structured, typed, and RBAC-controlled tools for:
     - Kubernetes inspection and safe patching (pod diagnostics, logs, resource bump, rollout restart).
     - Home Assistant IoT operations (service calls, state inspection).
     - UniFi Network management (client discovery, device status, port management).
     - Telegram alert and interactive reporting.
   - Operates over Streamable HTTP (`/mcp`) and stdio transports, allowing both autonomous in-cluster agents and local developer IDEs (Antigravity, Cursor, Claude) to use the exact same tool suite safely.

2. **`LYOKO` (Autonomous Remediation Agent)**:
   - **L**ive **Y**aml **O**ptimization & **K**8s **O**rchestration.
   - Built with **LangGraph** (StateGraph / Finite State Machine) and **FastAPI**.
   - Event-driven: awakened by Prometheus Alertmanager webhooks for any alert (Kubernetes, network, smart home, observability), and by Telegram messages from the operator.
   - One LangGraph graph, two branches. Every event enters at **Route**: alerts always take the incident branch, and for a Telegram message an LLM decides between the two (unclear or failed ⇒ chat).
     - **Chat**: a **Supervisor** answers the operator and carries out requests by delegating to domain specialists (`ask_kubernetes_specialist`, `ask_unifi_specialist`, `ask_homeassistant_specialist`, `ask_grafana_specialist`).
     - **Incident**, a sequence of four nodes. Diagnose, remediate, and verify are each a supervisor run that delegates to the same specialists:
       1. **Diagnose**: Read-only investigation via specialists; returns a root cause, whether it is fixable with the available tools, and a plan.
       2. **Remediate**: Executes the plan via specialists.
       3. **Verify**: Re-checks via specialists after a stabilization delay.
       4. **Notify**: Builds a structured report from the tool calls the gate actually allowed. Alerts post it to Telegram. For a message, it is the reply.
   - **Domain specialists** receive a scoped upstream toolset from `sector5-mcp`. Each specialist is locked to one domain. Its tool catalog with argument signatures is injected dynamically into its system prompt (`_format_catalog`), and the specialist is equipped with `gateway_call_tool` (guarded execution; a call rejected for its arguments returns the tool's schema inline to self-correct) and `gateway_get_tool_schema` (targeted re-read of one schema as fallback). Discovery via `gateway_get_domain_tools` is omitted to avoid redundant pagination loops. They never search outside their domain.
   - Both branches share one `ToolGate` policy: read-only tools run, `AUTO_APPROVED_TOOLS` run unattended, and everything else waits for approval via Telegram inline buttons. Diagnose and Verify are strictly read-only. Without an approval channel, changes are refused.
   - One event is one Langfuse trace. Agent runs must receive the run config of the graph node that starts them (`parent_config`), so they nest as named child spans instead of starting traces of their own.

---

## 2. Core Rules & Development Guidelines

### Language & Style
- **Strict English Only**: All code, docstrings, comments, commit messages, PR descriptions, test cases, and documentation (`README.md`, `AGENTS.md`, etc.) **must always be written in English**.

### Security & Zero-Leak Policy (Public Repository)
- This repository is **public**. Under no circumstances should secrets, real API keys, bearer tokens, Telegram bot tokens, WireGuard keys, internal production domains, or private network identifiers be committed.
- All secrets must be loaded via environment variables using `.env` (ignored by git) and documented through `.env.example`.
- All Kubernetes operations in `sector5-mcp` must validate inputs using Pydantic models and enforce namespace allowlists / denylists (e.g., blocking mutations in `kube-system` unless explicitly configured).

### Monorepo Structure (`uv` Workspace & Clean Architecture)

```text
lyoko-ai-ops/
├── pyproject.toml              # Root uv workspace configuration
├── uv.lock                     # Shared deterministic dependency lock
├── .env.example                # Documented configuration template
├── packages/
│   ├── mcps/                   # MCP Server packages (Capabilities & Tool Hubs)
│   │   └── sector5-mcp/        # FastMCP Gateway package
│   │       ├── pyproject.toml
│   │       ├── Dockerfile
│   │       └── src/
│   │           └── sector5_mcp/
│   │               ├── domain/
│   │               │   ├── models/     # k8s.py, homeassistant.py, unifi.py, auth.py, guardrail.py
│   │               │   ├── exceptions/ # base.py, auth.py, upstream.py, tool.py, guardrail.py
│   │               │   └── interfaces/ # upstream.py, auth.py
│   │               ├── application/    # MCPGatewayService, GuardrailEngine, use_cases/
│   │               ├── infrastructure/ # FastMCP, upstream clients, Google Auth
│   │               ├── config.py
│   │               └── server.py       # Composition root
│   │
│   └── agents/                 # Autonomous Agent packages (Cognitive Loops)
│       └── lyoko/              # LangGraph Auto-Remediation Agent
│           ├── pyproject.toml
│           ├── Dockerfile
│           └── src/
│               └── lyoko/
│                   ├── domain/
│                   │   ├── models/     # incident.py
│                   │   ├── exceptions/ # base.py, incident.py
│                   │   └── interfaces/ # mcp.py, llm.py
│                   ├── application/    # Feature-bounded packages: incidents/, chat/, workflow/, agents/, memory/, safety/, context/, prompts/
│                   ├── infrastructure/ # FastAPI webhook controller & MCP client
│                   ├── config.py
│                   └── main.py         # Composition root
│
└── tests/                      # Automated test suite
    ├── agents/lyoko/           # Tests for LYOKO agent (LangGraph, webhooks, models)
    └── mcps/sector5_mcp/       # Tests for MCP gateway (routing, auth, guardrails)
```

### Clean Architecture & Layer Boundary Governance (Strict Rule)

Every package in `lyoko-ai-ops` strictly adheres to **Clean Architecture** (Ports & Adapters / Hexagonal Architecture). Boundary crossing rules are strictly enforced:

1. **`domain/` (Pure Entities & Ports)**:
   - Contains pure business logic, domain entity models (`dataclasses`, `Pydantic`), domain exceptions, and abstract interfaces (`ABC` ports).
   - **ZERO external framework or vendor dependencies allowed**: Strictly forbidden to import `langchain`, `langchain_openai`, `langgraph`, `fastapi`, `telegram`, `psycopg`, `langfuse`, `mcp`, or concrete infrastructure adapters.
   - Domain layers must only depend on standard Python libraries or shared domain primitives.

2. **`application/` (Use Cases & Workflow Orchestration)**:
   - Organized into clean, cohesive feature packages (`incidents/`, `chat/`, `workflow/`, `agents/`, `memory/`, `safety/`, `context/`, `prompts/`, `skills/`).
   - Encapsulates discrete application workflows and operations into dedicated **`UseCase` classes** colocated within their bounded context (e.g. `incidents.use_cases.DiagnoseIncidentUseCase`, `incidents.use_cases.RemediateIncidentUseCase`, `incidents.use_cases.VerifyIncidentUseCase`, `incidents.use_cases.NotifyIncidentReportUseCase`, `incidents.use_cases.TriageIncidentUseCase`, `chat.use_cases.ProcessChatMessageUseCase`, `chat.use_cases.HandleChatTurnUseCase`, `workflow.routing.route_event.RouteEventUseCase`, `memory.use_cases.RetrieveMemoryLessonsUseCase`, `memory.use_cases.RecordFeedbackUseCase`).
   - Workflow nodes, web controllers, and chat connectors act as thin callers/adapters that delegate execution to their corresponding `UseCase` instances.
   - Contains cognitive agent definitions (`agents.SupervisorAgent`, `agents.DomainSpecialistAgent`), workflow graph definitions (`workflow.graph.create_lyoko_graph`), safety gates, and HITL managers (`safety.hitl.ApprovalManager`).
   - Coordinates domain models and interacts with external capabilities **exclusively through domain interfaces / ports** (`LLMClientInterface`, `MCPClientInterface`, `ChatConnector`, `AuthVerifierInterface`).
   - **ZERO infrastructure imports allowed**: Strictly forbidden to import concrete infrastructure classes or vendor SDKs (e.g., `ChatOpenAI`, `OpenAILLMAdapter`, `FastMCPClient`, `TelegramConnector`, `AsyncConnectionPool`, `LangfuseTracer`). All external services must be injected into application constructors.

3. **`infrastructure/` (Adapters & External Drivers)**:
   - Implements domain interfaces and encapsulates all vendor SDKs, databases, web servers, and protocols (`FastMCP`, `LangChain`, `OpenAI`, `Langfuse`, `python-telegram-bot`, `psycopg`, `FastAPI`).
   - Translates domain requests into third-party API calls and formats responses into domain models.

4. **Composition Root (`main.py` / `server.py`)**:
   - The only place where concrete infrastructure adapters are instantiated, configuration is bound, and dependencies are injected into application services and workflow graphs.

5. **Empty `__init__.py` Files, No `__all__`, & Explicit Imports (Strict Rule)**:
   - All `__init__.py` files across all packages (`packages/`) must remain **completely empty** (0 bytes / no code, re-exports, or barrel imports).
   - Wildcard exports and `__all__` definitions are strictly forbidden across all modules. All modules must export their symbols naturally, and callers must explicitly import specific symbols from the exact submodule where they are defined.
   - All imports across the codebase must explicitly target the specific submodule where the symbol is defined (e.g., `from lyoko.domain.models.memory import MemoryEntry` instead of `from lyoko.domain.models import MemoryEntry`).

6. **Single Responsibility & Anti-God-File Policy (Strict Rule)**:
   - All modules across `domain/` and `application/` must strictly maintain Single Responsibility and stay under **250-280 lines**.
   - Never bundle distinct concerns in a single service file (e.g., domain alias dictionaries, relevance scoring heuristics, catalog caching, and tool execution routing must be separated into `domain/models/aliases.py`, `application/scoring.py`, `application/registry.py`, and `application/service.py`).
   - Graph workflows (`workflow.py`) must separate StateGraph wiring and topology from discrete node implementations (modularized under `application/nodes/*.py`).

Automated AST architectural tests (`tests/test_clean_architecture.py`) run in CI to permanently prevent layer leakage, enforce empty `__init__.py` files, forbid `__all__`, and enforce module size ceilings.

### Prompt Engineering & System Prompt Standards
- **Declarative, Concise & Modular**: System prompts (stored under `packages/agents/lyoko/src/lyoko/application/prompts/*.md`) must define high-level roles, operational constraints, safety policies, and output formatting cleanly.
- **No Overfitted Examples or Ad-hoc Hacks**: Strictly avoid hardcoding hyper-specific micro-examples or capitalized shouting (`ALWAYS`) in system prompts. Dynamic runtime context, taught operator rules, and semantic memories must be injected via runtime context interpolation rather than polluting static system prompts.

---

## 3. Tooling & Quality Standards

- **Python Version**: Python 3.13+
- **Package Manager**: `uv` (`uv sync`, `uv run`, `uv add`)
- **Dependency Version Pinning**: All dependencies across all workspace `pyproject.toml` files **must always have an explicit major version ceiling** (e.g., `"langchain>=1.4.3,<2.0.0"`, `"pydantic>=2.13.0,<3.0.0"`). Unbounded dependencies (such as `"pkg>=1.0.0"` without `<2.0.0`) are strictly forbidden to prevent accidental breaking changes during dependency resolution.
- **Linter & Formatter**: `ruff` (`ruff check .`, `ruff format .`)
- **Testing**: `pytest` with `pytest-asyncio` for asynchronous tool and graph execution
- **Type Checking**: Full type annotations required on all public interfaces and Pydantic schemas.

---

## 4. GitOps Compatibility Notice

When `LYOKO` operates against Kubernetes clusters managed by GitOps controllers (such as Argo CD or Flux):
- Live `kubectl patch` modifications to Deployment specs can cause GitOps drift.
- Prefer non-conflicting mutation techniques (e.g., mutating annotations or scaling) or generating Git PRs against the source charts repository for permanent config changes.
