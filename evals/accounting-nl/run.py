"""Validate and score the Dutch accounting evaluation set.

    python3 run.py validate [--full] <cases-dir>...
    python3 run.py score <cases-dir> <predictions-dir> [--min-pass <percent>]
    python3 run.py selftest

validate  checks every case file: schema, RGS codes against the skill reference,
          balanced journal entries, invoice totals; --full also requires the set
          composition of the real set (30 invoices, 10 bank lines, 2 month ends).
score     compares the bookings of each prediction file (same id, same booking format)
          with the expected bookings of its case and prints the pass counts per kind.
selftest  scores the synthetic cases against themselves and a broken copy.
"""

import argparse
import copy
import csv
import sys
from collections import Counter
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
RGS_CSV = HERE.parent.parent / "skills/nl-conventions/references/rgs-3.6-mkb.csv"
KINDS = {"invoice": 30, "bank_line": 10, "month_end": 2}
TOOLS = {"draft_purchase_entry", "draft_general_journal_entry", "create_matchset", "process_entry"}
RUBRIEKEN = {"1a", "1b", "1c", "1d", "1e", "2a", "3a", "3b", "3c", "4a", "4b", "5b", "none"}


def rgs_codes():
    with open(RGS_CSV, newline="") as fh:
        return {r["referentiecode"] for r in csv.DictReader(fh)}


def cents(x):
    return round(float(x) * 100)


def load_dir(path):
    out = {}
    for f in sorted(Path(path).rglob("*.yaml")):
        doc = yaml.safe_load(f.read_text()) or {}
        doc["_file"] = str(f)
        if doc.get("id") in out:
            raise SystemExit(f"FAIL {f}: duplicate id {doc.get('id')}")
        out[doc.get("id")] = doc
    return out


def booking_errors(b, codes, where):
    errs = []
    if b.get("tool") not in TOOLS:
        errs.append(f"{where}: unknown tool {b.get('tool')}")
    d = b.get("description") or {}
    for part in ("supplier", "period", "ref"):
        if not str(d.get(part) or ""):
            errs.append(f"{where}: description lacks {part}")
        elif "|" in str(d.get(part)):
            errs.append(f"{where}: '|' in description {part}")
    lines = b.get("lines") or []
    if b.get("tool") in ("draft_purchase_entry", "draft_general_journal_entry") and not lines:
        errs.append(f"{where}: no lines")
    for i, ln in enumerate(lines):
        if ln.get("rgs") not in codes:
            errs.append(f"{where} line {i}: RGS code {ln.get('rgs')} not in RGS 3.6 MKB subset")
        vat = ln.get("vat")
        if vat and str(vat.get("rubriek")) not in RUBRIEKEN:
            errs.append(f"{where} line {i}: unknown rubriek {vat.get('rubriek')}")
    if b.get("tool") == "draft_general_journal_entry" and sum(cents(ln["amount"]) for ln in lines):
        errs.append(f"{where}: journal entry does not balance")
    return errs


def case_errors(case, codes):
    f = case["_file"]
    errs = []
    if case.get("kind") not in KINDS:
        errs.append(f"{f}: kind must be one of {sorted(KINDS)}")
    if not isinstance(case.get("synthetic"), bool):
        errs.append(f"{f}: synthetic must be true or false")
    if not case.get("input"):
        errs.append(f"{f}: no input")
    bookings = (case.get("expected") or {}).get("bookings") or []
    if not bookings:
        errs.append(f"{f}: no expected bookings")
    for n, b in enumerate(bookings):
        errs += booking_errors(b, codes, f"{f} booking {n}")
    if case.get("kind") == "invoice":
        inp = case.get("input") or {}
        total = sum(cents(ln["amount_excl"]) + cents(ln["vat_amount"]) for ln in inp.get("lines") or [])
        if total != cents(inp.get("total_incl", 0)):
            errs.append(f"{f}: invoice lines do not add up to total_incl")
        for b in bookings:
            if b.get("tool") == "draft_purchase_entry":
                excl = sum(cents(ln["amount"]) for ln in b.get("lines") or [])
                want = sum(cents(ln["amount_excl"]) for ln in inp.get("lines") or [])
                if excl != want:
                    errs.append(f"{f}: purchase entry excl. VAT differs from the invoice")
    return errs


def line_key(ln):
    vat = ln.get("vat") or {}
    return (ln.get("rgs"), cents(ln.get("amount", 0)), str(vat.get("rubriek", "")), vat.get("percentage"), cents(vat.get("amount", 0)), bool(vat.get("reverse_charge", False)))


def booking_key(b):
    d = b.get("description") or {}
    return (b.get("tool"), str(d.get("supplier")), str(d.get("period")), str(d.get("ref")), tuple(sorted(Counter(line_key(ln) for ln in b.get("lines") or []).items())))


def score_case(expected, predicted):
    want = Counter(booking_key(b) for b in (expected.get("expected") or {}).get("bookings") or [])
    got = Counter(booking_key(b) for b in predicted.get("bookings") or []) if predicted else Counter()
    want_lines = Counter(line_key(ln) for b in (expected.get("expected") or {}).get("bookings") or [] for ln in b.get("lines") or [])
    got_lines = Counter(line_key(ln) for b in (predicted or {}).get("bookings") or [] for ln in b.get("lines") or [])
    hit = sum((want_lines & got_lines).values())
    return want == got, hit, sum(want_lines.values()), sum(got_lines.values())


def score(cases, preds):
    per = {k: [0, 0] for k in KINDS}
    hit = want = got = 0
    for cid, case in cases.items():
        ok, h, w, g = score_case(case, preds.get(cid))
        per[case["kind"]][1] += 1
        per[case["kind"]][0] += ok
        hit, want, got = hit + h, want + w, got + g
        if not ok:
            print(f"MISS {cid}")
    for k, (p, n) in per.items():
        print(f"{k}: {p}/{n} pass")
    passed, total = sum(p for p, _ in per.values()), sum(n for _, n in per.values())
    print(f"lines: recall {hit}/{want}, precision {hit}/{got}")
    print(f"cases: {passed}/{total} pass")
    return passed, total


def cmd_validate(dirs, full):
    codes = rgs_codes()
    errs, counts = [], Counter()
    for d in dirs:
        for case in load_dir(d).values():
            errs += case_errors(case, codes)
            counts[case.get("kind")] += 1
    if full:
        for k, n in KINDS.items():
            if counts[k] != n:
                errs.append(f"set has {counts[k]} {k} cases, the full set needs {n}")
    for e in errs:
        print("FAIL", e)
    print(f"validated {sum(counts.values())} case(s): " + ", ".join(f"{k} {counts[k]}" for k in KINDS))
    return 1 if errs else 0


def cmd_score(cases_dir, preds_dir, min_pass):
    cases = load_dir(cases_dir)
    preds = load_dir(preds_dir)
    passed, total = score(cases, preds)
    return 0 if total and passed * 100 >= min_pass * total else 1


def cmd_selftest():
    cases = load_dir(HERE / "cases/synthetic")
    if cmd_validate([HERE / "cases/synthetic"], False):
        return 1
    preds = {cid: {"id": cid, "bookings": c["expected"]["bookings"]} for cid, c in cases.items()}
    passed, total = score(cases, preds)
    if passed != total:
        print("FAIL selftest: expected bookings do not score as a pass")
        return 1
    broken = copy.deepcopy(preds)
    first = sorted(broken)[0]
    broken[first]["bookings"][0]["lines"][0]["rgs"] = "WBedAdlOad"
    passed, total = score(cases, broken)
    if passed != total - 1:
        print("FAIL selftest: a wrong account is not scored as a miss")
        return 1
    print("selftest PASS")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("--full", action="store_true")
    v.add_argument("dirs", nargs="+")
    s = sub.add_parser("score")
    s.add_argument("cases")
    s.add_argument("predictions")
    s.add_argument("--min-pass", type=float, default=0)
    sub.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "validate":
        return cmd_validate(a.dirs, a.full)
    if a.cmd == "score":
        return cmd_score(a.cases, a.predictions, a.min_pass)
    return cmd_selftest()


if __name__ == "__main__":
    sys.exit(main())
