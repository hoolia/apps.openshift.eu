---
name: exact-invoice-matching
description: Use when matching bank transactions, payments or received invoices to open purchase or sales entries in Exact Online - finding which supplier invoice a bank line pays, which open payables or receivables have no payment, or which received invoice is not yet booked.
---

# Exact Online invoice matching

Works with the `exact` MCP server (Exact Online, read tools). Every tool takes an optional
`division`; call `whoami` first and use its current division unless the user names another
(`list_divisions`). This skill only reads: it proposes matches and never books, reconciles or
pays anything.

## Inputs

Ask for, or derive from the request:

- the period (`date_from`, `date_to`); default: the last full month
- the side: purchase (supplier invoices, payables) or sales (customer invoices, receivables)
- optionally the received invoices: PDF files in the downloads folder, mail attachments, or a list

## Procedure

1. Collect the open items:
   - purchase: `list_open_payables` (with `account_id` for one supplier) and
     `list_purchase_entries` with `status: open` for the period
   - sales: `list_open_receivables` and `list_sales_invoices` for the period
2. Collect the payments: `list_bank_transaction_lines` for the period (max 500 per call;
   split the period when it returns 500 lines).
3. Match every bank line to at most one open item, strongest rule first:
   1. the invoice number (`your_ref`, entry or invoice number) or the payment reference
      appears in the bank line description
   2. same account (supplier or customer) and the amount equals the open amount to the cent
   3. same amount to the cent and the counterparty name matches the account name
   4. same account, amount within 1% or a partial payment: report as a candidate, not a match
4. For received invoices that are files: read supplier, invoice number, date, amount excl. and
   incl. VAT. Look the supplier up with `search_accounts` (`kind: supplier`, by VAT number
   first, then name). An invoice whose supplier and `your_ref` already exist in
   `list_purchase_entries` is booked; every other one is "not yet booked".

## Report

One table per result, amounts with currency, never rounded:

| Group | Columns |
|---|---|
| Matched | bank date, amount, description, entry or invoice number, account, rule used |
| Candidates | as above, plus the difference and why it is not certain |
| Bank lines without an open item | date, amount, description |
| Open items without a payment | number, account, due date, open amount, days overdue |
| Received invoices not yet booked | file, supplier, invoice number, date, amount incl. VAT |

End with the next step: for unbooked invoices, the `exact-purchase-invoice-upload` skill.
