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


if __name__ == "__main__":
    unittest.main()
