import unittest

from crypto_yolo.models import Position, TargetRow
from crypto_yolo.reconciliation import verify_post_trade_state


class ReconciliationTests(unittest.TestCase):
    def test_verifies_actual_position_against_buffer(self):
        target = TargetRow("BTC", 100.0, 0, 0, 0, 1, 0.2, 0.2, 0.2, 2000, 20)
        rows = verify_post_trade_state(
            targets=[target], positions={"BTC": Position("BTC", 19.0, 100)}, mark_prices={"BTC": 100},
            nominal_usd=10_000, buffer=0.02,
        )
        self.assertTrue(rows[0].within_buffer)

    def test_zero_target_requires_flat_position(self):
        target = TargetRow("BTC", 100.0, 0, 0, 0, 1, 0, 0, 0, 0, 0)
        rows = verify_post_trade_state(
            targets=[target], positions={"BTC": Position("BTC", 0.1, 100)}, mark_prices={"BTC": 100},
            nominal_usd=10_000, buffer=0.10,
        )
        self.assertFalse(rows[0].within_buffer)


if __name__ == "__main__":
    unittest.main()
