import unittest

from crypto_yolo.config import YoloConfig
from crypto_yolo.models import SignalRow
from crypto_yolo.planner import plan_trades
from crypto_yolo.portfolio import build_targets


class PrecisionTests(unittest.TestCase):
    def test_trade_quantity_respects_exchange_size_decimals(self):
        cfg = YoloConfig(nominal_usd=10_000, buffer_mode="target")
        targets = build_targets([SignalRow("BTC", 100_000, 0.03, 0.03, 0.03, 1.0)], cfg)
        plan = plan_trades(
            targets,
            {},
            cfg,
            mark_prices={"BTC": 100_000},
            size_decimals={"BTC": 5},
        )
        rendered = f"{plan[0].trade_quantity:.8f}"
        self.assertTrue(rendered.endswith("000"))
        self.assertEqual(plan[0].trade_quantity, round(plan[0].trade_quantity, 5))

    def test_planner_trades_to_absolute_buffer_edge(self):
        from crypto_yolo.models import Position, TargetRow

        cfg = YoloConfig(nominal_usd=10_000, trade_buffer=0.02, buffer_mode="edge")
        target = TargetRow("BTC", 100.0, 0, 0, 0, 1.0, 0.10, 0.10, 0.10, 1000.0, 10.0)
        plan = plan_trades(
            [target],
            {"BTC": Position("BTC", 5.0, 100.0)},
            cfg,
            mark_prices={"BTC": 100.0},
            size_decimals={"BTC": 2},
        )
        self.assertAlmostEqual(plan[0].post_trade_weight, 0.09)
        self.assertAlmostEqual(plan[0].trade_quantity, 4.0)



if __name__ == "__main__":
    unittest.main()
