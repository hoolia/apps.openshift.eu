---
name: maandafsluiting
description: Use when a month must be closed in a Dutch Exact Online administration (maandafsluiting) - running the bookings that belong to a month end in order, depreciation, payroll, accruals, clearing, VAT check, and ending with the close review.
version: 1.0.0
license: Apache-2.0
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, month-end]
    related_skills: [nl-conventions, import-transactions, smart-categorize, afletteren, afschrijving, loonjournaalpost, month-end-close, monthly-digest]
---

# Maandafsluiting: close one month

Orchestrates the other skills for one administration and one month. Follow
`nl-conventions`. Each step that writes asks its own approval; this skill never approves on
the user's behalf and never skips a step silently.

## Inputs

- the division (`whoami`, `list_divisions`) and the month; default the last full month
- the asset schedule for `afschrijving` and the Nmbrs journal for `loonjournaalpost`, when
  the administration has assets or payroll

## Order

Send one status line per step on the approval channel as it finishes
(`5/9 afschrijving: P31 wacht op boek`).

1. **Bank complete.** `import-transactions`: every bank day of the month is in Exact (no gap
   in `list_bank_transaction_lines`). A gap: stop, ask for the statement.
2. **Purchase invoices.** Unbooked received invoices of the month: `exact-invoice-matching`
   (report), `exact-purchase-invoice-upload` per invoice.
3. **Categorise.** `smart-categorize` for bank lines on suspense (`BSchTusTov`) or without
   a counter account.
4. **Afletteren.** `afletteren` for both sides.
5. **Afschrijving.** `afschrijving` for the month.
6. **Loonjournaalpost.** `loonjournaalpost` for each payroll run of the month; skipped with
   the reason when this session is not in the payroll role.
7. **Accruals (overlopende posten).** Costs of the month without an invoice that recur
   every month (rent, energy, subscriptions seen in the last three months): propose one
   memoriaal with `draft_general_journal_entry` to the `BSchOpaN*` accounts, entry date
   the last day of the month, `ref` = `OVL-<YYYY-MM>`, and the reversal on the first day
   of the next month in the same proposal. Only on approval.
8. **VAT check.** For a month that ends a VAT period: per rubriek of `nl-conventions`
   `references/btw.md` the sum of the VAT G/L accounts (`list_transactions`) against the
   base. Report the totals; this skill files no VAT return.
9. **Review.** `month-end-close` for the checklist and `monthly-digest` for the summary.

## End report

A short list per step: done, waiting for approval (with the id) or skipped (with the
reason), then the open questions. The month is closed when every step is done and the
`month-end-close` checklist has no open item; say "maand <YYYY-MM> gesloten" only then.
Locking the period in Exact is the accountant's action, not this skill's.
