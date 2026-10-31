<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0045 — Agents: a graph behind a platform contract, tools over MCP, the model over an API

- **Status:** Accepted
- **Date:** 2026-10-31
- **Parent:** [ADR-0001](ADR-0001-industryflow-framing.md) (dec 1: observability and analytics are in
  scope)
- **Related:** [ADR-0036](ADR-0036-out-of-process-model-runtime.md) (dec 1: a separate service, deployed
  only where it is wanted), [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (dec 4, 6:
  injected secrets, Compose and Helm), [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md)
  (dec 4, 7: authenticated endpoints, no secrets in logs), [ADR-0046](ADR-0046-infrastructure-agent.md)
  (the first agent)

## Context

The platform gains agents that turn its own signals into text a person can act on, starting with
infrastructure incidents and growing one task at a time. Agent frameworks and model providers change
faster than the platform; the language model may be a hosted service or one the operator runs on a
separate host, and a small local model calls tools less reliably than a large hosted one. Whatever is
sent to a model leaves the platform's control, and generated text can be wrong while reading as
authoritative.

## Decision

1. **An agent is a standalone service with one contract: a trigger in, a note out.** Its internal flow is
   a graph of steps; agents are added as services, never as code inside an existing one.
2. **An agent reaches data only through tools served over MCP, configured per agent.** A tool outlives
   any agent framework and is usable by any MCP client; reading tools are separate from any tool that
   acts.
3. **The model is always reached over an API, its endpoint, provider and model chosen by
   configuration.** No model runs in the agent's process; one protocol covers hosted services and a model
   server on the operator's own host.
4. **Deterministic code gathers the evidence; the model only writes from it.** A statement about the
   system cites the evidence it rests on, and the model invents no figure.
5. **Every note says it is generated and names the model that wrote it.**
6. **Content is redacted before it leaves for a model, whatever the endpoint, and each sending is
   recorded.** Each agent's record names the categories it redacts; the record keeps what was redacted
   and when, never the redacted values.
7. **The graph framework is an implementation choice inside one agent, not a platform contract.**
8. **An agent is off unless the operator enables it.** It holds only the credentials its tools need,
   injected from outside the package.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Agent logic inside an existing service | Couples the model's failure modes and credentials to a service with other duties |
| 1 | Agents as extension plugins in the serving process | A first-party platform capability, not a domain extension (ADR-0008 dec 7), and it would share a process with scoring |
| 2 | Tools written against one framework's tool interface | Tied to the framework's lifetime; no other client can use them |
| 2 | The agent querying stores directly | Every agent would hold store credentials and its own copy of each query |
| 3 | A model loaded in the agent's process | The host has no accelerator, the model's size bounds the service, and swapping models becomes a rebuild |
| 3 | One provider fixed in code | The provider choice is open, and a local model must remain possible |
| 4 | The model investigating by calling tools at will | Unreliable on small models and not reproducible |
| 4 | The model reading logs continuously | Cost and noise without a signal; detection already exists |
| 5 | Unmarked generated text | Reads as an operator's or the platform's own statement |
| 6 | Redaction only for hosted endpoints | Two paths, and a local endpoint on another host is still another host |
| 7 | The framework's interfaces as the platform contract | Every agent and tool would follow the framework's churn |
| 8 | Agents on by default | Would send content to a model nobody chose |

## Consequences

**Positive**
- A new agent is a new service with its own tools; existing agents and tools are untouched.
- The model and provider change by configuration, so hosted and local models can be compared on the
  same incidents.

**Negative**
- Each agent is one more service to deploy and operate.
- Notes are only as good as the evidence the deterministic steps gather.
- Redaction can remove context the model would have needed.

## Deferred

| Open question | Settled by |
|---|---|
| Tools that act, and the human confirmation they need | The first agent that proposes an action |
| Cost limits per agent or per day | The first hosted-model bill that matters |
| Agents serving tenants rather than operators | The first tenant-facing agent; tenant isolation then binds every tool |

## References

- [ADR-0046](ADR-0046-infrastructure-agent.md): the first agent.
