---
name: afletteren
description: Use when open items in Exact Online must be cleared against their payments (afletteren) - after a bank import, before a month-end close, or when the user asks which payables and receivables are paid but still open.
version: 1.0.0
license: Apache-2.0
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, reconciliation]
    related_skills: [nl-conventions, exact-invoice-matching, maandafsluiting]
---

# Afletteren: clear open items against payments

Works with the `exact` MCP server. Follow `nl-conventions` (approval channel, write
discipline). Call `whoami` first and use its division unless the user names another.

The result is a proposal list. Matches are only written to Exact when the server offers
the `create_matchset` tool; without it, the list is the deliverable and the accountant
clears the items in Exact.

## Inputs

- side: purchase (payables), sales (receivables) or both; default both
- period of the payments; default the last full month

## Procedure

1. Open items: `list_open_payables` and `list_open_receivables` (max 200 per call; per
   `account_id` when more exist).
2. Payments: `list_bank_transaction_lines` for the period, split the period when 500 lines
   return.
3. Match with the rules of `exact-invoice-matching`, strongest first: invoice number or
   payment reference in the description; same account and amount to the cent; same amount
   and matching name. A partial payment, a difference up to 1% or one payment for several
   invoices is a candidate, never a match.
4. One payment for several invoices of one account is a match only when the sum of the
   invoices equals the payment to the cent.
5. A difference that remains on a match (bank charges, a payment discount) is not written
   off by this skill: list it under candidates with the amount.

## Proposal list

Send per side a short list, one line per proposal:

    Afletteren 2026-03 inkoop: 3 voorstellen
    P21 Voorbeeld Hosting BV INV-1001 121,00 = bank 2026-04-02
    P22 Voorbeeld Telecom BV 2 facturen 80,00 = bank 2026-04-05
    Kandidaten: 1 (verschil 0,50)

Then the candidates, bank lines without an open item, and open items older than 60 days
without a payment, each as a one-line list.

## Writing MatchSets

Only when the tool list contains `create_matchset`:

1. Per approved proposal: `create_matchset` with `dry_run: true`, the open item lines and
   the payment line of that proposal, and the four description parts of `nl-conventions`
   (`supplier`, `period`, `ref`, `short` = `aflettering`).
2. The approval request is the proposal line plus the dry run total, which must be 0,00.
   A non-zero total: stop, report, do not ask for approval.
3. On `boek <id>`: the same call with `dry_run: false`. Read the open items back and
   report which are no longer open.
4. On `afkeuren <id> <reden>`: write nothing, keep the reason in the final report.

Without `create_matchset`, end with: "MatchSets are not enabled on this server; clear the
proposals above in Exact (Financieel, Afletteren)."
