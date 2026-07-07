## ADDED Requirements

### Requirement: Global cross-instance rate limiting
The system SHALL enforce a minimum 2-second interval between LLM requests across all LLMClient instances using a shared timestamp and asyncio.Lock.

#### Scenario: Multiple LLMClient instances share rate limit
- **WHEN** two or more LLMClient instances attempt concurrent requests
- **THEN** the second request waits until at least 2 seconds have passed since the first request started

#### Scenario: Rate limit interval is configurable
- **WHEN** LLM_MIN_REQUEST_INTERVAL_SEC environment variable is set
- **THEN** that interval is used instead of the default 2 seconds

### Requirement: Streaming request mode
The system SHALL use stream=True for all LLM API calls and concatenate chunks into complete text before returning.

#### Scenario: LLM response arrives in chunks
- **WHEN** an LLM request is made with stream=True
- **THEN** all chunks are collected and concatenated into complete text before being returned to the caller

#### Scenario: Streaming is transparent to callers
- **WHEN** chat() or chat_json() is called
- **THEN** the interface remains unchanged; streaming happens internally

### Requirement: Truncation detection
The system SHALL detect stream truncation by checking finish_reason and raise StreamTruncatedError when finish_reason is not "stop".

#### Scenario: Stream completes normally
- **WHEN** finish_reason is "stop"
- **THEN** no truncation error is raised

#### Scenario: Stream is truncated
- **WHEN** finish_reason is "length", "content_filter", or any value other than "stop"
- **THEN** StreamTruncatedError is raised with the finish_reason in the error message

### Requirement: JSON retry on truncation
The system SHALL automatically retry chat_json() once when truncation is detected, and fall back to schema default if retry also fails.

#### Scenario: JSON parsing fails due to truncation
- **WHEN** chat_json() receives truncated response (finish_reason != "stop" OR text does not end with } or ])
- **THEN** the request is automatically retried once with the same parameters

#### Scenario: Retry succeeds
- **WHEN** retry returns valid JSON
- **THEN** parsed JSON is returned to caller

#### Scenario: Retry also fails
- **WHEN** retry still returns invalid or truncated JSON
- **THEN** schema default value is returned (normal degradation path)

### Requirement: Synthesis prompt output reduction
The system SHALL limit SYNTHESIS_PROMPT candidate count to 10 to reduce output pressure and truncation risk.

#### Scenario: Synthesis prompt includes top 10 candidates
- **WHEN** ResearchSynthesizer generates final synthesis prompt
- **THEN** at most 10 candidates are included in the prompt (reduced from previous 12)
