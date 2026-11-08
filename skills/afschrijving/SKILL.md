---
name: afschrijving
description: Use when the monthly depreciation (afschrijving) of fixed assets must be booked in Exact Online as a memoriaal from an asset schedule, or when the user asks what this month's depreciation is.
version: 1.0.0
license: Apache-2.0
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, depreciation]
    related_skills: [nl-conventions, maandafsluiting]
---

# Afschrijving: monthly depreciation memoriaal

Works with the `exact` MCP server and its `draft_general_journal_entry` tool. Follow
`nl-conventions`. Call `whoami` first and use its division unless the user names another.

## Input: the asset schedule

A CSV file the user gives or names (one per administration), straight-line depreciation
per month. Columns, as in `references/asset-schedule-example.csv` (synthetic):

| Column | Meaning |
|---|---|
| `asset_code`, `description` | the asset |
| `rgs_asset`, `rgs_cumulative`, `rgs_expense` | RGS codes of cost, cumulative depreciation, depreciation expense |
| `cost_center` | optional |
| `acquisition_date`, `start_period` | date bought; first month depreciated (`YYYY-MM`) |
| `cost`, `residual_value`, `life_months` | basis and useful life |
| `disposal_period` | optional; no depreciation from this month |

An asset bought during a month starts that month or the next, as the schedule says; the
skill never decides it.

## Procedure

1. Period: the month the user names; default the last full month.
2. Calculate:

       python3 -I ${HERMES_SKILL_DIR}/scripts/depreciation.py <schedule.csv> <YYYY-MM>

   It returns the amount per asset, the memoriaal lines grouped per RGS code (expense
   debit, cumulative credit) and the skipped assets with the reason.
3. Duplicate check: `list_transactions` for the period on the expense G/L account. A line
   whose description has `Afschrijving|<period>` already exists: stop and report it.
4. Resolve every RGS code to the division's G/L account (`list_gl_accounts`); a code without
   an account: stop and ask.
5. Journal: `list_journals` with `kind: general`; use the memoriaal journal, ask when there
   are more.
6. `draft_general_journal_entry` with `dry_run: true`: journal, entry date the last day of
   the period, the lines from step 2, description parts `supplier` = `Afschrijving`,
   `period` = the period, `ref` = `AFS-<YYYY-MM>`, `short` = `afschrijving <maand>`; per line
   `short` = the asset group (`inventaris`, `transportmiddelen`).
7. Approval request on the approval channel: total, one line per G/L account, the number of
   assets, and the skipped assets with reason. On `boek <id>` run the same call with
   `dry_run: false` and read it back; the entry stays open until `process_entry` is
   approved separately.

## Checks before the request

- Debit total equals credit total to the cent.
- The cumulative depreciation of an asset never exceeds cost minus residual value: the
  script stops at the end of the life, a schedule that starts earlier than the asset was
  bought is reported, not booked.
