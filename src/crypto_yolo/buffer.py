from __future__ import annotations


def relative_buffer_bounds(target_weight: float, buffer: float) -> tuple[float, float]:
    """Return the spreadsheet-style relative no-trade region around a target.

    A 5% buffer means +/- 5% of the absolute target weight.
    """
    width = buffer * abs(target_weight)
    return target_weight - width, target_weight + width


def buffered_destination(
    current_weight: float,
    target_weight: float,
    buffer: float,
    mode: str = "edge",
) -> tuple[float, bool]:
    """Choose the post-trade weight.

    mode='target': spreadsheet-compatible: if breached, trade fully to target.
    mode='edge': production preference: if breached, trade only to buffer edge.
    """
    lo, hi = relative_buffer_bounds(target_weight, buffer)
    if lo <= current_weight <= hi:
        return current_weight, True

    if mode == "target":
        return target_weight, False
    if mode != "edge":
        raise ValueError("buffer mode must be 'edge' or 'target'")

    return (lo if current_weight < lo else hi), False
