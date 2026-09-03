import unittest

from crypto_yolo.models import Position, TargetRow
from crypto_yolo.reconciliation import verify_post_trade_state


class ReconciliationTests(unittest.TestCase):
    def test_verifies_actual_position_against_buffer(self):
        target = TargetRow("BTC", 100.0, 0, 0, 0, 1, 0.2, 0.2, 0.2, 2000, 20)
        rows = verify_post_trade_state(
            targets=[target], positions={"BTC": Position("BTC", 19.8, 100)}, mark_prices={"BTC": 100},
            nominal_usd=10_000, buffer=0.02,
        )
        self.assertTrue(rows[0].within_buffer)


if __name__ == "__main__":
    unittest.main()
