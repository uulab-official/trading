"""Review a public-candle recovery candidate against immutable paper history.

No automatic promotion or live orders. Validate exact archived prefix and
independently replay the existing paper ledger against the expanded candidate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for source in (ROOT, ROOT / "packages/core", ROOT / "packages/data",
               ROOT / "packages/strategy", ROOT / "apps/server"):
    sys.path.insert(0, str(source))

from tools.paper_checkpoint_audit import audit
from tools.upbit_paper_archive import _validated_rows
from trading_bot.paper_checkpoint import locked_checkpoint


def review_candidate(archive: Path, checkpoint: Path, candidate: Path):
    archive, checkpoint, candidate = (Path(x).resolve() for x in (archive, checkpoint, candidate))
    if len({archive, checkpoint, candidate}) != 3:
        raise ValueError("Original archive, checkpoint and candidate must be distinct")
    if not all(path.is_file() for path in (archive, checkpoint, candidate)):
        raise ValueError("All three existing paper inputs are required")
    # The cycle's owner lock serializes its archive append and paper replay.
    # Acquire the same cycle lock for the entire read-only promotion review.
    lock_target = checkpoint.with_name("." + checkpoint.name + ".cycle")
    with locked_checkpoint(lock_target):
        with locked_checkpoint(archive):
            with locked_checkpoint(candidate):
                baseline = json.loads(archive.read_text(encoding="utf-8"))
                proposed = json.loads(candidate.read_text(encoding="utf-8"))
                original, old_duration = _validated_rows(baseline)
                future, new_duration = _validated_rows(proposed)
                if (baseline.get("mode") != "paper"
                        or proposed.get("mode") != "paper"
                        or old_duration != new_duration
                        or baseline.get("symbol") != proposed.get("symbol")
                        or baseline.get("timeframe") != proposed.get("timeframe")):
                    raise ValueError("Paper archive and candidate identities differ")
                if len(future) <= len(original):
                    raise ValueError("Candidate must contain additional completed bars")
                if future[:len(original)] != original:
                    raise ValueError("Candidate has modified historical OHLCV")
                for first, second in zip(future, future[1:]):
                    if second.timestamp - first.timestamp != new_duration:
                        raise ValueError("Candidate cannot include any missing interval")
                review = proposed.get("backfill_review")
                if (not isinstance(review, dict)
                        or review.get("manual_promotion_required") is not True
                        or review.get("live_orders_enabled") is not False
                        or review.get("appended_bars") != len(future) - len(original)):
                    raise ValueError("Candidate is missing an explicit manual-review marker")
                # A new history is never safe merely because its SHA matches.
                # Auditing the checkpoint against BOTH histories verifies
                # original processed bars, pending signal, risk and cash/holdings.
                old_audit = audit(archive, checkpoint)
                new_audit = audit(candidate, checkpoint)
                if (old_audit["checkpoint_sha256"] != new_audit["checkpoint_sha256"]
                        or old_audit["processed_bars"] != new_audit["processed_bars"]
                        or new_audit["symbol"] != baseline["symbol"]
                        or new_audit["timeframe"] != baseline["timeframe"]):
                    raise ValueError("Candidate cannot resume existing paper state")
                return {
                    "status": "candidate_verified_for_manual_review",
                    "mode": "paper",
                    "live_orders_enabled": False,
                    "promotion_performed": False,
                    "new_bars": len(future) - len(original),
                    "verified_checkpoint_bars": old_audit["processed_bars"],
                    "candidate_sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                    "checkpoint_sha256": old_audit["checkpoint_sha256"],
                    "operator_review_required": True,
                    "warning": "Not an authorization for real orders or unattended archive promotion",
                }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = review_candidate(args.archive, args.checkpoint, args.candidate)
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError, RuntimeError):
        print("Paper recovery candidate rejected. Original state was not modified.", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
