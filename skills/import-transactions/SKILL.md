---
name: import-transactions
description: Use when a bank statement export (CAMT.053, MT940 or a bank CSV) must be checked against the bank entries in Exact Online - finding missing days, missing or duplicate lines, and a closing balance that does not agree - before categorising or closing a month.
version: 1.0.0
author: Open Accountant (adapted)
license: MIT
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, bank]
    related_skills: [nl-conventions, smart-categorize, maandafsluiting]
---

# Import transactions: bank statement against Exact

Adapted from `shared/import-transactions` of openaccountant/skills (MIT, see `LICENSE`).
Works with the `exact` MCP server, read tools only. Follow `nl-conventions` for reports.

Bank lines reach Exact through the bank link or the bank import of Exact itself; the
`exact` server has no tool that posts bank lines. This skill checks that every line of the
statement is in Exact and reports what is not.

## Inputs

- the statement file in the downloads folder: CAMT.053 (`.xml`), MT940 (`.sta`, `.940`,
  `.txt` with `:61:` lines) or a bank CSV
- the bank account: IBAN or the bank G/L account; default the only bank journal
  (`list_journals` with `kind: bank`)

## Procedure

1. Detect the format: XML with `BkToCstmrStmt` is CAMT.053; `:20:` and `:61:` tags are
   MT940; otherwise CSV, with date, amount, counterparty, IBAN and description columns
   recognised from the header (a Dutch bank CSV names them `Datum`, `Bedrag`,
   `Naam tegenpartij`, `Tegenrekening`, `Omschrijving`; ask when unclear).
2. Parse: booking date, amount (credit positive, debit negative), counterparty name and
   IBAN, description, end-to-end or payment reference. Show the first 5 lines to the user
   and the opening and closing balance of the statement.
3. Exact side: `list_bank_transaction_lines` for the same dates and bank G/L account (split
   the range when 500 lines return).
4. Match line by line: same date (one bank day tolerance), same amount to the cent, then
   reference or description. Every statement line is matched at most once.
5. Report, short lines:
   - statement lines missing in Exact (date, amount, counterparty)
   - Exact lines not on the statement (possible duplicates)
   - bank days without any line in Exact
   - closing balance of the statement against opening balance plus the sum of the Exact
     lines; a difference is listed, never explained away

## Next step

Missing lines: import the statement in Exact or wait for the bank link, then run this skill
again. When everything is present: `smart-categorize` for lines on suspense.
