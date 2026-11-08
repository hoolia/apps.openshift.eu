---
name: exact-purchase-invoice-upload
description: Use when a received supplier invoice (a PDF from a portal download, a mail attachment or a file the user gives) must be booked in Exact Online as a purchase entry with the invoice attached - including invoices fetched from a supplier website with the browser.
---

# Exact Online purchase invoice upload

Works with the `exact` MCP server. Its write tools default to `dry_run: true`; nothing is
posted until the user confirms the dry run. Every tool takes an optional `division`: call
`whoami` first and use its current division unless the user names another.

## 1. Get the invoice file

- From a supplier portal: find the login with the `vault` MCP server (search by the portal's
  host name), open the portal with the browser MCP server, log in, open the invoice list and
  download the invoice. Never print a password or one-time code.
- The browser saves downloads in its output folder; when that is the shared downloads folder
  the file is at `/opt/data/downloads/<file>`. Otherwise fetch the same URL into that folder.
- Keep one file per invoice; note the file name.

## 2. Read the invoice

From the PDF: supplier name, supplier VAT number, invoice number, invoice date, due date,
currency, payment reference, and per line or per VAT rate: description, amount excl. VAT,
VAT percentage, VAT amount. The amounts must add up to the total incl. VAT; stop and report
when they do not. A negative total is a credit note (`credit_note: true`).

## 3. Look up the Exact data

1. Supplier: `search_accounts` with `vat_number`, else `query` (name) and `kind: supplier`.
   No supplier found: stop and ask; this skill creates no accounts.
2. Duplicate check: `list_purchase_entries` with `supplier_id` and `your_ref` (the invoice
   number). An entry exists: stop and report it.
3. Journal: `list_journals` with `kind: purchase`; use the only one, or ask when there are more.
4. G/L account per line: the account the last entries of this supplier used
   (`list_purchase_entries` with `supplier_id`, then `get_purchase_entry`), else
   `list_gl_accounts` with a query from the line description. Say which source you used.
5. VAT code per line: `list_vat_codes` with `use: purchase` and the percentage; reverse
   charge for an EU supplier without VAT on the invoice.

## 4. Draft, confirm, post

1. `draft_purchase_entry` with `dry_run: true`: `supplier_id`, `journal`, `your_ref`,
   `entry_date` (invoice date), `due_date`, `description`, `currency`, `payment_reference`,
   `credit_note`, `lines[{gl_account_id, amount, vat_code, vat_amount, description}]`.
   Description, accounts and VAT follow the `nl-conventions` skill (description parts
   `{supplier}|{period}|{ref}|{short}`, RGS meaning, BTW rubriek).
2. Send the dry run as an approval request on the approval channel (`nl-conventions`):
   one line per booking line, VAT and the total next to the invoice total. Only after
   `boek <id>` call it again with `dry_run: false`; `afkeuren <id> <reden>` writes nothing.
3. Attach the file: `attach_document` with `entry_id` of the new entry, `file_name`, and
   `content_base64` of the file (or `file_url` for an https URL); `dry_run: true` first,
   then `dry_run: false` with the same confirmation.
4. Read it back with `get_purchase_entry` and report entry number, status (open) and the
   linked document. The entry stays open for the accountant to process.
