"""
Integer-cents money handling.

Every financial value in the database and engine is an integer count of
cents. Dollars only exist at the UI boundary: a number_input collects
dollars and we convert to cents on the way in, and we format cents back to
a dollar string on the way out. This eliminates float drift in anything
that actually matters (recoupment targets, pro-rata splits, ledger sums).
"""


def to_cents(dollars) -> int:
    return int(round(float(dollars) * 100))


def from_cents(cents) -> float:
    return (cents or 0) / 100.0


def fmt(cents) -> str:
    cents = cents or 0
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def allocate_pro_rata(total_cents: int, weights: dict) -> dict:
    """
    Split total_cents among the keys in `weights` proportionally to their
    weight, using the largest-remainder method so the parts sum EXACTLY to
    total_cents (no float drift, no missing/extra cent).
    """
    weights = {k: w for k, w in weights.items() if w and w > 0}
    if total_cents <= 0 or not weights:
        return {k: 0 for k in weights}

    total_weight = sum(weights.values())
    raw = {k: total_cents * w / total_weight for k, w in weights.items()}
    floored = {k: int(raw[k]) for k in raw}
    allocated = sum(floored.values())
    remainder = total_cents - allocated

    # Give the leftover cents, one at a time, to the largest fractional
    # remainders first (standard largest-remainder / Hamilton apportionment).
    order = sorted(raw.keys(), key=lambda k: raw[k] - floored[k], reverse=True)
    for i in range(remainder):
        floored[order[i % len(order)]] += 1

    return floored
