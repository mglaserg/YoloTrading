import unittest

from crypto_yolo.config import YoloConfig
from crypto_yolo.models import Position, SignalRow
from crypto_yolo.planner import plan_trades
from crypto_yolo.portfolio import build_targets


class UniverseExitTests(unittest.TestCase):
    def test_position_that_leaves_rw_universe_is_closed(self):
        cfg = YoloConfig(nominal_usd=100_000, close_non_universe_positions=True)
        targets = build_targets([SignalRow("BTC", 100_000, 0.1, 0.1, 0.1, 1.0)], cfg)
        plan = plan_trades(
            targets,
            {"DOGE": Position("DOGE", 1000, 0.2)},
            cfg,
            mark_prices={"BTC": 100_000, "DOGE": 0.2},
        )
        doge = next(row for row in plan if row.ticker == "DOGE")
        self.assertTrue(doge.is_universe_exit)
        self.assertEqual(doge.target_weight, 0)
        self.assertEqual(doge.trade_quantity, -1000)


if __name__ == "__main__":
    unittest.main()
