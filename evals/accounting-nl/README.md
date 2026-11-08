# Dutch accounting evaluation set

Measures whether the accounting skills (`skills/nl-conventions` and the skills that book
through the `exact` MCP server) propose the right bookings. The cases in this repository
are synthetic; the real set lives in a private store and uses the same format.

| Set | Invoices | Bank lines | Month ends | Where |
|---|---|---|---|---|
| synthetic | 3 | 1 | 1 | `cases/synthetic/` |
| full | 30 | 10 | 2 | private store, never committed |

## Case format

One YAML file per case:

| Key | Content |
|---|---|
| `id` | unique id, also the file name of its prediction |
| `kind` | `invoice`, `bank_line` or `month_end` |
| `synthetic` | `true` for invented data; the private set uses `false` |
| `input` | what the agent sees: invoice fields and lines, a bank line, or a period with its asset schedule and recurring items |
| `expected.bookings` | the bookings the agent must propose |

A booking:

    - tool: draft_purchase_entry          # or draft_general_journal_entry, create_matchset
      description: {supplier: ..., period: YYYY-MM, ref: ...}
      lines:
      - {rgs: WBedKanSof, amount: 100.00, vat: {rubriek: 5b, percentage: 21, amount: 21.00, reverse_charge: false}}

- Accounts are RGS 3.6 codes from `skills/nl-conventions/references/rgs-3.6-mkb.csv`, not
  division G/L codes, so a case is valid for every division.
- VAT is the BTW rubriek and percentage, not an Exact VAT code.
- A purchase entry line amount is excl. VAT; a journal line amount is signed, debit
  positive, and the lines of a journal entry sum to 0.
- The `short` description part is free text and not scored.

## Predictions

One YAML file per case with the same `id` and a `bookings` list in the same format. Produce
them by running the agent on each case input with the write tools in dry run, and mapping
each dry-run payload to the booking format (G/L account to its RGS code).

## Run

    python3 run.py validate cases/synthetic                 # schema, RGS codes, balances
    python3 run.py validate --full <private-cases-dir>      # also the 30/10/2 composition
    python3 run.py score <cases-dir> <predictions-dir> --min-pass 90
    python3 run.py selftest                                 # part of ci/check.sh

A case passes when every expected booking is proposed with the same tool, description
`supplier`, `period` and `ref`, and the same lines (RGS code, amount to the cent, VAT
rubriek, percentage, amount and reverse charge), and nothing else is proposed. `score`
prints the pass count per kind, line recall and precision, and the ids that miss; it exits
1 below `--min-pass` percent.

## Adding the real set

Put the private cases in a directory outside this repository (`synthetic: false`, no file
in this repository refers to them) and pass that directory to `validate --full` and
`score`. Never copy a real case into `cases/`.
