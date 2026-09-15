from __future__ import annotations


CANONICAL_BUFFER_BASIS = "absolute_weight"
LEGACY_BUFFER_BASIS = "relative_target"


def absolute_buffer_bounds(target_weight: float, buffer: float) -> tuple[float, float]:
    """Return the Robot Wealth no-trade region around a target weight.

    ``buffer`` is the *total absolute portfolio-weight width* of the region,
    so a 2% buffer means +/- 1 percentage point around the target.  The
    region is clipped at zero so a no-trade band never crosses from long to
    short (or vice versa).  A zero target is always exact: there is no buffer
    around a requested flat position.
    """
    if buffer < 0:
        raise ValueError("trade buffer must be non-negative")
    if target_weight == 0:
        return 0.0, 0.0

    half_width = buffer / 2.0
    lo = target_weight - half_width
    hi = target_weight + half_width

    if target_weight > 0:
        lo = max(0.0, lo)
    else:
        hi = min(0.0, hi)
    return lo, hi


def relative_buffer_bounds(target_weight: float, buffer: float) -> tuple[float, float]:
    """Return the legacy YOLO relative-to-target no-trade region.

    A 5% buffer means +/- 5% of the absolute target weight.  This is retained
    only for backwards compatibility; ``absolute_weight`` is the canonical
    Robot Wealth/document behavior.
    """
    if buffer < 0:
        raise ValueError("trade buffer must be non-negative")
    width = buffer * abs(target_weight)
    return target_weight - width, target_weight + width


def buffer_bounds(
    target_weight: float,
    buffer: float,
    basis: str = CANONICAL_BUFFER_BASIS,
) -> tuple[float, float]:
    value = basis.strip().lower()
    if value == CANONICAL_BUFFER_BASIS:
        return absolute_buffer_bounds(target_weight, buffer)
    if value == LEGACY_BUFFER_BASIS:
        return relative_buffer_bounds(target_weight, buffer)
    raise ValueError(
        "buffer basis must be 'absolute_weight' or 'relative_target'"
    )


def buffered_destination(
    current_weight: float,
    target_weight: float,
    buffer: float,
    mode: str = "edge",
    *,
    basis: str = CANONICAL_BUFFER_BASIS,
) -> tuple[float, bool]:
    """Choose the post-trade weight inside a configured no-trade region.

    ``mode='edge'`` is the canonical Robot Wealth behavior: if the current
    weight breaches the no-trade region, trade only to the nearest edge.
    ``mode='target'`` remains available for spreadsheet/regression behavior.
    """
    lo, hi = buffer_bounds(target_weight, buffer, basis)
    if lo <= current_weight <= hi:
        return current_weight, True

    if mode == "target":
        return target_weight, False
    if mode != "edge":
        raise ValueError("buffer mode must be 'edge' or 'target'")

    return (lo if current_weight < lo else hi), False
