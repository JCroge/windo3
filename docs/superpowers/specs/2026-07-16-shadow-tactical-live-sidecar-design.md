---
comet_change: promote-shadow-tactical-live-48h
role: technical-design
canonical_spec: openspec
archived-with: 2026-07-17-promote-shadow-tactical-live-48h
status: final
---

# Shadow Tactical Live Sidecar

## Context

This change promotes the existing Shadow Tactical stream into a 24-hour live experiment without changing Main Tactical admission policy. Main continues to produce rejected Tactical plan records in `data/rejected_signal_events.jsonl`; the new sidecar consumes those records after they are written and mirrors eligible Tactical plans live.

The sidecar must not rerun Main Judge, CandidateRanker, RR/EV/cost promotion logic, Tactical slot logic, or Tactical circuit admission gates. It does still need mechanical exchange safety: malformed plans, invalid SL side, insufficient free balance, unknown OKX position mode, precision/min-size failures, excessive slippage/depth failures, and unverified protective SL must fail closed.

Same-account OKX deployment is allowed for the experiment. The account-level coupling is therefore handled explicitly: Main must not backfill sidecar-owned positions, Main must not cancel/adopt sidecar-owned or foreign owner-tag SL algos, and the sidecar must avoid same-symbol exposure that cannot be split from Main exposure after OKX aggregation.

## Recommended Approach

Use a separate live sidecar process that tails the shadow event log and writes its own state, audit, lifecycle, and ownership files. This is preferable to relaxing Main Tactical gates because the user wants the shadow Tactical artifacts themselves to drive live execution. It is also more practical than waiting for a subaccount because same-account deployment has been accepted, provided the dangerous account-level paths are guarded.

The sidecar starts from a durable watermark at process start by default. It processes only new `rejected_plan_created` events whose payload is Tactical by `track=tactical` or `exit_profile=tactical_v1`. Each shadow id is idempotent: once a record is seen, the sidecar records its status before or atomically with execution bookkeeping, so a restart or reread cannot duplicate an order.

## Components

### Shadow Tailer

The tailer reads `data/rejected_signal_events.jsonl`, tracks byte offset or event id watermark, and emits parsed candidate records. It ignores old records by default, ignores non-Tactical records without treating them as errors, and records malformed JSON or missing-field failures as sidecar audit events.

### Tactical Plan Mapper

The mapper converts a shadow record directly into a sidecar execution plan. It preserves symbol, side, entry price, stop loss, take profit levels, leverage, Tactical max hold minutes, exit profile, Tactical source, and attribution metadata. Gate metadata from the shadow record is retained for audit, but it does not block sidecar admission.

### Sidecar Executor Path

The executor path should be narrow: open the mapped plan with mechanical checks only. It may reuse existing exchange utilities for balance, precision, amount calculation, slippage/depth, OKX attach algo creation, and protective SL verification. It must avoid the strategy-ish parts of the normal smart plan path that would reclassify drift, recompute RR, or abandon because a strategy threshold failed.

The sidecar should use explicit state paths or a supported sidecar namespace so it does not write Main `positions.json`, Main risk state, Main halt state, Main live order events, or Main lifecycle files. It should use a distinct `BOT_INSTANCE_ID` or order prefix for entry orders and attached SL algos.

### Ownership Registry

The sidecar writes a registry such as `data/shadow_tactical_live_owners.json`. Each record should include shadow id, symbol, side, intended margin, order id, entry client order id, SL algo id, SL algo client order id, opened/closed status, and timestamps. Main uses this registry only as an ownership signal; Main strategy accounting does not adopt sidecar positions.

### Main Owner Isolation

Main `sync_positions()` currently sees account-level exchange positions and can backfill unknown positions into Main local state. It must consult sidecar ownership before backfill and skip positions that match active sidecar-owned exposure.

Main OKX algo migration currently scans account-level pending algos. It must classify foreign owner-tag algos and never cancel, replace, or adopt them. Main cleanup/close paths should remain limited to known Main-owned algos or exact local algo ids/client order ids.

### Same-Symbol Guard

In same-account mode, OKX may aggregate or net exposure for the same symbol and side. The sidecar therefore rejects or defers a shadow record when there is existing non-sidecar account exposure for that symbol/side bucket. This reduces exact shadow coverage only when the account cannot represent the shadow intent independently.

## Data Flow

1. Main writes a rejected Tactical plan to `data/rejected_signal_events.jsonl`.
2. Sidecar tailer reads the new event after its start watermark.
3. Event filter accepts only Tactical `rejected_plan_created` records.
4. Mapper validates required mechanical fields and creates the execution plan.
5. Sidecar checks idempotency, active exposure cap, same-symbol account exposure, hard amount/balance constraints, OKX mode, order precision/min-size, and slippage/depth.
6. Sidecar opens the trade with sidecar-owned client order ids and attached protective SL.
7. Sidecar verifies the attached SL. Failure closes sidecar-owned exposure when possible or halts further sidecar opens for that symbol.
8. Sidecar writes audit, ownership, state, and lifecycle records to sidecar-specific files.
9. Main sync/migration sees the account-level objects but skips sidecar-owned positions and foreign owner-tag algos.

## Error Handling

The sidecar fails closed for missing fields, invalid side, invalid SL side, missing TP/SL, duplicate shadow id, free-balance shortage, hard-limit breach, order precheck failure, slippage/depth failure, unknown OKX posMode, and protective SL verification failure.

Audit events must distinguish skipped, rejected, attempted, opened, protection_failed, closed, and stop-window-expired outcomes. The stop command only cancels or closes exposure whose ownership is proven by the sidecar registry and order tags; it refuses to touch ambiguous account exposure.

## Testing Strategy

Unit tests should cover Tactical event filtering, non-Tactical ignore behavior, malformed JSON and missing-field failures, direct shadow-to-plan mapping, idempotency/watermark behavior, hard-limit enforcement, same-symbol account exposure rejection, Main sync skipping sidecar-owned positions, and Main OKX migration preserving foreign owner-tag SL algos.

Integration-style tests can use fake exchange objects to prove that the sidecar writes only sidecar files and that Main account sync/migration does not mutate sidecar-owned objects. OpenSpec validation remains part of the verification checklist.

## Rollout

First deploy the Main owner-ignore and foreign-owner algo migration safety patch. Then start the sidecar as a separate cloud process with live OKX credentials, a distinct sidecar bot id/order prefix, and a 24-hour duration. The sidecar starts from new events after launch unless an explicit backfill option is provided later. At the end of the window it stops accepting new events and emits final processed/opened/rejected/active counts.

## Residual Risks

Same-account margin and equity remain shared. A sidecar position can still affect available balance, liquidation distance, and account risk for Main. The same-symbol guard prevents the worst ownership ambiguity but cannot make same-account exposure equivalent to a dedicated subaccount. For exact one-shadow-record-to-one-position replay across overlapping same-symbol records, a separate OKX subaccount remains the cleaner operational boundary.
