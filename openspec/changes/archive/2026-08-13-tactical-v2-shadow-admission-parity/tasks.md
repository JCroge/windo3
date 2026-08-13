## 1. Episode Renewal

- [x] Add a regression test for terminal `neutral + unblocked + fresh closed bar` renewal.
- [x] Add negative tests for neutral without fresh evidence and neutral while blocked.
- [x] Implement the minimal EpisodeRegistry renewal rule using monotonic closed-bar or changed structure evidence.
- [x] Verify episode replay and restart preserve the new epoch and terminal historical episodes.

## 2. Candidate Receipts

- [x] Define the compact receipt event schema and backward-compatible replay behavior.
- [x] Persist receipts for accepted, duplicate, validation, expiry, block, capacity, and other rejection paths.
- [x] Preserve message ID/source Shadow ID mapping without assuming historical receipts exist.
- [x] Add tests for receipt ordering, idempotent replay, and absent historical receipts.

## 3. Shadow Parity Replay

- [x] Add a pinned 22-candidate replay fixture derived from the audited cloud sequence without live credentials or exchange I/O.
- [x] Assert normalized output of three BICO episodes and two PUMP episodes, with repeated rows explicitly rejected.
- [x] Run the replay 100 times and assert identical accepted identities and reasons.
- [x] Keep executable quote, governor capacity, entry TTL, and protection checks in the parity boundary.

## 4. Verification and Operations

- [x] Run the focused Tactical V2 regression suite and the full relevant test suite.
- [x] Add an operator report showing accepted, duplicate, rejected, and unknown candidate counts.
- [x] Update the runbook to prohibit Sidecar admission restoration until quote-level and protection evidence passes.
- [x] Verify no production configuration or cloud data is modified by the implementation tests.
- [x] Reconcile current-stage operator documentation with the 2026-08-12 Sidecar NO-GO gate.
