# Hermes Agent Harness Integration & Cost Guardrails Design

## 1. Problem Statement & Motivation

Currently, LYOKO's cognitive execution is driven by a procedural ReAct loop (`ReActHarnessRunner`) and rigid LangGraph state machines. While this guarantees structured phase transitions for incidents, it introduces several operational limitations in a real-world homelab environment:

1. **Repetitive Exploratory Cost**: Every new firing alert starts with a clean slate. Even for known recurring issues (e.g., recurring PVC locks or transient CrashLoopBackOffs), the agent executes redundant tool discovery and diagnosis calls, incurring repetitive token expenses on OpenAI.
2. **Lack of Long-Term Learning**: The agent does not synthesize successful remediation patterns into self-improving operational skills.
3. **Rigid Delegation vs. Adaptive Execution**: When investigating complex cross-domain failures (e.g., UniFi network degradation affecting Home Assistant and K8s API connectivity), 1-level specialist handoffs struggle compared to autonomous reasoning loops.
4. **Unbounded Spend Risk**: Without an explicit hard-dollar budget ceiling per execution, runaway tool loops or large tool payloads can lead to unexpected API costs.

By integrating the real **Hermes Agent** (`NousResearch/hermes-agent` v0.19.0) as LYOKO's cognitive engine/brain while executing in an isolated virtual environment (`.venv-hermes`), LYOKO gains adaptive reasoning and native tool dispatching without dependency conflicts (`openai 2.x` vs `3.x`). Critically, all tool executions remain strictly intercepted by LYOKO's **`ToolGate`**, guaranteeing human-in-the-loop (HITL) approval via Telegram for mutating actions, enforcing **hard cost budget caps**, and keeping `sector5-mcp` decoupled from Telegram.

---

## 2. Architectural Objectives

1. **Real Hermes Agent Execution (No Reinventing the Wheel)**:
   - Execute genuine `run_agent.AIAgent` and `tools.registry.registry` from `hermes-agent` rather than a simulated ReAct loop.
   - Run in an isolated virtual environment (`.venv-hermes`) to cleanly separate `hermes-agent` dependencies from LYOKO's core workspace.
2. **Strict Cost & Budget Ceilings**:
   - Explicit `max_turns` per operational phase: Diagnose (4), Remediate (3), Chat (5).
   - Hard budget cap per event (default: `$0.10 USD`). Execution halts gracefully when the ceiling is reached, preventing runaway costs.
   - Cycle detection to immediately terminate repetitive identical tool invocations.
   - Large output buffering (`SmartOutputBufferService`) to truncate payloads >50 lines to head+tail (25+25) and offload dumps to `.lyoko/scratchpad/`.
3. **Guaranteed Safety & HITL (Zero-Trust ToolGate Interception)**:
   - All state-mutating tool calls (e.g., Kubernetes patches, restarts, Home Assistant service calls) are strictly routed through LYOKO's `ToolGate`.
   - Mutating calls are suspended and dispatch interactive Telegram approval buttons (`[✅ Approve]` / `[❌ Deny]`). The harness cannot bypass human confirmation.
   - `sector5-mcp` remains completely decoupled from Telegram and approval concerns.
4. **Hexagonal Clean Architecture Compliance**:
   - Domain layer defines pure budget models and ports (`BudgetConfig`, `BudgetUsage`, `HarnessRunnerInterface`).
   - Application layer provides safety gates (`BudgetGuardService`, `ToolGate`).
   - Infrastructure layer implements the concrete runner (`HermesHarnessRunner`), subprocess bridge (`HermesSubprocessBridge`), pricing helpers (`hermes_pricing`), and isolated worker (`hermes_worker.py`).
   - Strict adherence to project rules: empty `__init__.py`, no wildcard imports, module line count <= 280 lines.
5. **Observability & Trace Continuity**:
   - Single Langfuse trace per event; each agent run (and each tool executed through the gate) remains a child span of the graph node that started it.
   - Real-time status reporting via `on_status` callbacks to drive Telegram draft heartbeats.
   - The LLM calls Hermes makes inside the subprocess are not yet traced (see Known Limitations).

---

## 3. System Architecture & Components

```
Alertmanager Webhook / Telegram Operator Message
                       │
                       ▼
            [ LYOKO Application Entry ]
                       │
                       ▼
           [ ToolGate & BudgetGuardService ]
                       │
                       ▼
           [ HermesHarnessRunner (Infrastructure Adapter) ]
                       │
                       ▼
           [ HermesSubprocessBridge (IPC Controller) ]
                       │ (stdin / stdout JSON-lines)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│ .venv-hermes Subprocess (hermes_worker.py)                   │
│                                                             │
│ 1. Initializes real NousResearch AIAgent                    │
│ 2. Registers dynamic tool schemas into tools.registry       │
│ 3. Executes cognitive reasoning loop                        │
│ 4. Tool Call Dispatch:                                      │
│    - Emits {"type": "call_tool", "tool_name", "args"}       │
│    - Waits for response from host                           │
└─────────────────────────────────────────────────────────────┘
                       │
                       │ (tool request via IPC)
                       ▼
           [ ToolGate Evaluation in LYOKO ]
          ┌───────────────────────────────────┐
          │ - Read-only? -> Allow             │
          │ - Mutating?  -> Telegram HITL     │
          │   [✅ Approve] [❌ Deny]          │
          │ - Approved?  -> sector5-mcp call  │
          │ - Denied?    -> Return error      │
          └───────────────────────────────────┘
                       │
                       │ (tool result via IPC)
                       ▼
           [ SmartOutputBufferService ]
          (Truncate >50 lines to 25+25 & scratchpad)
                       │
                       ▼
           [ Returns to hermes_worker.py ]
                       │
                       ▼
           [ Final Response -> Langfuse & Telegram ]
```

---

## 4. Component Details & Clean Architecture Mapping

### 4.1 Domain Layer (`packages/agents/lyoko/src/lyoko/domain/`)
- `domain/models/budget.py`:
  - `BudgetConfig`: Holds `max_turns_diagnose: int = 4`, `max_turns_remediate: int = 3`, `max_turns_chat: int = 5`, `max_budget_usd: float = 0.10`.
  - `BudgetUsage`: Tracks `input_tokens: int`, `output_tokens: int`, `accumulated_cost_usd: float`, `turns_used: int`.
  - `BudgetTripReason`: Enum (`MAX_TURNS_EXCEEDED`, `MAX_BUDGET_EXCEEDED`, `CYCLE_DETECTED`).
- `domain/interfaces/harness.py`:
  - Utilizes existing `HarnessRunnerInterface` and `AgentHarnessInterface`.

### 4.2 Application Layer (`packages/agents/lyoko/src/lyoko/application/`)
- `application/safety/budget_guard.py`:
  - `BudgetGuardService`: Evaluates whether a new iteration can proceed. Formats graceful fallback instructions when a ceiling trips so the agent summarizes collected diagnostics instead of crashing.
- `application/safety/tool_gate.py`:
  - `ToolGate`: Authorizes tool calls, consults read-only lookups, triggers Telegram approval requests for mutating tools, and waits for human confirmation.
- `application/skills/`:
  - Uses `SkillMatcherService` to inject relevant operational runbooks into the initial prompt.

### 4.3 Infrastructure Layer (`packages/agents/lyoko/src/lyoko/infrastructure/`)
- `infrastructure/llm/hermes_worker.py`:
  - Standalone worker script executed with `.venv-hermes/bin/python`.
  - Imports official `run_agent.AIAgent` and `tools.registry.registry`.
  - Dynamically registers tool definitions with handlers that send JSON tool requests over stdout and await results on stdin.
- `infrastructure/llm/hermes_bridge.py`:
  - `HermesSubprocessBridge`: Manages the lifecycle and IPC with `hermes_worker.py`.
  - Streams live status messages (`on_status`) to drive Telegram draft updates.
  - Passes tool calls to the host tool execution callback.
  - Enforces graceful termination and cleanup on exit or cancellation.
- `infrastructure/llm/hermes_pricing.py`:
  - Utilities for OpenAI token cost estimation (`estimate_cost_usd`), usage extraction (`extract_usage`), status formatting (`format_tool_status`), and error detection (`is_tool_error`).
- `infrastructure/llm/hermes_runner.py`:
  - `HermesHarnessRunner`: Implements `HarnessRunnerInterface`.
  - Delegates real executions to `HermesSubprocessBridge`, with seamless fallback to an in-process bounded loop when running under unit tests with mocks.
  - Integrates `SmartOutputBufferService` for payload truncation.
- `composition.py` (`build_llm_adapter`):
  - Selects `HermesHarnessRunner` when `settings.lyoko_orchestrator == "hermes"`, otherwise `ReActHarnessRunner`, and injects it into `OpenAILLMAdapter`. The LangGraph graph, supervisor, specialists, `ToolGate`, and Telegram wiring are unchanged; only the tool-use loop inside each agent run is swapped.
- Tool interception path: `SupervisedAgentRunner` builds the tools with `mcp_client.get_langchain_tools(authorizer=gate.authorize)`. When Hermes requests a tool over IPC, the runner invokes that same LangChain tool, so `ToolGate.authorize()` runs before any call reaches `sector5-mcp`.

---

## 5. Configuration & Defaults (`lyoko/config.py`)

New environment variables in `.env.example` and `config.py` (settings have no env prefix; `LYOKO_ORCHESTRATOR` is the field name itself, and `ORCHESTRATOR` is accepted as an alias):

```bash
# Agent Orchestrator & Execution Limits
LYOKO_ORCHESTRATOR=hermes            # "hermes" (Hermes Agent in .venv-hermes) | "langgraph" (LangGraph ReAct)
HERMES_VENV_PATH=.venv-hermes        # Isolated hermes-agent virtualenv (alias: HERMES_PATH)
HERMES_HOME=/app/.hermes             # Directory for persistent sessions, state and cache (configurable)
MAX_TURNS_DIAGNOSE=4                 # Max turns during incident diagnosis
MAX_TURNS_REMEDIATE=3                # Max turns during incident remediation
MAX_TURNS_CHAT=5                     # Max turns during conversational chat
MAX_BUDGET_USD_PER_EVENT=0.10        # Hard ceiling in USD per event
CYCLE_DETECTION_WINDOW=2             # Identical consecutive calls that trigger a cycle reject
```

The smart output buffer thresholds (50 lines, 25 head + 25 tail) are defaults of `SmartOutputBufferService` and are not configurable through the environment.

---

## 6. Execution Lifecycle & Failure Modes

1. **Subprocess Initialization**:
   - `HermesSubprocessBridge` resolves `.venv-hermes/bin/python` and verifies availability.
   - Spawns `hermes_worker.py` with `HERMES_HOME` configured for persistent local state.
   - Streams configuration payload via stdin.
2. **Turn & Budget Exhaustion**:
   - Bridge mode: each tool request from Hermes counts as one turn. The `post_api_request` hook reports token usage (`prompt_tokens`, `completion_tokens`, `total_tokens`) back to the host via IPC, which updates `BudgetUsage.accumulated_cost_usd`.
   - Once `turns_used >= max_turns` or `accumulated_cost_usd >= max_budget_usd`, `BudgetGuardService` answers further tool requests with a termination instruction instead of executing them. Hermes's own `max_iterations` acts as a hard boundary.
   - In-process fallback: turns and the USD cap (estimated from response token usage) are both enforced before each LLM call.
3. **Cycle Rejection**:
   - If the same tool call with identical arguments occurs consecutively, the runner rejects the call and instructs the model: *"Cycle detected. Tool was already executed with identical parameters. Synthesize existing results or adjust approach."*
4. **Mutating Tool Interception & Telegram Denial**:
   - Mutating tool requests sent by the worker are paused at `ToolGate.authorize()`.
   - Telegram sends interactive `[✅ Approve]` and `[❌ Deny]` buttons.
   - If the operator clicks `❌ Deny` or the approval times out, `ToolGate` returns a refusal message back over IPC. Hermes Agent receives the denial and reports what was found without executing the action.
5. **Observability & Langfuse Span Propagation**:
   - When Hermes executes an LLM call inside the subprocess, the `post_api_request` hook captures model parameters, token counts, and durations.
   - This data is transmitted back to LYOKO over IPC (`llm_usage` message), allowing LYOKO to register generation spans in the parent Langfuse trace and enrich trace cost footers in Telegram.

---

## 7. Testing & Quality Assurance Plan

1. **Unit Tests (`tests/agents/lyoko/test_budget_guard.py`)**:
   - Verify turn counting and budget accumulation logic.
   - Verify trip conditions for turn limits and USD budget limits.
2. **Unit Tests (`tests/agents/lyoko/test_hermes_bridge.py`)**:
   - Verify bridge availability and missing venv handling.
   - Verify bidirectional IPC message exchange (`status`, `call_tool`, `llm_usage`, `result`, `error`).
   - Verify tool denial forwarding over IPC.
3. **Unit Tests (`tests/agents/lyoko/test_hermes_harness_runner.py`)**:
   - Verify runner delegates to `HermesSubprocessBridge` in live execution.
   - Verify in-process mock fallback for fast unit testing.
   - Verify turn ceiling, cost cap, and cycle detection behavior.
4. **Architectural & Integration Tests**:
   - Run `tests/test_clean_architecture.py` to verify AST rules, zero banned imports, empty `__init__.py` files, and file line ceilings (<= 280 lines).
   - Full test suite verification with `uv run pytest`.
   - Linter verification with `uv run ruff check .` and `uv run ruff format --check .`.

---

## 8. Implementation Tasks (Active Plan)

1. **Langfuse LLM Tracing & Token Accounting via IPC**:
   - Register Hermes `post_api_request` hook in `hermes_worker.py`.
   - Emit an IPC `llm_usage` event carrying model name, input/output tokens, and duration.
   - In `HermesSubprocessBridge` / `HermesHarnessRunner`, record usage into `BudgetUsage` and create child generation spans under the active Langfuse trace.
2. **Persistent `HERMES_HOME` State**:
   - Set and pass `HERMES_HOME` (defaulting to `/app/.hermes` in container and `.hermes` locally) to the worker subprocess environment.
   - Declare persistent volume in `docker-compose.yml` (`hermes_data:/app/.hermes`).
3. **Container Image Integration (`Dockerfile`)**:
   - Add step in `packages/agents/lyoko/Dockerfile` to create `.venv-hermes` via `uv venv` and install `hermes-agent==0.19.0`.
   - Ensure container runtime can execute both the primary LYOKO Python environment and the isolated Hermes worker seamlessly.
4. **Dual Engine Support Preservation**:
   - Keep both `LYOKO_ORCHESTRATOR=hermes` and `LYOKO_ORCHESTRATOR=langgraph` completely supported, allowing runtime switching via environment variable.
