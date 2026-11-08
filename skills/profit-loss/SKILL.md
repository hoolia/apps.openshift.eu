---
name: profit-loss
description: Use when the user asks for a profit and loss statement (winst- en verliesrekening) of an Exact Online administration for a month, quarter, year to date or any date range, in the Dutch layout with gross margin and net result.
version: 1.0.0
author: Open Accountant (adapted)
license: MIT
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, reporting]
    related_skills: [nl-conventions, monthly-digest]
---

# Profit and loss statement

Adapted from `business/profit-loss` of openaccountant/skills (MIT, see `LICENSE`).
Works with the `exact` MCP server, read tools only.

## Procedure

1. Period: what the user names; default year to date.
2. Fast path: for year to date and the current period, `get_profit_and_loss` gives revenue,
   costs and result against last year. Use it as the control total.
3. Detail for any range: `list_gl_accounts`, then for each profit and loss account
   (`balance_type` W) `list_transactions` for the range. Map every account to its RGS level
   2 group with `nl-conventions` and sum.
4. Layout, by RGS group:

| Line | RGS level 2 |
|---|---|
| Netto-omzet | `WOmz` |
| Kostprijs van de omzet | `WKpr`, `WWiv` |
| Brutomarge | omzet minus kostprijs |
| Overige bedrijfsopbrengsten | `WOvb` |
| Personeelskosten | `WPer` |
| Afschrijvingen | `WAfs` |
| Overige bedrijfskosten | `WBed` |
| Bedrijfsresultaat | |
| Financiele baten en lasten | `WFbe` |
| Resultaat voor belastingen | |
| Belastingen | `WBel` |
| Resultaat na belastingen | |

   Any other RGS level 2 group with a non-zero total (`WWvi`, `WBwv`, `WFbe` parts,
   `WRed`) gets its own line under its RGS name, above the result it belongs to.
5. Brutomarge % = brutomarge / netto-omzet x 100; netto-marge % = resultaat na
   belastingen / netto-omzet x 100.
6. The detail result must equal `get_profit_and_loss` for the same range when that range is
   year to date; a difference is reported with the accounts that cause it.

## Report

In chat: one line per layout line with amount and, when asked, last year. The full account
list goes into a file in the downloads folder.
