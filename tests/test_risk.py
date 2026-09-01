import unittest

from crypto_yolo.config import YoloConfig
from crypto_yolo.models import Position, SignalRow
from crypto_yolo.planner import plan_trades
from crypto_yolo.portfolio import build_targets
from crypto_yolo.risk import summarize_post_trade


class RiskTests(unittest.TestCase):
    def test_margin_gate_can_reject(self):
        cfg = YoloConfig(
            nominal_usd=100_000,
            account_collateral_usd=1_000,
            margin_required=0.10,
            max_margin_utilization=0.50,
            buffer_mode="target",
        )
        targets = build_targets([SignalRow("BTC", 50_000, 0.6, 0.6, 0.6, 0.5)], cfg)
        plan = plan_trades(targets, {}, cfg)
        risk = summarize_post_trade(plan, cfg)
        self.assertFalse(risk.approved)
        self.assertTrue(any("margin" in r for r in risk.reasons))


if __name__ == "__main__":
    unittest.main()
