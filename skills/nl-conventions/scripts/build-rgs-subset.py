"""Build references/rgs-3.6-mkb.csv from the published RGS 3.6 workbook.

Usage: python3 -I build-rgs-subset.py <Definitieve versie RGS 3.6.xlsx> <out.csv>

Selection, as in example 1 of the workbook's Recap sheet: sheet MKB-RGS3.6-def,
filter Basis, no inactive code, levels 1 to 4 (level 5 holds mutation detail such as
opening balance and additions, which the ledger of an MKB administration does not
use as separate accounts). Rows specific to an eenmanszaak or VOF (filter EZ/VOF) are
kept and marked, so a BV can drop them.
"""

import csv
import hashlib
import sys

import openpyxl

SHA256 = "4322efc90b2b92801bac0a95874d89cddb3e8fbc8967fc0ada4d2348c194035a"
SHEET = "MKB-RGS3.6-def"


def main(src, out):
    digest = hashlib.sha256(open(src, "rb").read()).hexdigest()
    if digest != SHA256:
        sys.exit(f"unexpected workbook: sha256 {digest}, want {SHA256}")
    ws = openpyxl.load_workbook(src, read_only=True)[SHEET]
    rows = list(ws.iter_rows(values_only=True))[2:]
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["referentiecode", "referentienummer", "sortering", "nivo", "dc", "omschrijving", "omslagcode", "alleen_ez_vof", "alleen_bv"])
        for r in rows:
            code, omslag, sort, nr, short, _desc, dc, nivo, basis = r[:9]
            inactief, ez_vof, bv = r[12], r[15], r[16]
            if not code or basis != 1 or nivo not in (1, 2, 3, 4):
                continue
            if inactief not in (None, "", " "):
                continue
            if isinstance(nr, int) and nivo >= 3:
                nr = f"{nr:07d}"
            w.writerow([code, nr or "", sort or "", nivo, dc or "", " ".join(str(short).split()), omslag or "", 1 if ez_vof == 1 else "", 1 if bv == 1 else ""])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
