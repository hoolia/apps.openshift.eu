---
name: runway-calculator
description: Use when the user asks how many months an Exact Online administration can keep paying its costs at the current burn rate (runway), under current, lean and growth scenarios.
version: 1.0.0
author: Open Accountant (adapted)
license: MIT
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, cash]
    related_skills: [nl-conventions, cash-flow-forecast]
---

# Runway calculator

Adapted from `business/runway-calculator` of openaccountant/skills (MIT, see `LICENSE`).
Works with the `exact` MCP server, read tools only.

## Procedure

1. Cash: the bank G/L accounts (RGS `BLimBan*`) today, as in `cash-flow-forecast`, plus
   open receivables not more than 90 days overdue, minus open payables, minus the VAT and
   wage tax balance to pay (`BSchBepBtw`, `BSchBepLhe`). Report the parts.
2. Per month, the last 6 full months, from `list_transactions` on the profit and loss
   accounts grouped by RGS level 2 (`nl-conventions`):
   - gross burn: all costs except depreciation (`WAfs`, not a cash cost)
   - revenue: `WOmz` and `WOvb`
   - net burn = gross burn minus revenue
3. Trend: average of the last 3 months against the 3 before; report the change in percent.
4. Scenarios: current (3-month average), lean (gross burn minus 20%), growth (gross burn
   plus 30%), revenue unchanged in all three. Runway = cash / net burn in months, one
   decimal; cash-out month = this month plus the runway.
5. Net burn 0 or negative: "winstgevend, geen runway-grens"; report the monthly cash
   increase instead.
6. Level: green 12 months or more, yellow 6 to 12, red under 6.

## Report

    Runway Voorbeeld BV, kas 40.000
    huidig  10,0 mnd (2027-01) geel
    zuinig  20,0 mnd (2027-11) groen
    groei    5,7 mnd (2026-09) rood
    trend burn +8% (3 mnd)

The amounts exclude VAT; the payroll of the coming months is part of the burn, not a
separate deduction.
