from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from crypto_yolo.archive import SignalArchive
from crypto_yolo.config import YoloConfig
from crypto_yolo.live import fetch_live_inputs
from crypto_yolo.models import RawApiResponse, SignalRow
from crypto_yolo.validation import SignalValidationError


class FakeRW:
    def __init__(self, *args, **kwargs):
        pass

    def fetch_weights(self):
        payload = {"data": [{"ticker": "BTCUSDT", "date": "2026-09-02"}]}
        return RawApiResponse(
            "yolo/weights",
            "https://api.robotwealth.com/v1/yolo/weights",
            datetime(2026, 9, 3, 9, 2, tzinfo=timezone.utc),
            200,
            '{"data":[{"ticker":"BTCUSDT","date":"2026-09-02"}]}',
            payload,
        )

    def fetch_volatilities(self):
        payload = {"data": [{"ticker": "BTCUSDT", "ewvol": 0.5}]}
        return RawApiResponse(
            "yolo/volatilities",
            "https://api.robotwealth.com/v1/yolo/volatilities",
            datetime(2026, 9, 3, 9, 2, tzinfo=timezone.utc),
            200,
            '{"data":[{"ticker":"BTCUSDT","ewvol":0.5}]}',
            payload,
        )

    @staticmethod
    def parse_signals(weights_payload, vol_payload):
        return [
            SignalRow(
                ticker=f"C{i}",
                price=100 + i,
                momentum=0.1,
                trend=0.2,
                carry=0.3,
                ewvol=0.5,
                date="2026-09-02",
            )
            for i in range(10)
        ]


class LiveArchiveGuardTests(unittest.TestCase):
    def test_stale_payload_is_archived_before_trade_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = replace(
                YoloConfig(),
                data_dir=tmp,
                rw_api_key="fake",
                hl_account_address="0x0000000000000000000000000000000000000000",
            )
            with patch("crypto_yolo.live.RobotWealthClient", FakeRW):
                with self.assertRaisesRegex(SignalValidationError, "stale"):
                    fetch_live_inputs(cfg, expected_date=date(2026, 9, 3))

            pulls = SignalArchive(Path(tmp) / "yolo.sqlite").recent_pulls()
            self.assertEqual(len(pulls), 2)
            self.assertTrue(all(row["validation_status"] == "rejected" for row in pulls))
            self.assertTrue(any("2026-09-02" in row["raw_text"] for row in pulls))


if __name__ == "__main__":
    unittest.main()
