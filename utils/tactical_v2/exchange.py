"""Live Tactical V2 exchange adapter and fail-closed protection handling."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ProtectionProof:
    complete: bool
    reason: str
    representation: str
    protected_qty: float
    tp_algo_ids: tuple[str, ...]
    sl_algo_ids: tuple[str, ...]


class LiveExchangeAdapter:
    """Coordinates narrow ContractExecutor primitives without strategy policy."""

    def __init__(self, *, executor: Any, store: Any = None, governor: Any = None):
        self.executor = executor
        self.store = store
        self.governor = governor

    async def submit_entry(self, intent: Any, *, order_type: str) -> dict:
        return await asyncio.to_thread(
            self.executor.submit_tactical_entry,
            intent,
            order_type=order_type,
        )

    async def query_entry(self, intent: Any) -> dict:
        raw = await asyncio.to_thread(self.executor.query_tactical_entry, intent)
        if isinstance(raw, dict) and raw.get("query_state") in {
            "found",
            "not_found",
            "query_error",
        }:
            return raw
        # Keep simple executors/test doubles compatible with the explicit contract.
        if raw is None:
            return {
                "query_state": "not_found",
                "observation": None,
                "successful_sources": ["legacy_adapter"],
                "errors": [],
            }
        if isinstance(raw, dict):
            return {
                "query_state": "found",
                "observation": raw,
                "successful_sources": ["legacy_adapter"],
                "errors": [],
            }
        return {
            "query_state": "query_error",
            "observation": None,
            "successful_sources": [],
            "errors": [{"source": "legacy_adapter", "error": "invalid result"}],
        }

    async def cancel_entry(self, intent: Any) -> dict:
        return await asyncio.to_thread(self.executor.cancel_tactical_entry, intent)

    async def query_position(self, intent: Any) -> Optional[dict]:
        symbol = self.executor._normalize_symbol(str(getattr(intent, "symbol", "")))
        return await asyncio.to_thread(
            self.executor._fetch_okx_position_state,
            symbol,
            raise_on_error=True,
        )

    async def cancel_protection(self, intent: Any) -> dict:
        return await asyncio.to_thread(self.executor.cancel_tactical_protection, intent)

    async def close_position(
        self,
        intent: Any,
        *,
        filled_qty: float,
        ownership_proof: str,
        reason: str,
    ) -> dict:
        return await asyncio.to_thread(
            self.executor.close_tactical_position,
            intent,
            filled_qty=filled_qty,
            ownership_proof=ownership_proof,
            reason=reason,
        )

    async def settle_fill(
        self,
        intent: Any,
        *,
        filled_qty: float,
        remaining_qty: float,
    ) -> ProtectionProof:
        effective_filled_qty = float(filled_qty)
        if remaining_qty > 0:
            cancel = await asyncio.to_thread(self.executor.cancel_tactical_entry, intent)
            if not cancel.get("proven"):
                proof = ProtectionProof(
                    False,
                    "entry_remainder_unproven",
                    "incomplete",
                    0.0,
                    (),
                    (),
                )
                await self._fail_closed(intent, filled_qty, proof)
                return proof
            cancel_fill = cancel.get("filled_qty")
            if cancel_fill is not None:
                effective_filled_qty = max(effective_filled_qty, float(cancel_fill))

        raw = await asyncio.to_thread(
            self.executor.verify_tactical_protection,
            intent,
            filled_qty=effective_filled_qty,
        )
        proof = ProtectionProof(
            complete=bool(raw.get("complete")),
            reason=str(raw.get("reason") or "unknown"),
            representation=str(raw.get("representation") or "incomplete"),
            protected_qty=float(raw.get("protected_qty") or 0),
            tp_algo_ids=tuple(str(value) for value in raw.get("tp_algo_ids") or ()),
            sl_algo_ids=tuple(str(value) for value in raw.get("sl_algo_ids") or ()),
        )
        if not proof.complete:
            await self._fail_closed(intent, effective_filled_qty, proof)
        return proof

    async def _fail_closed(
        self,
        intent: Any,
        filled_qty: float,
        proof: ProtectionProof,
    ) -> None:
        evidence = {
            "intent_id": intent.intent_id,
            "episode_id": intent.episode_id,
            "reason": proof.reason,
            "filled_qty": float(filled_qty),
            "tp_algo_ids": list(proof.tp_algo_ids),
            "sl_algo_ids": list(proof.sl_algo_ids),
        }
        if self.store is not None:
            self.store.append("protection_integrity_failed", evidence)
        if self.governor is not None:
            self.governor.activate_integrity_halt(
                "tactical_protection_incomplete",
                evidence=evidence,
            )
        cleanup_errors = []
        try:
            await asyncio.to_thread(self.executor.cancel_tactical_protection, intent)
        except Exception as exc:
            cleanup_errors.append({"operation": "cancel_protection", "error": str(exc)})
        ownership = self.executor.make_tactical_clord_id(intent.intent_id, "entry")
        try:
            await asyncio.to_thread(
                self.executor.close_tactical_position,
                intent,
                filled_qty=filled_qty,
                ownership_proof=ownership,
                reason="risk_forced:protection_integrity",
            )
        except Exception as exc:
            cleanup_errors.append({"operation": "safe_close", "error": str(exc)})
        if cleanup_errors and self.store is not None:
            self.store.append(
                "protection_cleanup_failed",
                {**evidence, "cleanup_errors": cleanup_errors},
            )
