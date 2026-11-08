---
name: revenue-concentration
description: Use when the user wants to know how dependent an Exact Online administration is on a few customers - revenue share per customer, the Herfindahl-Hirschman index and the customers above the risk threshold.
version: 1.0.0
author: Open Accountant (adapted)
license: MIT
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, revenue]
    related_skills: [nl-conventions, profit-loss]
---

# Revenue concentration

Adapted from `business/revenue-concentration` of openaccountant/skills (MIT, see
`LICENSE`). Works with the `exact` MCP server, read tools only.

## Procedure

1. Period: default the last 12 full months.
2. Revenue per customer: `list_sales_invoices` for the period (status other than draft),
   amounts excl. VAT, credit notes netted. Revenue booked without a sales invoice (a
   memoriaal on `WOmz*`) is listed as "zonder factuur" and counts as one source.
3. Control: the sum equals the revenue of `get_revenue_by_period` for the same months
   within 1%; a larger difference is reported before any ratio.
4. Share per customer = revenue / total x 100; cumulative share in descending order.
5. HHI = sum of the squared shares in percent (0 to 10.000). Level: under 1.500 low,
   1.500 to 2.500 moderate, above 2.500 high.
6. Flag every customer above 25% of revenue, and the top three when together above 50%.
   Intercompany revenue (RGS `WOmzOit*`, or a customer that is a group company) is
   reported separately and left out of the HHI.

## Report

    Concentratie 2025-04 t/m 2026-03
    omzet 120.000, HHI 2.550 hoog
    1 Voorbeeld Klant A  40,0%
    2 Voorbeeld Klant B  25,0%  (65,0%)
    3 Voorbeeld Klant C  15,0%  (80,0%)
    overig 4 klanten     20,0%
    risico: A boven 25%, top 3 boven 50%
