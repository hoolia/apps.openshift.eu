---
name: nl-conventions
description: Use when booking, proposing or reviewing any entry in a Dutch Exact Online administration - choosing a G/L account by its RGS 3.6 meaning, picking the Exact VAT code for a BTW rubriek, or writing the entry description - and before any of the other accounting skills writes to Exact.
version: 1.0.0
license: Apache-2.0
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, rgs, btw]
    related_skills: [afletteren, afschrijving, loonjournaalpost, maandafsluiting, smart-categorize]
    config:
    - key: accounting.fewshot_dir
      description: Directory of the private few-shot booking examples; empty means none
      default: ""
---

# Dutch bookkeeping conventions for Exact Online

The house rules every accounting skill follows. Other skills point here; read this once per
session before the first write.

## Description convention

Every entry and entry line carries one description in four parts:

    {supplier}|{period}|{ref}|{short}

| Part | Content | Example |
|---|---|---|
| `supplier` | the counterparty as named in Exact (supplier or customer account name); for a memoriaal without counterparty the source: `Afschrijving`, `Loonjournaal`, `Correctie` | `Voorbeeld Hosting BV` |
| `period` | the period the amount belongs to, `YYYY-MM` (service period, not payment date); a quarter `YYYY-Qn`, a year `YYYY` | `2026-03` |
| `ref` | the source reference: supplier invoice number, payroll run, asset code, bank statement reference | `INV-1001` |
| `short` | at most 30 characters, lower case Dutch, what it is | `hosting maart` |

- The `exact` write tools render this string server-side. Pass the four parts in the tool's
  description fields (`supplier`, `period`, `ref`, `short`) and not a rendered string, so
  every entry is rendered and truncated the same way.
- Only when the tool schema of this server offers a single `description` field, render the
  string yourself in exactly this form.
- No `|` inside a part (replace it with `/`), no line breaks, no personal data: no employee
  names, BSN, IBAN or salary amounts per person in any description.

## Accounts: RGS 3.6 semantics

Choose the account by its RGS meaning first, then find the division's G/L account for it.

1. Pick the RGS level 4 code (`referentiecode`) from
   `references/rgs-3.6-mkb.csv` (read it with `skill_view` on this skill's
   `references/rgs-3.6-mkb.csv`, or `grep` in `${HERMES_SKILL_DIR}/references/`). Columns:
   `referentiecode`, `referentienummer`, `nivo`, `dc` (normal side), `omschrijving`,
   `omslagcode` (the counter code when the balance flips side), `alleen_ez_vof`,
   `alleen_bv`.
2. Resolve it to the division's G/L account with `list_gl_accounts` (query on the RGS
   description words). The `exact` server does not return the division's RGS mapping;
   state the G/L code and the RGS code you assumed side by side so the reviewer can check.
3. A BV never uses a row marked `alleen_ez_vof`; an eenmanszaak or VOF never uses a row
   marked `alleen_bv`.
4. Common meanings (see the CSV for the full set):

| Situation | RGS code |
|---|---|
| bank current account | `BLimBanRba` |
| trade creditors / debtors | `BSchCreHac` / `BVorDebHad` |
| to pay VAT / input VAT (5b) | `BSchBepBtw` / `BSchBepBoo` |
| to pay wage tax | `BSchBepLhe` |
| net wages payable | `BSchSalNet` |
| pension premiums payable | `BSchOvsStp` |
| interim account salaries | `BSchTusTsa` |
| suspense (vraagposten) | `BSchTusTov` |
| accrued costs | `BSchOpaNtb` (or the specific `BSchOpaN*` row) |
| revenue services 21% | `WOmzNodOdh` |
| wages / social charges / pension | `WPerLesLon` / `WPerSolPsv` / `WPerPenPen` |
| depreciation inventory / cumulative | `WAfsAmvBei` / `BMvaBeiCae` |
| software subscriptions / IT | `WBedKanSof` / `WBedKanKoa` |
| accountancy and advice | `WBedAeaAea` |
| bank charges | `WBedAdlBan` |
| non-deductible VAT on costs | `WBedAdlNbo` |

Source, version and licence of the CSV: `references/SOURCE.md`.

## VAT: BTW rubriek first, Exact code second

Exact VAT codes are set per division; never assume a code. Decide the BTW rubriek of the
line, then pick the code with `list_vat_codes` (`use: purchase` or `sales`) whose
percentage, type and `VATTransactionType` fit. Rules: `references/btw.md`.

## Amounts, dates and periods

- Tool calls: numbers with a dot (`1234.56`), ISO dates. Reports to the user: Dutch
  notation (`EUR 1.234,56`), never rounded.
- Reports in chat are short: a heading line, then one line per item. A wide table only
  goes into a file the user downloads, with the chat message naming the file.
- Debit positive, credit negative in a journal line, unless the tool schema says otherwise.
- A journal entry balances to the cent before it is drafted. Stop when it does not.
- The entry date of a memoriaal is the last day of its period.

## Write discipline

- Every write starts with `dry_run: true`. Send the dry run as an approval request on the
  approval channel and wait for the answer before `dry_run: false`.
- Drafted entries stay open. `process_entry` (booking an entry final) runs only after an
  approval of that entry, after the draft was read back.
- Never create accounts, G/L accounts or VAT codes.

## Approval channel

Approvals arrive on the approval channel: the chat the user reaches the agent on. Name no
chat platform in a message. Each proposal gets a short id (`P` plus a number, unique in the
session). The user answers with one of two commands:

| Command | Effect |
|---|---|
| `boek <id>` | approve: run the same call with `dry_run: false` |
| `afkeuren <id> <reden>` | reject: write nothing, record the reason, propose again only on a new input |

Any other answer is not an approval. One approval covers one proposal, never a batch the
user has not seen.

An approval request fits a phone screen: no table wider than three short columns, one line
per booking line, amounts right after the account. Form:

    P12 inkoop Voorbeeld Hosting BV
    INV-1001, 2026-03, factuur 2026-03-31
    D 4206115 software    100,00 21%
    D btw 5b               21,00
    C crediteuren         121,00
    boek P12 / afkeuren P12 <reden>

## Few-shot examples

Booking examples from the real journal come from a private store and are never part of
this repository. Where they are mounted and how they look: `references/few-shot/README.md`.
When none are mounted, work from the rules above and say so in the report.
