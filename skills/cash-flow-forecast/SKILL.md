---
name: cash-flow-forecast
description: Use when the user wants a cash flow forecast (liquiditeitsprognose) for the coming months of an Exact Online administration - from the bank balance, open receivables and payables, recurring costs, payroll and VAT payments.
version: 1.0.0
author: Open Accountant (adapted)
license: MIT
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, cash]
    related_skills: [nl-conventions, runway-calculator]
---

# Cash flow forecast

Adapted from `business/cash-flow-forecast` of openaccountant/skills (MIT, see `LICENSE`).
Works with the `exact` MCP server, read tools only.

## Procedure

1. Horizon: default 3 months, per month.
2. Opening balance: the bank G/L accounts (RGS `BLimBan*`) today, from `list_transactions`
   since the start of the financial year (it includes the opening balance entry); when the
   user gives today's bank balance, use that and report the difference.
3. Known cash flows:
   - receivables: `list_open_receivables`, in the month of the due date; overdue items in
     the first month, items more than 90 days overdue not at all (listed separately)
   - payables: `list_open_payables`, in the month of the due date
   - payroll: the net wages, wage tax and pension of the last payroll month
     (`BSchSalNet`, `BSchBepLhe`, `BSchOvsStp`) repeated each month; wage tax is paid the
     month after
   - VAT: the balance of `BSchBepBtw` and its sub accounts, paid in the month after the VAT
     period ends (a quarter: the month after the quarter)
4. Estimated flows: the 3-month average of revenue (`get_revenue_by_period`) incl. VAT
   and of recurring costs not yet invoiced (`expense-optimizer` grouping), from the month
   after the receivables and payables run out.
5. Per month: opening, inflow known, inflow estimated, outflow known, outflow estimated,
   closing. Known and estimated stay separate.
6. Flag a month whose closing balance is below two months of fixed costs (payroll plus
   recurring costs).

## Report

One line per month on a phone:

    Prognose Voorbeeld BV, begin / eind
    2026-04  25.400 / 21.900
    2026-05  21.900 / 19.300
    2026-06  19.300 / 14.800 onder buffer 16.000

The full table goes into a file in the downloads folder.
