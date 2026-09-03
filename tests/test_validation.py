from datetime import date
import unittest

from crypto_yolo.models import SignalRow
from crypto_yolo.validation import SignalValidationError, validate_signals


def row(i: int, d: str = "2026-09-03") -> SignalRow:
    return SignalRow(f"C{i}", 100 + i, 0.1, 0.2, -0.1, 0.5, d)


class ValidationTests(unittest.TestCase):
    def test_current_complete_snapshot_passes(self):
        result = validate_signals(
            [row(i) for i in range(10)],
            expected_date=date(2026, 9, 3),
            expected_universe_size=10,
        )
        self.assertEqual(result, date(2026, 9, 3))

    def test_stale_snapshot_is_blocked(self):
        with self.assertRaisesRegex(SignalValidationError, "stale"):
            validate_signals(
                [row(i, "2026-09-02") for i in range(10)],
                expected_date=date(2026, 9, 3),
                expected_universe_size=10,
            )

    def test_nonpositive_vol_is_blocked(self):
        rows = [row(i) for i in range(10)]
        rows[2] = SignalRow("C2", 102, 0.1, 0.2, -0.1, 0.0, "2026-09-03")
        with self.assertRaisesRegex(SignalValidationError, "ewvol"):
            validate_signals(rows, expected_date=date(2026, 9, 3), expected_universe_size=10)


if __name__ == "__main__":
    unittest.main()
