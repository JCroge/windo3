## MODIFIED Requirements

### Requirement: Entry Drift Classification
The system SHALL classify the relative drift between Judge plan's `entry_ref`
anchor and the executor's live ticker price into one of four bands and act
accordingly:

- `accept` (drift ≤ 0.5%): proceed with the original plan unchanged
- `small` (0.5% < drift ≤ 2%): recompute SL/TP by sl_pct/tp_pct ratios on the
  new entry, re-check R:R against the plan's original floor; for a Sidecar
  Tactical plan, the original floor SHALL be `tactical_min_rr_for_track` when
  it is finite and positive; pass = accept recomputed plan, fail = reject with
  reason `drift_rr_floor_fail`
- `medium` (2% < drift ≤ 5%): recompute as above but with floor + 0.20
  absolute bump
- `abandon` (drift > 5%): reject with reason `drift_too_large`

Non-Sidecar plans and Sidecar plans without a valid Tactical floor SHALL retain
the existing floor selection: `gate_metadata.rr_floor` when present, otherwise
`2.0`.

#### Scenario: Tactical Sidecar drift uses its frozen lower floor
- **WHEN** a Sidecar Tactical plan has `tactical_min_rr_for_track=0.75`
- **AND** live drift is in the small band
- **AND** the recomputed R:R is `0.81`
- **THEN** the drift gate SHALL return `recalc_pass`
- **AND** it SHALL NOT reject solely because the generic `2.0` floor is unmet

#### Scenario: Generic plan fallback remains unchanged
- **WHEN** a non-Sidecar plan has no valid Tactical floor
- **AND** its metadata has no explicit `rr_floor`
- **THEN** the drift gate SHALL continue to use floor `2.0`

#### Scenario: Medium Tactical drift keeps the safety bump
- **WHEN** a Sidecar Tactical plan has `tactical_min_rr_for_track=0.75`
- **AND** drift is in the medium band
- **THEN** the drift gate SHALL use `0.95` as the R:R floor
