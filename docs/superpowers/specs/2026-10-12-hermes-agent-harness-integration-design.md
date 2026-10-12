# Hermes Agent Harness Integration & Cost Guardrails Design

## 1. Problem Statement & Motivation

Currently, LYOKO's cognitive execution is driven by a procedural ReAct loop (`ReActHarnessRunner`) and rigid LangGraph state machines. While this guarantees structured phase transitions for incidents, it introduces several operational limitations in a real-world homelab environment:

1. **Repetitive Exploratory Cost**: Every new firing alert starts with a clean slate. Even for known recurring issues (e.g., recurring PVC locks or transient CrashLoopBackOffs), the agent executes 4-6 redundant tool discovery and diagnosis calls, incurring repetitive token expenses on OpenAI.
2. **Lack of Long-Term Learning**: The agent does not synthesize successful remediation patterns into self-improving operational skills.
3. **Rigid Delegation vs. Adaptive Execution**: When investigating complex cross-domain failures (e.g., UniFi network degradation affecting Home Assistant and K8s API connectivity), 1-level specialist handoffs struggle compared to autonomous subagent task branching.
4. **Unbounded Spend Risk**: Without an explicit hard-dollar budget ceiling per execution, runaway tool loops or large tool payloads can lead to unexpected API costs.

By integrating the autonomous reasoning loop, persistent memory, and skill-synthesis capabilities inspired by **Hermes Agent** (Nous Research) while strictly enforcing **hard cost budget caps**, **turn limits**, and **ToolGate human-in-the-loop (HITL)** approvals, LYOKO gains adaptive reasoning and self-improving memory without sacrificing safety or financial control.

---

## 2. Architectural Objectives

1. **Strict Cost & Budget Ceilings**:
   - Explicit `max_turns` per operational phase: Diagnose (4), Remediate (3), Chat (5).
   - Hard budget cap per event (default: `$0.10 USD`). Execution halts gracefully when the ceiling is reached, preventing runaway costs.
   - Cycle detection to immediately terminate repetitive identical tool invocations.
   - Large output buffering (`SmartOutputBufferService`) to truncate payloads >50 lines to head+tail (25+25) and offload dumps to `.lyoko/scratchpad/`.
2. **Guaranteed Safety & HITL (Zero-Trust Tool Execution)**:
   - All state-mutating tool calls (e.g., Kubernetes patches, restarts, Home Assistant service calls) are strictly routed through LYOKO's `ToolGate`.
   - Mutating calls are suspended and dispatch interactive Telegram approval buttons (`[✅ Approve]` / `[❌ Deny]`). The harness cannot bypass human confirmation.
3. **Hexagonal Clean Architecture Compliance**:
   - Domain layer defines pure budget models and ports (`BudgetTracker`, `HarnessRunnerInterface`, `AgentHarnessInterface`).
   - Application layer provides safety gates (`BudgetGuardService`).
   - Infrastructure layer implements the concrete runner (`HermesHarnessRunner`).
   - Strict adherence to project rules: empty `__init__.py`, no wildcard imports, module line count <= 280 lines.
4. **Observability & Trace Continuity**:
   - Single Langfuse trace per event with nested child spans for iterations and tool executions.
   - Real-time status reporting via `on_status` callbacks to drive Telegram draft heartbeats.
   - Cost transparency footer in Telegram messages displaying token counts and total spend.
5. **Self-Improving Skill Synthesis**:
   - Successful resolutions with operator approvals synthesize candidate skill documents in Markdown with YAML frontmatter under `.lyoko/learned_skills/`.

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
         ┌────────────────────────────────────────────────────────┐
         │ 1. Turn & Budget Check:                                │
         │    - turns < max_turns?                                │
         │    - cost < max_budget_usd ($0.10)?                    │
         │                                                        │
         │ 2. Context & Skill Injection:                          │
         │    - Load matching skills from MarkdownSkillRepo       │
         │                                                        │
         │ 3. LLM Reasoning Call (OpenAI gpt-4o / gpt-4o-mini):   │
         │    - Calculate tokens & track accumulated cost         │
         │                                                        │
         │ 4. Cycle Detection:                                    │
         │    - Identical tool + args repeated? -> Reject cycle   │
         │                                                        │
         │ 5. Tool Execution via ToolGate:                        │
         │    - Read-only? -> Direct call to sector5-mcp          │
         │    - Mutating?  -> Telegram HITL [Approve] [Deny]      │
         │                                                        │
         │ 6. Smart Output Buffer:                                │
         │    - <=50 lines: Keep in context                       │
         │    - >50 lines: Truncate 25+25 & offload to disk       │
         └────────────────────────────────────────────────────────┘
                       │
                       ▼
            [ Langfuse Trace & Telegram Footer ]
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
- `application/skills/`:
  - Uses existing `SkillMatcherService` to inject relevant operational runbooks into the initial prompt.

### 4.3 Infrastructure Layer (`packages/agents/lyoko/src/lyoko/infrastructure/`)
- `infrastructure/llm/hermes_runner.py`:
  - `HermesHarnessRunner`: Implements `HarnessRunnerInterface`.
  - Bounded iteration loop driven by `BudgetGuardService`.
  - Dynamic pricing calculation for OpenAI models (`gpt-4o`, `gpt-4o-mini`).
  - Integrates `SmartOutputBufferService` for payload truncation.
  - Intercepts tool execution to delegate to `ToolGate` for HITL approvals.
  - Emits status callbacks for Telegram draft heartbeats.
- `infrastructure/llm/openai.py`:
  - Configures and wires `HermesHarnessRunner` based on `settings.HARNESS_ENGINE`.

---

## 5. Configuration & Defaults (`lyoko/config.py`)

New environment variables added to `.env.example` and `config.py`:

```bash
# Agent Harness & Execution Limits
LYOKO_HARNESS_ENGINE="hermes"                 # "hermes" | "react"
LYOKO_MAX_TURNS_DIAGNOSE=4                    # Max LLM turns during incident diagnosis
LYOKO_MAX_TURNS_REMEDIATE=3                   # Max LLM turns during incident remediation
LYOKO_MAX_TURNS_CHAT=5                        # Max LLM turns during conversational chat
LYOKO_MAX_BUDGET_USD_PER_EVENT=0.10           # Hard ceiling in USD per incident/chat trace
LYOKO_CYCLE_DETECTION_WINDOW=2                # Number of identical calls triggering a cycle reject
LYOKO_SMART_BUFFER_MAX_LINES=50               # Max lines in LLM tool context (25 head + 25 tail)
```

---

## 6. Execution Lifecycle & Failure Modes

1. **Turn Exhaustion**:
   - When `turns_used >= max_turns`, the runner injects a final system instruction: *"Turn limit reached. Summarize all collected evidence, root causes identified, and recommended next steps immediately without calling further tools."*
2. **Budget Cap Trip**:
   - If `accumulated_cost_usd >= max_budget_usd`, tool execution is halted immediately. The runner logs a warning and returns the accumulated findings tagged with `[Budget Cap Reached]`.
3. **Cycle Rejection**:
   - If the same tool call with identical arguments occurs consecutively, the runner rejects the call and instructs the model: *"Cycle detected. Tool was already executed with identical parameters. Synthesize existing results or adjust approach."*
4. **Tool Denial / Timeout**:
   - When the operator clicks `❌ Deny` or the approval times out, `ToolGate` returns a refusal message. The runner acknowledges the denial and does not retry the rejected action.

---

## 7. Testing & Quality Assurance Plan

1. **Unit Tests (`tests/agents/lyoko/test_budget_guard.py`)**:
   - Verify turn counting and budget accumulation logic.
   - Verify trip conditions for turn limits and USD budget limits.
2. **Unit Tests (`tests/agents/lyoko/test_hermes_harness_runner.py`)**:
   - Test execution halts cleanly when `max_turns` is reached.
   - Test execution halts when `cost_limit` is exceeded.
   - Test cycle detector blocks repetitive tool loops.
   - Test tool calls correctly delegate to `ToolGate` and respect read-only vs. mutating permissions.
   - Test output buffering truncates oversized responses.
3. **Architectural & Integration Tests**:
   - Run `tests/test_clean_architecture.py` to verify AST rules, zero banned imports, and file line ceilings.
   - Verify full test suite passes with `uv run pytest`.
