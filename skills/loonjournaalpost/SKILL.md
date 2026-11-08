---
name: loonjournaalpost
description: Use when a payroll journal (loonjournaalpost) exported from Nmbrs must be booked in Exact Online as a memoriaal - only in the payroll role on a locally served model, with a two-step approval.
version: 1.0.0
license: Apache-2.0
metadata:
  hermes:
    tags: [accounting, exact-online, netherlands, payroll, nmbrs]
    related_skills: [nl-conventions, maandafsluiting]
    config:
    - key: accounting.payroll_local_models
      description: Comma-separated model names that are served locally and may process payroll data; empty means payroll is refused
      default: ""
---

# Loonjournaalpost: Nmbrs payroll journal to Exact

Works with the `exact` MCP server (`draft_general_journal_entry`, `process_entry`). Follow
`nl-conventions`. Payroll data is personal data; the guards below come first.

## Guards: stop when one fails

1. **Payroll role, local model only.** The model of this session must be one of
   `accounting.payroll_local_models` (shown in the skill config when the skill loads). The
   list is empty or the model is not on it: stop with "Payroll runs only in the payroll
   role on a local model." Do not read the file.
2. **No personal data out.** No employee name, BSN, IBAN or per-person amount appears in a
   chat message, an Exact description, a log line or a file outside the payroll folder.
   The entry is booked per G/L account and cost centre, never per employee.
3. **One run once.** The same payroll run is never booked twice (step 4).

## Input

The journal export (journaalpost) of one payroll run from Nmbrs, CSV or XLSX, that the user puts in the downloads folder. It holds per line the G/L
account code as set in Nmbrs, a description, debit, credit and optionally cost centre or
department, plus the run (period and run number). An export with employee-level lines is
aggregated per G/L account and cost centre before anything else.

## Procedure

1. Read the file; take period (`YYYY-MM`) and run number from the export. Neither found:
   stop and ask.
2. Aggregate per (G/L account, cost centre); debit positive, credit negative.
3. Check: total debit equals total credit to the cent. The RGS meaning of every account is
   payroll-plausible (`WPer*` costs, `BSchBepLhe`, `BSchSalNet`, `BSchOvsStp`,
   `BSchTusTsa`, `BSchSalTvg`); an account outside these: list it in the request as a
   question, do not drop it.
4. Duplicate check: `list_transactions` for the period on the net wages G/L account; a line
   with `Loonjournaal|<period>|<run>` exists: stop and report it.
5. Resolve each code with `list_gl_accounts`; an unknown code: stop and ask.
6. `draft_general_journal_entry`, `dry_run: true`: the memoriaal journal (`list_journals`
   with `kind: general`), entry date the last day of the period, the aggregated lines,
   description parts `supplier` = `Loonjournaal`, `period`, `ref` = `<run>`,
   `short` = `loonjournaal <maand>`.

## Two-step approval

1. **Draft.** Approval request P1 on the approval channel: period, run, total gross wages
   (sum of `WPerLes*`), total wage tax, total net wages, number of lines. No per-person
   figure. On `boek <id>`: the same call with `dry_run: false`; the entry is open in Exact.
2. **Final.** Read the entry back (`list_transactions` on the net wages account). Send a
   second request P2 with the Exact entry number and the same totals, asking to compare
   them with the Nmbrs payroll summary of the run. Only on `boek <id>` of P2:
   `process_entry` with `dry_run: true`, then `dry_run: false`.

`afkeuren <id> <reden>` on P1 writes nothing; on P2 it leaves the entry open with the
reason for the accountant. The two approvals are separate messages; one answer never
covers both.
