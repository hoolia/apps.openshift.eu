---
name: month-end-close
description: Use when the state of a month in Exact Online must be reviewed before it is closed - a read-only checklist of bank completeness, suspense, open purchase entries, unmatched payments, depreciation and payroll present, and anomalies.
version: 1.0.0
author: Open Accountant (adapted)
license: MIT
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, month-end]
    related_skills: [nl-conventions, maandafsluiting, monthly-digest]
---

# Month-end close checklist

Adapted from `business/month-end-close` of openaccountant/skills (MIT, see `LICENSE`).
Works with the `exact` MCP server, read tools only. `maandafsluiting` runs the bookings;
this skill only checks and reports what is still open.

## Checklist

Month: the one the user names; default the last full month. Each item is `ok` or `open`
with a count and amount.

1. **Bank complete**: `list_bank_transaction_lines` has lines on every bank day with
   activity, and the last statement balance the user gives equals the Exact balance
   (opening balance plus lines). Without a statement balance the item stays `open`.
2. **Suspense empty**: `list_transactions` on the suspense G/L account (RGS `BSchTusTov`)
   sums to 0,00 for the month.
3. **Purchase entries processed**: `list_purchase_entries` with `status: open` for the
   month; every open entry is listed.
4. **Payments matched**: bank lines without an open item (`exact-invoice-matching` rules).
5. **Depreciation booked**: `list_transactions` on the depreciation expense accounts
   (`WAfs*`) has a line with `Afschrijving|<month>`, when the administration has assets.
6. **Payroll booked**: a line with `Loonjournaal|<month>` on the net wages account
   (`BSchSalNet`), when the administration has payroll.
7. **Anomalies**:
   - duplicates: same account, amount and date twice
   - amounts more than three times the average of that G/L account over 6 months
   - a recurring cost of the last three months missing this month
   - cost accounts with a credit balance, balance accounts on the wrong side (`dc` in the
     RGS reference of `nl-conventions`)
8. **Result**: `get_profit_and_loss` for the period result.

## Report

Short, one line per item, for the approval channel:

    Close 2026-03 Voorbeeld BV
    bank        ok
    suspense    open 2 / 84,10
    inkoop      open 1 / 121,00
    betalingen  ok
    afschr.     ok
    loon        ok
    afwijkingen 1 (dubbel?)
    resultaat   4.210,00

End with the skill that resolves each open item. The month is ready to close when every
item is `ok`.
