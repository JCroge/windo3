from types import SimpleNamespace

import pytest


def _paths(tmp_path):
    return SimpleNamespace(
        tactical_v2_events=str(tmp_path / "events.jsonl"),
        tactical_v2_state=str(tmp_path / "state.json"),
    )


def _candidate(entry=1.0, side="long"):
    return {"symbol": "WLD-USDT-SWAP", "side": side, "entry_ref": entry}


def _structure(
    bias="bullish",
    token="break-up-1",
    *,
    available=True,
    block_long=False,
    block_short=False,
):
    return {
        "tf_15m_available": available,
        "tf_15m_bias": bias,
        "tf_15m_structure_token": token,
        "tf_15m_closed_bar_ts": 900.0,
        "tf_15m_block_long": block_long,
        "tf_15m_block_short": block_short,
    }


def _registry(tmp_path):
    from utils.tactical_v2.episodes import EpisodeRegistry
    from utils.tactical_v2.store import TacticalStore

    return EpisodeRegistry(TacticalStore(_paths(tmp_path)), namespace="testnet")


def _latest_reset_evidence_reason(registry):
    events = [
        event
        for event in registry.store.read_events()
        if event["event_type"] == "episode_reset_evidence"
    ]
    return events[-1]["data"]["evidence"]["reason"]


def test_repeated_prices_in_same_structure_share_consumed_episode(tmp_path):
    registry = _registry(tmp_path)

    first = registry.assign(_candidate(1.0), _structure())
    second = registry.assign(_candidate(1.01), _structure())

    assert first.episode_id == second.episode_id
    assert first.eligible is True
    assert second.eligible is False
    assert second.reason == "duplicate_episode"


def test_episode_identity_and_terminality_survive_restart(tmp_path):
    from utils.tactical_v2.episodes import EpisodeRegistry
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    first_registry = EpisodeRegistry(TacticalStore(paths), namespace="testnet")
    first = first_registry.assign(_candidate(), _structure())
    first_registry.mark_terminal(first.episode_id, "expired")

    restarted = EpisodeRegistry(TacticalStore(paths), namespace="testnet")
    repeated = restarted.assign(_candidate(1.02), _structure())

    assert repeated.episode_id == first.episode_id
    assert repeated.eligible is False
    assert repeated.terminal_reason == "expired"


def test_opposing_block_then_renewal_advances_epoch(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure())
    registry.mark_terminal(first.episode_id, "structure_invalidated")

    registry.observe(
        "WLD-USDT",
        "long",
        _structure(bias="bearish", token="break-down", block_long=True),
    )
    renewed = registry.assign(_candidate(0.99), _structure(token="break-up-1"))

    assert renewed.eligible is True
    assert renewed.reason == "eligible"
    assert renewed.episode_id != first.episode_id
    assert renewed.epoch_seq == first.epoch_seq + 1
    assert _latest_reset_evidence_reason(registry) == "opposing_block_then_renewed"


def test_neutral_then_renewed_direction_advances_epoch(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure())
    registry.mark_terminal(first.episode_id, "expired")
    registry.observe(
        "WLD-USDT",
        "long",
        _structure(bias="neutral", token="break-up-1"),
    )

    renewed = registry.assign(
        _candidate(),
        _structure(bias="bullish", token="break-up-1"),
    )

    assert renewed.eligible is True
    assert renewed.reason == "eligible"
    assert renewed.episode_id != first.episode_id
    assert _latest_reset_evidence_reason(registry) == "neutral_then_renewed"


def test_fresh_aligned_renewal_precedes_opposing_block_marker(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")
    registry.observe(
        "WLD-USDT",
        "long",
        _structure(bias="bearish", token="break-down-1", block_long=True),
    )

    renewed = registry.assign(
        _candidate(),
        {
            **_structure(bias="bullish", token="break-up-2"),
            "tf_15m_closed_bar_ts": 915.0,
        },
    )

    assert renewed.eligible is True
    assert renewed.reason == "eligible"
    assert renewed.episode_id != first.episode_id
    assert _latest_reset_evidence_reason(registry) == "new_confirmed_structure"


def test_fresh_aligned_renewal_precedes_neutral_seen_marker(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")
    registry.observe(
        "WLD-USDT",
        "long",
        _structure(bias="neutral", token="break-up-1"),
    )

    renewed = registry.assign(
        _candidate(),
        {
            **_structure(bias="bullish", token="break-up-2"),
            "tf_15m_closed_bar_ts": 915.0,
        },
    )

    assert renewed.eligible is True
    assert renewed.reason == "eligible"
    assert renewed.episode_id != first.episode_id
    assert _latest_reset_evidence_reason(registry) == "new_confirmed_structure"


def test_terminal_neutral_candidate_with_new_closed_bar_advances_epoch(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")

    renewed = registry.assign(
        _candidate(),
        {
            **_structure(bias="neutral", token="break-up-1"),
            "tf_15m_closed_bar_ts": 915.0,
        },
    )

    assert renewed.eligible is True
    assert renewed.reason == "new_confirmed_structure"
    assert renewed.episode_id != first.episode_id
    assert renewed.epoch_seq == first.epoch_seq + 1


@pytest.mark.parametrize("bias", ["bearish", "unavailable"])
def test_terminal_fresh_candidate_with_incompatible_bias_is_duplicate(
    tmp_path,
    bias,
):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")

    repeated = registry.assign(
        _candidate(),
        {
            **_structure(bias=bias, token="break-up-2"),
            "tf_15m_closed_bar_ts": 915.0,
        },
    )

    assert repeated.episode_id == first.episode_id
    assert repeated.eligible is False
    assert repeated.reason == "duplicate_episode"


def test_terminal_neutral_candidate_with_changed_token_advances_epoch(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")

    renewed = registry.assign(
        _candidate(),
        _structure(bias="neutral", token="break-up-2"),
    )

    assert renewed.eligible is True
    assert renewed.reason == "new_confirmed_structure"
    assert renewed.episode_id != first.episode_id


def test_terminal_neutral_candidate_with_older_bar_and_same_token_is_duplicate(
    tmp_path,
):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")

    repeated = registry.assign(
        _candidate(),
        {
            **_structure(bias="neutral", token="break-up-1"),
            "tf_15m_closed_bar_ts": 899.0,
        },
    )

    assert repeated.episode_id == first.episode_id
    assert repeated.eligible is False
    assert repeated.reason == "duplicate_episode"


def test_observed_newer_bar_rejects_stale_terminal_candidate(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")
    registry.observe(
        "WLD-USDT",
        "long",
        {
            **_structure(
                bias="bearish",
                token="break-down-1",
                block_long=True,
            ),
            "tf_15m_closed_bar_ts": 930.0,
        },
    )

    repeated = registry.assign(
        _candidate(),
        {
            **_structure(bias="neutral", token="break-up-1"),
            "tf_15m_closed_bar_ts": 915.0,
        },
    )

    assert repeated.episode_id == first.episode_id
    assert repeated.eligible is False
    assert repeated.reason == "duplicate_episode"


def test_observed_same_fresh_bar_can_renew_terminal_episode(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")
    structure = {
        **_structure(bias="neutral", token="break-up-1"),
        "tf_15m_closed_bar_ts": 915.0,
    }
    registry.observe("WLD-USDT", "long", structure)

    renewed = registry.assign(_candidate(), structure)

    assert renewed.eligible is True
    assert renewed.reason == "new_confirmed_structure"
    assert renewed.episode_id != first.episode_id


def test_terminal_neutral_fresh_renewal_survives_registry_restart(tmp_path):
    from utils.tactical_v2.episodes import EpisodeRegistry
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    registry = EpisodeRegistry(TacticalStore(paths), namespace="testnet")
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")

    restarted = EpisodeRegistry(TacticalStore(paths), namespace="testnet")
    renewed = restarted.assign(
        _candidate(),
        {
            **_structure(bias="neutral", token="break-up-1"),
            "tf_15m_closed_bar_ts": 915.0,
        },
    )

    assert renewed.eligible is True
    assert renewed.reason == "new_confirmed_structure"
    assert renewed.episode_id != first.episode_id
    assert renewed.epoch_seq == first.epoch_seq + 1


def test_terminal_neutral_candidate_with_same_evidence_is_duplicate(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")

    repeated = registry.assign(
        _candidate(),
        _structure(bias="neutral", token="break-up-1"),
    )

    assert repeated.episode_id == first.episode_id
    assert repeated.eligible is False
    assert repeated.reason == "duplicate_episode"


def test_numeric_and_string_structure_tokens_are_same_evidence(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token=1))
    registry.mark_terminal(first.episode_id, "expired")

    repeated = registry.assign(
        _candidate(),
        _structure(bias="neutral", token="1"),
    )

    assert repeated.episode_id == first.episode_id
    assert repeated.eligible is False
    assert repeated.reason == "duplicate_episode"


def test_structure_evidence_is_normalized_before_persistence(tmp_path):
    class StructureToken:
        def __str__(self):
            return "break-up-1"

    registry = _registry(tmp_path)
    assigned = registry.assign(
        _candidate(),
        {
            **_structure(token=StructureToken()),
            "tf_15m_closed_bar_ts": "900.0",
        },
    )

    event = registry.store.read_events()[0]
    state = event["data"]["registry_state"]
    assert assigned.eligible is True
    assert state["last_structure_token"] == "break-up-1"
    assert state["last_closed_bar_ts"] == 900.0
    assert state["max_observed_closed_bar_ts"] == 900.0


def test_non_finite_closed_bar_is_stored_as_missing(tmp_path):
    registry = _registry(tmp_path)

    assigned = registry.assign(
        _candidate(),
        {
            **_structure(),
            "tf_15m_closed_bar_ts": float("nan"),
        },
    )

    event = registry.store.read_events()[0]
    state = event["data"]["registry_state"]
    assert assigned.eligible is True
    assert state["last_closed_bar_ts"] is None
    assert state["max_observed_closed_bar_ts"] is None


def test_terminal_neutral_candidate_with_opposing_block_is_blocked(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.mark_terminal(first.episode_id, "expired")

    blocked = registry.assign(
        _candidate(),
        {
            **_structure(
                bias="neutral",
                token="break-up-1",
                block_long=True,
            ),
            "tf_15m_closed_bar_ts": 915.0,
        },
    )

    assert blocked.episode_id == first.episode_id
    assert blocked.eligible is False
    assert blocked.reason == "opposing_block"


def test_new_structure_token_resets_only_after_terminal(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure(token="break-up-1"))

    still_same = registry.assign(_candidate(), _structure(token="break-up-2"))
    registry.mark_terminal(first.episode_id, "capacity_skipped")
    reset = registry.assign(_candidate(), _structure(token="break-up-2"))

    assert still_same.episode_id == first.episode_id
    assert still_same.eligible is False
    assert reset.episode_id != first.episode_id
    assert reset.eligible is True
    assert reset.reason == "eligible"


def test_bearish_new_structure_token_renewal_keeps_eligible_reason(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(
        _candidate(side="short"),
        _structure(bias="bearish", token="break-down-1"),
    )
    registry.mark_terminal(first.episode_id, "expired")

    renewed = registry.assign(
        _candidate(side="short"),
        _structure(bias="bearish", token="break-down-2"),
    )

    assert renewed.eligible is True
    assert renewed.reason == "eligible"
    assert renewed.episode_id != first.episode_id


def test_missing_structure_never_manufactures_reset(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure())
    registry.mark_terminal(first.episode_id, "expired")

    unavailable = registry.assign(
        _candidate(),
        _structure(bias="unavailable", token=None, available=False),
    )

    assert unavailable.episode_id == first.episode_id
    assert unavailable.eligible is False


def test_reset_evidence_is_persisted_before_new_episode(tmp_path):
    registry = _registry(tmp_path)
    first = registry.assign(_candidate(), _structure())
    registry.mark_terminal(first.episode_id, "expired")
    registry.observe("WLD-USDT", "long", _structure(bias="neutral", token=None))
    registry.assign(_candidate(), _structure(token="break-up-2"))

    events = registry.store.read_events()
    reset_seq = next(
        row["seq"] for row in events if row["event_type"] == "episode_reset_evidence"
    )
    new_seq = max(
        row["seq"] for row in events if row["event_type"] == "episode_assigned"
    )

    assert reset_seq < new_seq


def test_failed_renewal_append_retry_matches_restart(tmp_path):
    from utils.tactical_v2.episodes import EpisodeRegistry
    from utils.tactical_v2.store import TacticalStore

    structure = {
        **_structure(bias="neutral", token="break-up-1"),
        "tf_15m_closed_bar_ts": 915.0,
    }

    def fail_then_retry(path, *, restart):
        registry = EpisodeRegistry(TacticalStore(_paths(path)), namespace="testnet")
        first = registry.assign(_candidate(), _structure(token="break-up-1"))
        registry.mark_terminal(first.episode_id, "expired")
        original_append = registry.store.append
        failed = False

        def fail_once(event_type, data, **kwargs):
            nonlocal failed
            state = data.get("registry_state", {})
            if (
                not failed
                and event_type == "episode_assigned"
                and state.get("epoch_seq") == 2
            ):
                failed = True
                raise OSError("simulated append failure")
            return original_append(event_type, data, **kwargs)

        registry.store.append = fail_once
        with pytest.raises(OSError, match="simulated append failure"):
            registry.assign(_candidate(), structure)
        registry.store.append = original_append
        if restart:
            registry = EpisodeRegistry(
                TacticalStore(_paths(path)),
                namespace="testnet",
            )
        return registry.assign(_candidate(), structure)

    same_process = fail_then_retry(tmp_path / "same-process", restart=False)
    after_restart = fail_then_retry(tmp_path / "restart", restart=True)

    assert same_process.eligible is True
    assert same_process.reason == "new_confirmed_structure"
    assert same_process == after_restart


def test_failed_observation_append_leaves_no_phantom_state(tmp_path):
    from utils.tactical_v2.episodes import EpisodeRegistry
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    registry = EpisodeRegistry(TacticalStore(paths), namespace="testnet")
    registry.assign(_candidate(), _structure())
    original_append = registry.store.append

    def fail_once(event_type, data, **kwargs):
        registry.store.append = original_append
        raise OSError("simulated observation append failure")

    registry.store.append = fail_once
    with pytest.raises(OSError, match="simulated observation append failure"):
        registry.observe(
            "WLD-USDT",
            "long",
            _structure(bias="neutral", token="break-up-1"),
        )

    restarted = EpisodeRegistry(TacticalStore(paths), namespace="testnet")
    assert registry._states == restarted._states
    assert registry._episode_states == restarted._episode_states
    assert registry._episode_keys == restarted._episode_keys


def test_historical_episode_can_terminal_without_replacing_current_epoch(tmp_path):
    from utils.tactical_v2.episodes import EpisodeRegistry
    from utils.tactical_v2.store import TacticalStore

    paths = _paths(tmp_path)
    registry = EpisodeRegistry(TacticalStore(paths), namespace="testnet")
    first = registry.assign(_candidate(), _structure(token="break-up-1"))
    registry.observe(
        "WLD-USDT",
        "long",
        _structure(bias="neutral", token=None),
    )
    renewed = registry.assign(
        _candidate(1.01),
        _structure(bias="bullish", token="break-up-2"),
    )

    registry.mark_terminal(first.episode_id, "tactical_tp1")
    registry.mark_terminal(first.episode_id, "tactical_tp1")
    repeated_current = registry.assign(
        _candidate(1.02),
        _structure(bias="bullish", token="break-up-2"),
    )

    assert registry.terminal_reason(first.episode_id) == "tactical_tp1"
    assert repeated_current.episode_id == renewed.episode_id
    assert repeated_current.eligible is False
    assert repeated_current.terminal_reason is None
    assert sum(
        row["event_type"] == "episode_terminal"
        and row["data"]["episode_id"] == first.episode_id
        for row in registry.store.read_events()
    ) == 1

    restarted = EpisodeRegistry(TacticalStore(paths), namespace="testnet")
    after_restart = restarted.assign(
        _candidate(1.03),
        _structure(bias="bullish", token="break-up-2"),
    )

    assert restarted.terminal_reason(first.episode_id) == "tactical_tp1"
    assert after_restart.episode_id == renewed.episode_id
    assert after_restart.eligible is False
    assert after_restart.terminal_reason is None
