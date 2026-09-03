import unittest
from datetime import date
from unittest.mock import patch

from crypto_yolo.config import YoloConfig
from crypto_yolo.live import LiveInputs, wait_for_live_inputs
from crypto_yolo.validation import SignalValidationError


class SignalWaitTests(unittest.TestCase):
    def test_retries_stale_then_returns_current(self):
        cfg = YoloConfig(signal_poll_seconds=5, signal_wait_minutes=2)
        expected = date(2026, 9, 3)
        sentinel = LiveInputs([], expected, 123, None)  # type: ignore[arg-type]
        sleeps = []
        clock = iter([0.0, 0.0, 5.0])
        with patch(
            "crypto_yolo.live.fetch_live_inputs",
            side_effect=[SignalValidationError("stale RW signal date 2026-09-02; expected 2026-09-03"), sentinel],
        ):
            result = wait_for_live_inputs(
                cfg,
                expected_date=expected,
                sleep_fn=sleeps.append,
                monotonic_fn=lambda: next(clock),
                status_fn=lambda _: None,
            )
        self.assertIs(result, sentinel)
        self.assertEqual(sleeps, [5.0])

    def test_malformed_payload_fails_without_retry(self):
        cfg = YoloConfig(signal_poll_seconds=5, signal_wait_minutes=2)
        sleeps = []
        with patch(
            "crypto_yolo.live.fetch_live_inputs",
            side_effect=SignalValidationError("expected 10 YOLO assets, received 9"),
        ):
            with self.assertRaisesRegex(SignalValidationError, "expected 10"):
                wait_for_live_inputs(
                    cfg,
                    expected_date=date(2026, 9, 3),
                    sleep_fn=sleeps.append,
                    monotonic_fn=lambda: 0.0,
                    status_fn=lambda _: None,
                )
        self.assertEqual(sleeps, [])

    def test_timeout_blocks(self):
        cfg = YoloConfig(signal_poll_seconds=30, signal_wait_minutes=1)
        values = iter([0.0, 0.0, 30.0, 60.0])
        with patch(
            "crypto_yolo.live.fetch_live_inputs",
            side_effect=SignalValidationError("stale RW signal date 2026-09-02; expected 2026-09-03"),
        ):
            with self.assertRaisesRegex(SignalValidationError, "did not become current"):
                wait_for_live_inputs(
                    cfg,
                    expected_date=date(2026, 9, 3),
                    sleep_fn=lambda _: None,
                    monotonic_fn=lambda: next(values),
                    status_fn=lambda _: None,
                )
