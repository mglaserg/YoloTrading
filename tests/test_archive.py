import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from crypto_yolo.archive import SignalArchive
from crypto_yolo.models import RawApiResponse, SignalRow


class ArchiveTests(unittest.TestCase):
    def test_raw_pull_and_signal_snapshot_are_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = SignalArchive(Path(tmp) / "yolo.sqlite")
            payload = {"data": [{"ticker": "BTCUSDT", "date": "2026-09-03"}]}
            raw = json.dumps(payload, separators=(",", ":"))
            response = RawApiResponse(
                endpoint="yolo/weights",
                url="https://api.robotwealth.com/v1/yolo/weights",
                pulled_at_utc=datetime(2026, 9, 3, 9, 1, tzinfo=timezone.utc),
                status_code=200,
                raw_text=raw,
                payload=payload,
            )
            pull_id = archive.record_pull(response, "2026-09-03")
            archive.mark_pull_validation(pull_id, "accepted")
            snapshot_id = archive.record_signal_snapshot(
                pulled_at_utc=response.pulled_at_utc.isoformat(),
                signal_date="2026-09-03",
                signals=[SignalRow("BTC", 100_000, 0.1, 0.2, 0.3, 0.5, "2026-09-03")],
                weights_pull_id=pull_id,
                volatilities_pull_id=None,
                validation_status="accepted",
            )
            archive.mark_snapshot_planned(snapshot_id)
            rows = archive.recent_pulls()
            self.assertEqual(rows[0]["endpoint"], "yolo/weights")
            self.assertEqual(rows[0]["raw_text"], raw)
            self.assertEqual(rows[0]["validation_status"], "accepted")
            self.assertEqual(rows[0]["used_for_trade_plan"], 1)
            self.assertEqual(rows[0]["used_for_rebalance"], 0)
            self.assertNotIn("api_key", rows[0]["url"])


if __name__ == "__main__":
    unittest.main()
