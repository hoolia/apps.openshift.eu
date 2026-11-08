"""Monthly straight-line depreciation of an asset schedule for one period.

Usage: python3 -I depreciation.py <schedule.csv> <YYYY-MM>

Prints JSON: one line per asset with its amount for the period, the lines of the
memoriaal grouped per RGS code, and the assets that are skipped and why. Amounts are
in cents internally; the last month of the useful life takes the rounding remainder,
so the total over the life equals cost minus residual value.
"""

import csv
import json
import sys
from collections import defaultdict

REQUIRED = ["asset_code", "description", "rgs_expense", "rgs_cumulative", "start_period", "cost", "residual_value", "life_months"]


def months_between(start, period):
    sy, sm = map(int, start.split("-"))
    py, pm = map(int, period.split("-"))
    return (py - sy) * 12 + (pm - sm)


def cents(value):
    return round(float(str(value).replace(",", ".")) * 100)


def amount_for(row, period):
    """Cents of depreciation in period, or (0, reason)."""
    n = months_between(row["start_period"], period)
    life = int(row["life_months"])
    if n < 0:
        return 0, "not started"
    if n >= life:
        return 0, "fully depreciated"
    disposal = (row.get("disposal_period") or "").strip()
    if disposal and months_between(disposal, period) >= 0:
        return 0, "disposed"
    base = cents(row["cost"]) - cents(row["residual_value"])
    monthly = base // life
    if n == life - 1:
        return base - monthly * (life - 1), ""
    return monthly, ""


def main(path, period):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    missing = [c for c in REQUIRED if rows and c not in rows[0]]
    if missing:
        sys.exit(f"schedule lacks columns: {', '.join(missing)}")
    assets, skipped = [], []
    debit, credit = defaultdict(int), defaultdict(int)
    for row in rows:
        amount, reason = amount_for(row, period)
        if not amount:
            skipped.append({"asset_code": row["asset_code"], "reason": reason})
            continue
        assets.append({"asset_code": row["asset_code"], "description": row["description"], "amount": amount / 100})
        debit[(row["rgs_expense"], row.get("cost_center", ""))] += amount
        credit[(row["rgs_cumulative"], "")] += amount
    lines = [{"rgs": k[0], "cost_center": k[1], "amount": v / 100} for k, v in sorted(debit.items())]
    lines += [{"rgs": k[0], "cost_center": k[1], "amount": -v / 100} for k, v in sorted(credit.items())]
    total = sum(debit.values())
    print(json.dumps({"period": period, "total": total / 100, "assets": assets, "lines": lines, "skipped": skipped}, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
