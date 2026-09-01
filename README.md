# Crypto YOLO Trading

Implementation scaffold for the Robot Wealth-style Crypto YOLO strategy: momentum + trend + carry, volatility scaled and traded with a buffer.

## V1 reference logic

This repository intentionally reproduces the portfolio-construction logic visible in **YOLO Trade Helper v8** before adding discretionary enhancements.

For each asset:

1. Combine the three megafactors:

   `raw_weight = (momentum * M + trend * T + carry * C) / 3`

2. Inverse-volatility scale:

   `vol_scaled = raw_weight / ewvol`

3. Clip each asset to `+/- 25%` by default.

4. If the sum of absolute weights exceeds `100%`, scale the entire portfolio proportionally back to `100%` gross.

5. Multiply the final weights by `YOLO_NOMINAL_USD` to obtain target dollar notionals.

The RW helper identifies the relevant endpoints as:

- `yolo/weights`
- `yolo/volatilities`

and fields including:

- `ticker`
- `arrival_price`
- `momentum_megafactor`
- `trend_megafactor`
- `carry_megafactor`
- `combo_weight`
- `date`
- `ewvol`

## Trade buffer

The reference spreadsheet uses a **relative** buffer:

`abs(current_weight - target_weight) < buffer * abs(target_weight)`

Two execution modes are implemented:

- `target`: spreadsheet-compatible; when breached, trade fully to target.
- `edge`: preferred live mode; when breached, trade only to the nearest edge of the no-trade region.

Default is `edge`.

## Safety posture

This version is a **dry-run portfolio and trade planner**. It does not submit orders.

Before live trading, the next milestones are:

1. Wire authenticated RW API transport and validate the real payload shape.
2. Wire Hyperliquid read-only account/subaccount state.
3. Match real current positions to YOLO targets and test rounding/minimum sizes.
4. Add Hyperliquid testnet order execution and fill reconciliation.
5. Only then enable explicitly gated live execution.

## Run

No third-party packages are required for the current tests/preview.

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python -m crypto_yolo.cli --fixture examples/sample_snapshot.json
```

To reproduce spreadsheet-style trade-to-target behavior:

```bash
PYTHONPATH=src python -m crypto_yolo.cli --fixture examples/sample_snapshot.json --buffer-mode target
```

Copy `.env.example` into your preferred secret-management workflow before connecting real providers. Never commit API keys or private keys.
