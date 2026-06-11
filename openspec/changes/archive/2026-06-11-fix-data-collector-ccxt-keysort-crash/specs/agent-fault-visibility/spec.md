## ADDED Requirements

### Requirement: Agent setup failures are logged, never silent

When an agent's `setup()` raises during `BaseAgent.run()`, the system SHALL log the full exception traceback at CRITICAL level (identifying the agent) before the task terminates. A setup failure MUST NOT leave the agent dead with no diagnostic output.

#### Scenario: setup() raises an exception
- **WHEN** an agent's `setup()` raises (e.g. `load_markets()` throws)
- **THEN** `run()` logs `Agent [<name>] setup 失败` with the full `traceback.format_exc()` at CRITICAL level
- **AND** the exception is re-raised so the task is recorded as failed

#### Scenario: Healthy setup is unaffected
- **WHEN** an agent's `setup()` completes normally
- **THEN** no failure is logged and the agent proceeds to its message/periodic loops

### Requirement: Failed agent tasks raise an operator alert

The orchestrator health loop SHALL detect agent tasks that have terminated with an exception and publish a `telegram_alert` of type `agent_task_failed` that names the affected agent and the exception. The same failed task MUST NOT be alerted repeatedly on every health tick.

#### Scenario: An agent task dies with an exception
- **WHEN** the health loop observes a task that is done, not cancelled, and `exception()` is not `None`
- **THEN** it publishes `telegram_alert {type: "agent_task_failed", agent: <name>, error: <repr>}`
- **AND** `agent_health.json` continues to report the `tasks_failed` count

#### Scenario: The same failure is not re-alerted
- **WHEN** a previously-alerted failed task is still failed on a subsequent health tick
- **THEN** no duplicate alert is published for that task

#### Scenario: Failed task cannot be mapped to an agent
- **WHEN** a failed task's index does not correspond to a known agent
- **THEN** the alert is still published with an `unknown-agent` label rather than being suppressed
