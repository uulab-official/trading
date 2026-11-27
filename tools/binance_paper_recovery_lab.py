"""Synthetic two-week clock-driven paper recovery drill with the actual Rust binary.

Run only in a dedicated temporary directory. No network or broker operations.
"""

import argparse
import json
import tempfile
import time
from pathlib import Path

from tools import binance_paper_forward_supervisor as supervisor
from tools import binance_paper_ops as ops
from tools import binance_paper_recovery as recovery
from tools import binance_public_archive as archive

ROOT = Path(__file__).resolve().parents[1]
STEP = 3_600_000
START = STEP * 500_000
BARS = 14 * 24 + 3


class SyntheticFeed:
    def __init__(self, scenario):
        self.bars = scenario["bars"]
        self.count = 3

    def clock(self):
        return START + self.count * STEP + archive.CLOSE_GRACE_MS

    def fetch(self, route, params):
        if route == "time":
            return {"serverTime": self.clock()}
        assert route == "klines"
        assert params["symbol"] == "DEMOUSDT" and params["interval"] == "1h"
        first = (params["startTime"] - START) // STEP
        result = []
        for index in range(first, first + params["limit"]):
            assert 0 <= index < self.count
            bar = self.bars[index]
            stamp = bar["open_ms"]
            result.append(
                [
                    stamp,
                    bar["open"],
                    bar["high"],
                    bar["low"],
                    bar["close"],
                    bar["volume"],
                    stamp + STEP - 1,
                    "0",
                    1,
                    "0",
                    "0",
                    "0",
                ]
            )
        return result


def run(binary):
    started = time.monotonic()
    fixture = ROOT / "apps/server/config/paper_forward_example.json"
    scenario = json.loads(fixture.read_text(encoding="utf-8"))["scenario"]
    scenario["timeframe"] = "1h"
    originals = scenario["bars"]
    bars = []
    for index in range(BARS):
        item = originals[index % len(originals)]
        bars.append({**item, "open_ms": START + index * STEP})
    scenario["bars"] = bars
    feed = SyntheticFeed(scenario)
    with tempfile.TemporaryDirectory(prefix="paper-recovery-lab-") as temp:
        root = Path(temp)
        path = root / "archive.sqlite"
        template = root / "template.json"
        accounts = root / "accounts"
        accounts.mkdir()
        recovery.write_json(template, {**scenario, "bars": []})
        archive.create(path, "DEMOUSDT", "1h", START)
        session = supervisor.make_session(path, accounts, binary, template, START, 3)
        previous = None
        for count in (3, 100, 200, BARS):
            feed.count = count
            assert archive.sync_once(path, fetch=feed.fetch, clock=feed.clock) == "caught_up"
            with supervisor.owner(session):
                state = supervisor.cycle(session, initialize=previous is None, clock=feed.clock)
            assert state["status"] == "paper_current", state["reason"]
            report = state["last_report"]
            assert report["input_bar_count"] == count
            if previous is not None:
                assert report["session_sha256"] == previous["session_sha256"]
                for old, new in zip(previous["accounts"], report["accounts"]):
                    assert (
                        old["checkpoint"]["head"]["sequence"]
                        < new["checkpoint"]["head"]["sequence"]
                    )
            previous = report

        audit = ops.collect(session, clock=feed.clock, deep_audit=True)
        assert audit["verification"] == "independently_replayed_at_request", audit["status"]
        assert audit["live_orders_enabled"] is False
        bundle = root / "backup.zip"
        receipt = recovery.capture(session, bundle)
        digest = receipt["bundle_sha256"]
        assert recovery.verify(bundle, digest, binary)["report_sha256"] == receipt["report_sha256"]
        restored_dir = root / "restored"
        result = recovery.restore(bundle, digest, binary, restored_dir, clock=feed.clock)
        assert result["operator_review_required"]
        restored = recovery.session_at(restored_dir, binary, START, 3)
        state = supervisor.read_state(restored)
        assert state["status"] == "halted" and state["last_report"] == previous
        assert restored.stop_path.is_file() and archive.stop_requested(restored.archive_path)
        with supervisor.owner(restored):
            assert supervisor.cycle(restored, clock=feed.clock) == state
        # Explicitly reviewed paper-only resume preserves economic history.
        restored.stop_path.unlink()
        Path(f"{restored.archive_path}.STOP").unlink()
        reviewed = recovery.review_resume(restored, receipt["report_sha256"], clock=feed.clock)
        assert reviewed["status"] == "stopped" and reviewed["last_report"] == previous
        with supervisor.owner(restored):
            replayed = supervisor.cycle(restored, clock=feed.clock)
        assert replayed["status"] == "paper_current" and replayed["last_report"] == previous
        bad = root / "tampered.zip"
        recovery.checked_copy(bundle, bad, recovery.MAX_BUNDLE)
        with bad.open("ab") as handle:
            handle.write(b"tampered")
        try:
            recovery.restore(bad, digest, binary, root / "bad-restore", clock=feed.clock)
        except supervisor.SupervisorError:
            assert not (root / "bad-restore").exists()
        else:
            raise AssertionError("Tampered bundle accepted")
        return {
            "mode": "synthetic_paper_recovery_lab",
            "live_orders_enabled": False,
            "bars": BARS,
            "simulated_interval": "1h",
            "session_sha256": previous["session_sha256"],
            "accounts": previous["accounts"],
            "checks": [
                "four_staged_captures",
                "exact_rust_replay",
                "deep_operator_audit",
                "verified_bundle",
                "stopped_restore",
                "reviewed_resume",
                "tamper_refusal",
            ],
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    args = parser.parse_args()
    print(archive.canonical(run(args.binary.resolve(strict=True))))


if __name__ == "__main__":
    main()
