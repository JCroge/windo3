# Shadow Tactical Live Sidecar Build Report

## Local Verification

- Focused pytest command: PASS
- Focused pytest result: `71 passed in 4.96s`
- OpenSpec strict validation: PASS
- Verify-fix note: default sidecar start does not backfill old shadow events; `--backfill-from-start` is explicit.

## Cloud Start Command

Run only after Main owner-ignore patch is deployed and the Main process is running the new code:

```bash
cd /opt/crypto-arbitrage
git pull --ff-only
export BOT_INSTANCE_ID=stlive
export SHADOW_TACTICAL_OWNER_REGISTRY=data/shadow_tactical_live_owners.json
nohup python3 scripts/shadow_tactical_live_sidecar.py run \
  --duration-hours 24 \
  --poll-seconds 2 \
  > logs/shadow_tactical_live_sidecar.log 2>&1 &
```

The sidecar defaults to no backfill on first start. Use `--backfill-from-start`
only for a deliberate replay test, not for this 24-hour live run.

## Status Command

```bash
cd /opt/crypto-arbitrage
python3 scripts/shadow_tactical_live_sidecar.py status
tail -n 100 logs/shadow_tactical_live_sidecar.log
```

## Stop Command

The stop path only closes exposure when the sidecar owner registry and sidecar
positions file prove the same `shadow_id`. Ambiguous positions are skipped and
must be inspected manually.

```bash
cd /opt/crypto-arbitrage
python3 scripts/shadow_tactical_live_sidecar.py stop
python3 scripts/shadow_tactical_live_sidecar.py status
```
