---
name: expense-optimizer
description: Use when the user wants to find recurring costs in an Exact Online administration to cancel, reduce or renegotiate - subscriptions, duplicate services, price increases and suppliers no longer used.
version: 1.0.0
author: Open Accountant (adapted)
license: MIT
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, costs]
    related_skills: [nl-conventions, monthly-digest]
---

# Expense optimizer

Adapted from `shared/expense-optimizer` of openaccountant/skills (MIT, see `LICENSE`).
Works with the `exact` MCP server, read tools only. It proposes; it cancels nothing.

## Procedure

1. Period: the last 12 months.
2. Recurring costs: `list_purchase_entries` for the period (all statuses but rejected) and
   `list_bank_transaction_lines` for direct debits without an invoice. Group per supplier;
   a supplier with entries in at least three different months, or a yearly entry, is
   recurring.
3. Per recurring supplier: frequency, last amount, amount 12 months ago, yearly cost excl.
   VAT, the G/L account and its RGS meaning (`nl-conventions`).
4. Flag:
   - price increase above 5% within the year
   - two suppliers on the same RGS code for the same kind of service (for example two on
     `WBedKanSof` for the same tool category)
   - a supplier whose last entry is older than its frequency (stopped, or billed elsewhere)
   - recurring costs on `WBedKanTef`, `WBedHuiGwe`, `WBedAutOpa` and insurance: candidates
     to renegotiate
5. Classify each: cancel, reduce, renegotiate, merge, keep. Keep is the default when there
   is no evidence.

## Report

One line per proposal, largest yearly saving first, then the total:

    Besparing 12 mnd: 3 voorstellen, 1.440,00 per jaar
    samenvoegen  Voorbeeld Tool A + B   720,00
    heronderhand Voorbeeld Telecom BV   480,00 (+12%)
    opzeggen     Voorbeeld Cloud BV     240,00 (laatst 2025-10)

The user decides; a cancellation happens at the supplier, not in Exact.
