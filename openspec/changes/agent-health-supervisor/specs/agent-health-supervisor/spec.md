## ADDED Requirements

### Requirement: Aggregated agent health snapshot

The Orchestrator SHALL aggregate four agent-health dimensions — loop-alive (stall), queue backlog, LLM degraded, and data degraded — into the periodically-written `agent_health.json`, via the single pure function `utils/health_snapshot.py::build_health_snapshot`. The snapshot MUST preserve the existing legacy keys (`agents_registered`, `tasks_alive`, `tasks_failed`, `halted_symbols`, `bus_dlq_size`, `ts`) for backward compatibility, and add `loop_health`, `queue_health`, `llm_health`, and `data_health` sub-objects. The builder MUST be pure (no IO, no side effects, no mutation of agent state, no bus calls); all external state is passed in by the caller. `_write_agent_health` MUST continue to return the DLQ size so the existing DLQ-growth alert chain is unaffected.

#### Scenario: Snapshot extends legacy schema
- **WHEN** the Orchestrator writes `agent_health.json`
- **THEN** the file contains all six legacy keys unchanged
- **AND** it additionally contains `loop_health`, `queue_health`, `llm_health`, `data_health`

#### Scenario: Builder is pure
- **WHEN** `build_health_snapshot` is called with agent stubs and bus metrics
- **THEN** it returns a snapshot dict computed only from the passed-in state
- **AND** it performs no IO, does not mutate the agents, and does not call the bus

### Requirement: Loop-alive heartbeat stall detection

Each agent SHALL stamp `_last_alive_ts` at the top of every `_message_loop` iteration (the loop polls `bus.receive` with a 0.5s timeout, so a healthy agent refreshes the stamp at least every ~0.5s regardless of message arrival or business cadence). The supervisor SHALL flag an agent as stalled when `now - _last_alive_ts` strictly exceeds `AGENT_STALL_TIMEOUT_SEC` (default 60). Agents that have not started (`_last_alive_ts <= 0`) MUST be skipped. The separate `_last_work_ts` (stamped only when a message is actually processed) MUST be display-only and MUST NOT drive any alert.

#### Scenario: Idle but alive agent is not stalled
- **WHEN** an agent's message loop is running but receives no messages for longer than the business idle period but its `_last_alive_ts` is within the stall threshold
- **THEN** the agent is NOT flagged as stalled

#### Scenario: Hung loop is flagged
- **WHEN** an agent's `_last_alive_ts` is older than `AGENT_STALL_TIMEOUT_SEC`
- **THEN** the agent appears in `loop_health.stalled` with its idle seconds

#### Scenario: Unstarted agent is skipped
- **WHEN** an agent has `_last_alive_ts <= 0`
- **THEN** it is not counted as stalled

### Requirement: Edge-triggered health transition alerts

The Orchestrator SHALL emit a `telegram_alert` exactly once when any health dimension transitions from healthy to unhealthy (level `warning`, type `health_<dim>`), and exactly once when it transitions back from unhealthy to healthy (level `info`, type `health_<dim>_recovered`). While a dimension remains persistently unhealthy, no further alert is emitted. The four dimensions MUST be tracked independently. The unhealthy predicate is: loop = `stalled_count > 0`; queue = `backlogged_count > 0`; llm = `degraded`; data = `degraded OR stale`. This alerting is observability-only and MUST NOT auto-halt, auto-remediate, or affect any trading decision. The existing DLQ-growth and agent-task-failed alerts, and the Judge's `risk_alert{llm_degraded}` decision path, MUST remain independent and unchanged.

#### Scenario: Rising edge fires once
- **WHEN** a dimension becomes unhealthy and was healthy on the previous tick
- **THEN** exactly one `health_<dim>` warning is published
- **AND** subsequent ticks with the dimension still unhealthy publish nothing

#### Scenario: Recovery fires once
- **WHEN** a dimension returns to healthy after having been unhealthy
- **THEN** exactly one `health_<dim>_recovered` info is published

#### Scenario: Oscillation re-alerts
- **WHEN** a dimension goes unhealthy, recovers, then goes unhealthy again
- **THEN** the second unhealthy transition publishes a new `health_<dim>` warning

#### Scenario: Dimensions are independent
- **WHEN** two dimensions become unhealthy on the same tick
- **THEN** each publishes its own transition alert independently

### Requirement: Telegram health visibility

The Telegram interface SHALL expose agent health both as a one-line summary appended to `/status` (listing only unhealthy dimensions, or `✓` when all healthy, or a missing-snapshot indicator) and as a dedicated `/health` command rendering per-dimension detail (each offending agent/symbol, plus the snapshot age). When the health snapshot is missing or unreadable, both views MUST degrade gracefully rather than error.

#### Scenario: /status summary lists only unhealthy dimensions
- **WHEN** a user runs `/status` and some dimensions are unhealthy
- **THEN** a health line lists only the unhealthy dimensions with a ⚠ marker
- **AND** when all dimensions are healthy the line shows `✓`

#### Scenario: /health detail shows offenders
- **WHEN** a user runs `/health` with stalled or degraded entities
- **THEN** each offending agent/symbol is listed under its dimension
- **AND** the snapshot age is shown

#### Scenario: Missing snapshot degrades gracefully
- **WHEN** `agent_health.json` is missing or unreadable
- **THEN** `/status` and `/health` show a missing-snapshot indicator without raising

### Requirement: Health thresholds are configurable and bounded

The three supervisor thresholds — `AGENT_STALL_TIMEOUT_SEC` (default 60), `QUEUE_BACKLOG_WARN_PENDING` (default 200), and `DATA_STALE_TIMEOUT_SEC` (default 180) — SHALL be defined in `config_loader` DEFAULTS with HARD_LIMITS bounds and env-var overrides, following the existing config pattern. These thresholds affect observability/alert sensitivity only and MUST NOT relax any trading risk limit.

#### Scenario: Defaults within hard limits
- **WHEN** config is loaded with no overrides
- **THEN** the three thresholds resolve to their defaults (60 / 200 / 180), each within its HARD_LIMITS range
