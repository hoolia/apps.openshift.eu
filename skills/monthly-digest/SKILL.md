---
name: monthly-digest
description: Use when the user wants a short monthly summary of an Exact Online administration - revenue, costs, result, cash, receivables and payables, the largest costs and anything unusual - for the owner or the month-end review.
version: 1.0.0
author: Open Accountant (adapted)
license: MIT
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, reporting]
    related_skills: [nl-conventions, profit-loss, month-end-close]
---

# Monthly digest

Adapted from `shared/monthly-digest` of openaccountant/skills (MIT, see `LICENSE`).
Works with the `exact` MCP server, read tools only.

## Procedure

1. Month: default the last full month. Per currency, never rounded.
2. Result: `get_profit_and_loss` (period and year to date against last year) and
   `get_revenue_by_period` for this and last year.
3. Costs per group: `list_transactions` per cost G/L account group of the month, grouped by
   the RGS level 3 meaning (`WPerLes`, `WBedHui`, `WBedKan`, ...; `nl-conventions`), with
   the previous month.
4. Cash: sum of the bank G/L accounts (RGS `BLimBan*`) at month end.
5. Receivables and payables: `list_open_receivables` and `list_open_payables`, totals and
   the overdue part.
6. Largest five cost lines of the month, and counterparties seen for the first time.
7. Unusual: a cost group more than 25% above its 3-month average; a recurring cost missing.

## Digest

Short enough for a phone:

    Maand 2026-03 Voorbeeld BV
    Omzet      12.000,00 (+8%)
    Kosten      7.790,00 (-2%)
    Resultaat   4.210,00
    Bank       25.400,00
    Debiteuren  6.050,00 (1.210,00 te laat)
    Crediteuren 2.300,00
    Grootste kosten: huur 1.500,00, lonen 4.000,00
    Opvallend: telefoon +60%

The full tables (cost groups, top five, new counterparties) go into a file in the
downloads folder when the user asks for them.
