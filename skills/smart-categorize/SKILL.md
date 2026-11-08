---
name: smart-categorize
description: Use when bank lines in Exact Online are booked to suspense (vraagposten) or need a counter account - proposing the RGS-based G/L account and VAT treatment per counterparty from earlier bookings and moving them with an approved memoriaal.
version: 1.0.0
author: Open Accountant (adapted)
license: MIT
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, categorisation]
    related_skills: [nl-conventions, import-transactions, maandafsluiting]
---

# Smart categorize: counter accounts for bank lines

Adapted from `shared/smart-categorize` of openaccountant/skills (MIT, see `LICENSE`).
Works with the `exact` MCP server. Follow `nl-conventions`.

## Procedure

1. Uncategorised lines: `list_bank_transaction_lines` for the period on the suspense G/L
   account (RGS `BSchTusTov`, found with `list_gl_accounts`), and `list_transactions` on
   that account for lines that came from other journals.
2. Group the lines by counterparty: IBAN first, then the normalised name (upper case, no
   `BV`, `B.V.`, `NV`, punctuation or payment provider prefix such as `CCV*` or `SumUp *`).
3. Per group, find the account in this order and name the source in the proposal:
   1. a payment of an open item: not a categorisation, hand it to `afletteren`
   2. the same counterparty booked earlier (`list_transactions` with `account_id`, or
      `list_bank_transaction_lines` with `description`): the G/L account used most in the
      last 12 months
   3. a private few-shot example (`nl-conventions`, Few-shot examples) for this counterparty
   4. the RGS meaning of what the counterparty sells (`nl-conventions`, Accounts); state
      the assumption
4. VAT: a bank line is no invoice. Without an invoice, book the gross amount without VAT
   deduction; when an invoice exists, the cost belongs on a purchase entry
   (`exact-purchase-invoice-upload`) and the bank line is cleared against it.
5. Proposal per group on the approval channel: counterparty, number of lines, total, the
   G/L account with RGS code, and the source of the choice.
6. On `boek <id>`: one memoriaal with `draft_general_journal_entry` that moves the lines
   from suspense to the account (`dry_run: true`, then `false`), description parts
   `supplier` = counterparty, `period` = month of the lines, `ref` = the bank reference,
   `short` = what was bought.

## Rules

- A line already on another account than suspense is never moved.
- Transfers between own accounts, taxes (Belastingdienst) and payroll payments are not
  costs: propose the matching balance account (`BSchBep*`, `BSchSalNet`) or ask.
- Report the rules that were learned (counterparty, account) at the end so the accountant
  can add them as a bank rule in Exact.
