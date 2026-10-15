<div align="center">

# LYOKO AIOps

### Autonomous SRE & Self-Healing Platform for Homelabs

<img src="assets/code-lyoko-logo.png" alt="Code Lyoko Logo" width="360" />

<p><em>"Virtualization, Materialization, Self-Healing."</em><br/>
<em>A tribute to our favorite childhood series — bringing quantum supercomputer automation to homelab infrastructure.</em></p>

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![FastMCP](https://img.shields.io/badge/FastMCP-gateway-009688.svg?style=flat)](https://github.com/jlowin/fastmcp)
[![Hermes Agent](https://img.shields.io/badge/Hermes-Agent-0066FF.svg?style=flat)](https://github.com/NousResearch/Hermes-Function-Calling)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-336791.svg?style=flat&logo=postgresql&logoColor=white)](https://www.postgresql.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

<p align="center">
  Unified operations across <b>Kubernetes</b>, <b>UniFi Network</b>, <b>Home Assistant</b>, <b>Grafana</b>, and <b>GitHub</b>.
</p>

[Quick Start](docs/quickstart.md) · [Architecture](docs/architecture.md) · [Security](docs/security-guardrails.md) · [Packages](#packages)

</div>

---

## Overview

**LYOKO AIOps** is an event-driven, self-healing SRE platform built around an autonomous remediation engine (**LYOKO**, powered by Hermes Agent) and a unified, guardrail-protected tool gateway (**`sector5-mcp`**). 

When incidents occur, the system diagnoses root causes in read-only mode, formulates the minimum necessary fix, and requests human approval (HITL) via **Telegram** before applying any state modification.

> [!NOTE]
> **🎮 Why LYOKO?**
>
> A tribute to my favorite childhood show, *Code Lyoko*. Just like the team fighting off X.A.N.A.'s virtual attacks and deactivating towers from the factory console, LYOKO coordinates specialized domain operators to neutralize failures and protect your homelab.
>
> In this repository, **LYOKO** stands for **L**ive **Y**aml **O**ptimization & **K**8s **O**rchestration: your homelab's virtual defender that investigates alerts, neutralizes failures, and safely restores stability before downtime strikes.

<p align="center">
  <img src="assets/virtual-sectors-map.jpg" alt="LYOKO Virtual Sectores & Sector 5 MCP Gateway" width="100%" />
</p>

---

## Key Capabilities

- **Operational Skill Runbooks**: Dynamically matches alert metadata against curated operational runbooks and homelab best practices to guide investigations.
- **Zero-Trust Safety Harness**: Investigation and verification phases are strictly read-only. Destructive shell commands and protected namespaces are blocked at the gateway, while state-changing operations require explicit operator approval.
- **Unified Tool Gateway**: Aggregates Kubernetes, UniFi Network, Home Assistant, Grafana, and GitHub behind a single authenticated FastMCP gateway over Streamable HTTP and stdio.
- **Autonomous SRE Engine**: Powered by Hermes Agent, LYOKO coordinates domain investigations, diagnoses root causes, remediates incidents under ToolGate supervision, and produces structured reports.

---

## Architecture

<p align="center">
  <img src="docs/assets/system-architecture.png" alt="LYOKO AIOps Platform Architecture" width="100%" />
</p>

---

## Operating Modes

1. **Autonomous Incident Remediation**: Awakened by Prometheus Alertmanager webhooks. Runs an end-to-end pipeline: `Triage ➔ Autonomous Hermes Resolution (under ToolGate) ➔ Telegram Incident Report`.
2. **Interactive Operator Assistant**: A conversational Telegram assistant for day-to-day operations, log inspection, resource adjustments, and direct infrastructure administration.

---

## Packages

| Package | Role | Description |
| :--- | :--- | :--- |
| [`sector5-mcp`](packages/mcps/sector5-mcp) | Tool Gateway | FastMCP server aggregating upstream APIs with authentication, per-domain tool catalogs, and namespace guardrails. |
| [`lyoko`](packages/agents/lyoko) | Autonomous SRE Engine | Hermes-powered autonomous agent with ToolGate safety harness and Telegram approvals. |

---

## Quick Start

```bash
# 1. Clone repository
git clone https://github.com/csp33/lyoko-ai-ops.git
cd lyoko-ai-ops

# 2. Configure environment (set OPENAI_API_KEY)
cp .env.example .env

# 3. Launch full stack with Docker Compose
docker compose up -d
```

For environment variables, local `uv` development, and testing guides, see the [Quick Start Guide](docs/quickstart.md).

---

## Documentation

- [System Architecture](docs/architecture.md)
- [Security & ToolGate Policy](docs/security-guardrails.md)
- [Quick Start Guide](docs/quickstart.md)
- [sector5-mcp Gateway Guide](packages/mcps/sector5-mcp/README.md)
- [LYOKO Agent Guide](packages/agents/lyoko/README.md)
