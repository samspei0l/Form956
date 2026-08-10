"""Amount parsing/formatting, ported from winzoylegal_new's
_shared/formatAmount.ts + _shared/costCalculations.ts."""
from __future__ import annotations

VAC_SURCHARGE_RATE = 0.014  # DoHA card-payment surcharge on top of the base VAC amount


def parse_amt(v) -> float:
    """Parse a raw amount (string, possibly with thousand separators, or a
    number) into a float, defaulting to 0.0 on anything unparseable."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").strip()
    if not s:
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def apply_vac_surcharge(base) -> float:
    """Given the base VAC amount staff enters, return the amount incl. the
    1.4% card surcharge."""
    return parse_amt(base) * (1 + VAC_SURCHARGE_RATE)


def sum_amounts(*values) -> float:
    return sum(parse_amt(v) for v in values)


def fmt_amt(v) -> str:
    """Format a number (or numeric string) with thousand separators and
    exactly 2 decimal places, e.g. 1234.5 -> '1,234.50'."""
    return f"{parse_amt(v):,.2f}"
