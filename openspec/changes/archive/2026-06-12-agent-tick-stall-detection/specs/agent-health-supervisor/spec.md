## ADDED Requirements

### Requirement: Tick-loop hang detection

Each agent SHALL stamp `_tick_enter_ts` immediately before each `tick()` invocation and `_tick_exit_ts` immediately after it returns, in `_periodic_loop`. The supervisor SHALL flag an agent's tick loop as hung when it is currently inside a tick (`_tick_enter_ts > _tick_exit_ts`) AND the current tick has been executing longer than `AGENT_TICK_STALL_TIMEOUT_SEC` (default 120, anchored at 2× the longest healthy single-tick duration of ~60s). Agents that have not yet entered a tick (`_tick_enter_ts <= 0`) MUST be skipped. This detection is complementary to the message-loop `_last_alive_ts` heartbeat: the former catches a hung periodic loop while the message loop remains healthy. The tick-hung signal MUST be surfaced within the existing `loop_health` dimension (`tick_stalled_count`, `tick_stalled` list of `{name, tick_sec}`), and the loop dimension's unhealthy predicate becomes `stalled_count > 0 OR tick_stalled_count > 0`. Alert detail and the `/health` view MUST distinguish a message-loop stall from a tick-loop hang, since they imply different operator actions. This detection is observability-only and MUST NOT gate, veto, halt, or otherwise affect any trading decision.

#### Scenario: Hung tick is flagged
- **WHEN** an agent is inside a `tick()` call (`_tick_enter_ts > _tick_exit_ts`) and the current tick has run longer than `AGENT_TICK_STALL_TIMEOUT_SEC`
- **THEN** the agent appears in `loop_health.tick_stalled` with its current tick seconds
- **AND** the loop dimension is reported unhealthy

#### Scenario: Healthy long tick within budget is not flagged
- **WHEN** an agent is inside a `tick()` call but the current tick has run for less than `AGENT_TICK_STALL_TIMEOUT_SEC` (e.g. a 60s reviewer sleep)
- **THEN** the agent is NOT flagged as tick-stalled

#### Scenario: Between ticks is not flagged
- **WHEN** an agent has completed its last tick (`_tick_exit_ts >= _tick_enter_ts`)
- **THEN** the agent is NOT flagged as tick-stalled regardless of elapsed time since the last tick

#### Scenario: Unstarted agent is skipped
- **WHEN** an agent has `_tick_enter_ts <= 0` (no tick has begun)
- **THEN** it is not flagged as tick-stalled

#### Scenario: Message-loop stall and tick hang are distinguished
- **WHEN** an alert fires for the loop dimension
- **THEN** the alert detail indicates whether the cause is a message-loop stall (`_last_alive_ts`) or a tick-loop hang (`_tick_enter_ts`)

### Requirement: Tick stall threshold is configurable and bounded

The tick-stall threshold `AGENT_TICK_STALL_TIMEOUT_SEC` (default 120) SHALL be defined in `config_loader` DEFAULTS with a HARD_LIMITS bound of `[30, 3600]` and an env-var override, following the existing config pattern. It affects observability/alert sensitivity only and MUST NOT relax any trading risk limit.

#### Scenario: Default within hard limits
- **WHEN** config is loaded with no override
- **THEN** `AGENT_TICK_STALL_TIMEOUT_SEC` resolves to 120, within its HARD_LIMITS range `[30, 3600]`
