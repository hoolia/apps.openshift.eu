# Few-shot booking examples (private)

This directory is a placeholder. Booking examples taken from a real journal contain
company data and are never committed to this repository.

## Where they come from

The deployment mounts or clones a private store into the agent and names it in the skill
config key `accounting.fewshot_dir` (Hermes `config.yaml`, `skills.config.accounting.fewshot_dir`).
When the key is empty or the directory does not exist, the skills work without examples
and say so in their report.

## Format

One YAML file per example, the same booking format as the evaluation cases
(`evals/accounting-nl/README.md` in the source repository), with `synthetic: false`:

    id: fs-0001
    kind: invoice            # invoice, bank_line, month_end, payroll, depreciation
    input:                   # what the agent saw: invoice fields, bank line, journal
      supplier: ...
    expected:
      bookings:
      - tool: draft_purchase_entry
        description: {supplier: ..., period: 2026-03, ref: ..., short: ...}
        lines:
        - {rgs: WBedKanSof, gl: "4206115", amount: 100.00, vat: {rubriek: 5b, percentage: 21}}

## Use

Before proposing a booking, look for examples with the same supplier, then the same RGS
code; follow the account and VAT choice of the most recent one, and name the example id
in the proposal.
