# Crypto YOLO Trading

A Python implementation of the Robot Wealth-style Crypto YOLO strategy: **momentum + trend + carry**, inverse-volatility scaled, buffer-aware, and designed for Hyperliquid execution.

Version 0.4 adds **cash-flow-adjusted compounding** to the live-data dry-run stack. It can consume the real Robot Wealth YOLO endpoints, persist every vendor pull locally, reject stale/malformed signals, read Hyperliquid positions/marks/margin state, calculate the exact dry-run trade plan, and optionally scale the nominal allocation with unitized YOLO NAV. It still **cannot submit orders**.

## Production portfolio logic

The production baseline intentionally stays simple and close to the Robot Wealth trade helper.

For each asset:

1. Combine the megafactors:

   `raw_weight = (momentum * M + trend * T + carry * C) / 3`

2. Inverse-volatility scale using **Robot Wealth supplied `ewvol`**:

   `vol_scaled = raw_weight / ewvol`

3. Clip each asset to `+/- 25%` by default.

4. If gross exposure exceeds `100%`, proportionally scale the full portfolio back to `100%` gross.

5. Multiply final weights by the **effective nominal allocation** to obtain target dollar notionals. In fixed mode this is `YOLO_NOMINAL_USD`; in compound mode it is the base nominal multiplied by cash-flow-adjusted YOLO NAV performance.

No covariance/ERC estimator is in the production path. ERC remains a possible research comparison later.


## Compound sizing

YOLO now supports two sizing modes:

- `fixed` — `YOLO_NOMINAL_USD` remains constant.
- `compound` — `YOLO_NOMINAL_USD` is the **base nominal**, and the live planner scales it by YOLO's unitized NAV performance.

The compounding formula is effectively:

`effective_nominal = base_nominal * NAV_per_unit / initial_NAV_per_unit`

The first compound live-data run initializes `NAV_per_unit = 1.0`. If the dedicated YOLO subaccount subsequently gains 10%, the nominal allocation also rises 10%. If it loses 10%, nominal falls 10%. This changes only the dollar scale; the Robot Wealth factor weights, inverse-vol logic, caps, gross constraint, and buffer are unchanged.

### Deposits and withdrawals do not count as returns

The sizing ledger is **unitized**, similar to a fund NAV. External cash flows change the number of strategy units rather than NAV-per-unit. This prevents a deposit from looking like profit or a withdrawal from looking like a loss.

After a deposit or withdrawal has posted to the YOLO Hyperliquid subaccount, record it immediately:

```powershell
python -m crypto_yolo.cli --record-flow 10000
python -m crypto_yolo.cli --record-flow -5000
```

Positive values are deposits; negative values are withdrawals. The command reads the current Hyperliquid equity and issues/redeems strategy units at the inferred pre-flow NAV-per-unit.

A dedicated YOLO subaccount is required by default in compound mode. This prevents P&L from another strategy in a shared account from changing YOLO's sizing. The guard can be disabled explicitly, but that is not recommended.

### Compounding guardrails

The raw performance multiplier is clipped before it reaches position sizing:

```text
YOLO_MIN_NOMINAL_MULTIPLIER=0.25
YOLO_MAX_NOMINAL_MULTIPLIER=3.00
```

For a $50,000 base nominal, those defaults bound the effective nominal between $12,500 and $150,000 even if the raw unitized NAV multiplier moves outside that range. All normal margin/risk checks still run afterward.

If you intentionally change the base nominal or want to establish a fresh compounding inception point, rebase explicitly:

```powershell
python -m crypto_yolo.cli --rebase-compounding
```

You can inspect the current unitized state at any time:

```powershell
python -m crypto_yolo.cli --sizing-status
```

Sizing decisions and cash-flow events are persisted in the same local `state/yolo.sqlite` audit database.

## Robot Wealth live inputs

The client uses:

- `https://api.robotwealth.com/v1/yolo/weights`
- `https://api.robotwealth.com/v1/yolo/volatilities`

and joins the current point-in-time fields including:

- `ticker`
- `arrival_price`
- `date`
- `momentum_megafactor`
- `trend_megafactor`
- `carry_megafactor`
- `combo_weight`
- `ewvol`

`arrival_price` is retained for future transaction-cost analysis. Hyperliquid mark prices are used to value the current account and calculate executable quantities.

## Immutable signal archive

Every received Robot Wealth response is written to `state/yolo.sqlite` **before portfolio construction and before staleness validation**.

The archive keeps:

- UTC pull timestamp
- endpoint
- HTTP status
- vendor payload date when available
- SHA-256 hash of the raw response
- complete raw response body
- validation status and rejection reason
- whether the payload was used to construct a trade plan
- a separate reserved flag for whether it was actually used in a future live rebalance
- normalized joined YOLO signal rows

This means even a stale or malformed vendor response remains available for later diagnosis.

The database is deliberately local and ignored by Git.

## Staleness / fail-closed guard

The live planner refuses to proceed if any of these checks fail:

- payload date is not the expected current UTC date
- YOLO universe is not the configured size (default: 10)
- duplicate tickers
- weights and volatility payloads do not match by ticker
- weights/volatility dates conflict when both are supplied
- missing/non-positive `arrival_price`
- missing/non-positive `ewvol`
- non-finite factor values
- non-200 RW response

A rejected payload is still archived first.

For historical/replay plumbing tests, `--expected-date YYYY-MM-DD` can override the expected date.

## Hyperliquid read-only integration

The live planner calls Hyperliquid's public `/info` endpoint for:

- `clearinghouseState`
- `metaAndAssetCtxs`

This gives the planner:

- current positions
- mark prices
- account value
- notional exposure
- margin used
- withdrawable collateral
- per-asset quantity precision (`szDecimals`)

No private key is required for this version.

A dedicated YOLO Hyperliquid subaccount is preferred. If `HL_YOLO_SUBACCOUNT_ADDRESS` is set, that address is used instead of the master account address.

## Trade buffer

Default buffer is now `2%`.

The current implementation retains the relative trade-helper form:

`abs(current_weight - target_weight) <= buffer * abs(target_weight)`

Two modes are available:

- `edge` — preferred: when breached, trade only back to the nearest buffer edge.
- `target` — when breached, trade all the way to target.

Hyperliquid quantity precision is applied to the dry-run order quantity.

## Universe changes

By default, an existing Hyperliquid position that is no longer present in the current RW YOLO universe receives a controlled target of zero.

Set:

`YOLO_CLOSE_NON_UNIVERSE_POSITIONS=false`

only if the account/subaccount intentionally contains positions belonging to another strategy.

This is one reason a dedicated YOLO subaccount is strongly preferred.

## Setup on Windows

This repository uses a packaged `src/` layout. Install it once in editable mode so `crypto_yolo` is importable from normal Python commands:

```powershell
python -m pip install -e .
```

If you prefer `uv` for package scaffolding/install commands without creating a project virtual environment:

```powershell
uv pip install --system -e .
```

Then:

```powershell
python -m unittest discover -s tests -v
```

## Configuration

Copy `.env.example` to `.env` and fill in only your own values:

```powershell
Copy-Item .env.example .env
```

The CLI loads this simple `.env` automatically. Existing process environment variables override values in the file.

Important live-data settings:

```text
RW_API_KEY=
HL_ACCOUNT_ADDRESS=
HL_YOLO_SUBACCOUNT_ADDRESS=

YOLO_NOMINAL_USD=50000
YOLO_SIZING_MODE=compound
YOLO_MIN_NOMINAL_MULTIPLIER=0.25
YOLO_MAX_NOMINAL_MULTIPLIER=3.00
YOLO_REQUIRE_DEDICATED_SUBACCOUNT_FOR_COMPOUND=true
YOLO_TRADE_BUFFER=0.02
YOLO_BUFFER_MODE=edge
YOLO_MAX_ASSET_WEIGHT=0.25
YOLO_MAX_GROSS_WEIGHT=1.0
YOLO_MAX_MARGIN_UTILIZATION=0.60

DRY_RUN=true
HYPERLIQUID_TESTNET=true
```

`YOLO_NOMINAL_USD` remains the intuitive strategy sizing knob. In `fixed` mode it is the actual nominal; in `compound` mode it is the inception/base nominal that compounds with unitized YOLO performance. Exchange collateral is not the same thing as strategy nominal allocation.

## Fixture preview

```powershell
python -m crypto_yolo.cli --fixture examples/sample_snapshot.json
```

## Real live-data dry run

Once `RW_API_KEY` and the Hyperliquid account/subaccount address are configured:

```powershell
python -m crypto_yolo.cli --live-data
```

The sequence is:

```text
RW yolo/weights
RW yolo/volatilities
        ↓
archive both raw payloads
        ↓
staleness + completeness gate
        ↓
join factors + supplied ewvol
        ↓
inverse-vol YOLO targets
        ↓
Hyperliquid marks + positions + margin state
        ↓
unitized NAV → effective nominal (compound mode)
        ↓
2% trade buffer
        ↓
exchange-precision trade quantities
        ↓
risk summary
        ↓
DRY-RUN trade plan
```

This version always stops there. **No order API exists in the code yet.**

## Inspect the local RW archive

```powershell
python -m crypto_yolo.cli --archive-status
```

This shows recent endpoint pulls, dates, HTTP codes, and whether they were accepted or rejected.

## Next milestone

After several clean real-data dry runs with the signal archive and sizing ledger:

1. persist the complete decision/trade-plan ledger
2. add Hyperliquid testnet ALO/post-only execution
3. persist orders, cancellations, reprices, fills, and fees
4. reconcile post-trade state against the intended buffer destination
5. compute TCA versus RW `arrival_price`
6. only then add an explicitly gated live mode

The point-in-time signal archive will also become the input history for later independent validation and EdgeLab experiments.
