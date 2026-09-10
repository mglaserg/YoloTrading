import unittest

from crypto_yolo.config import YoloConfig
from crypto_yolo.models import SignalRow
from crypto_yolo.portfolio import build_targets


class PortfolioTests(unittest.TestCase):
    def test_spreadsheet_formula_and_asset_clip(self):
        cfg = YoloConfig(nominal_usd=100_000)
        rows = [SignalRow("BTC", 50_000, 0.6, 0.6, 0.6, 0.5)]
        target = build_targets(rows, cfg)[0]
        self.assertAlmostEqual(target.raw_weight, 0.6)
        self.assertAlmostEqual(target.vol_scaled_weight, 0.25)
        self.assertAlmostEqual(target.final_weight, 0.25)
        self.assertAlmostEqual(target.target_value_usd, 25_000)

    def test_gross_normalization(self):
        cfg = YoloConfig(max_asset_weight=0.25, max_gross_weight=1.0)
        rows = [SignalRow(str(i), 1, 1, 1, 1, 0.1) for i in range(5)]
        targets = build_targets(rows, cfg)
        self.assertAlmostEqual(sum(abs(t.final_weight) for t in targets), 1.0)
        self.assertTrue(all(abs(t.final_weight) <= 0.25 for t in targets))

    def test_long_only_zeros_negative_weights_without_rescaling_cash(self):
        cfg = YoloConfig(
            nominal_usd=1000,
            direction_mode="long_only",
            max_asset_weight=0.25,
            max_gross_weight=1.0,
        )
        rows = [
            SignalRow("BTC", 100.0, 0.03, 0.03, 0.03, 0.10),
            SignalRow("ETH", 100.0, -0.03, -0.03, -0.03, 0.10),
        ]
        targets = build_targets(rows, cfg)
        self.assertAlmostEqual(targets[0].final_weight, 0.25)
        self.assertAlmostEqual(targets[1].final_weight, 0.0)
        self.assertAlmostEqual(sum(t.final_weight for t in targets), 0.25)


if __name__ == "__main__":
    unittest.main()
