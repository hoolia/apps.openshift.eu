# RGS 3.6 MKB subset: source and version

`rgs-3.6-mkb.csv` is a filtered copy of the public Referentie GrootboekSchema (RGS).

| Item | Value |
|---|---|
| Version | RGS 3.6, definitive version, based on NT18 |
| Source | `https://www.referentiegrootboekschema.nl/sites/default/files/kennisbank/Definitieve%20versie%20RGS%203.6.xlsx` |
| Source page | `https://www.referentiegrootboekschema.nl/definitieve-versie-rgs-36` |
| SHA-256 of the workbook | `4322efc90b2b92801bac0a95874d89cddb3e8fbc8967fc0ada4d2348c194035a` |
| Sheet | `MKB-RGS3.6-def` |
| Filter | `Basis` = 1, not `Inactief`, `Nivo` 1 to 4 |
| Rows | 1317 codes |
| Licence | RGS is a public standard, free of licence costs; its participants place it under Creative Commons Zero (`https://www.referentiegrootboekschema.nl/voorstel-licentievrij-gebruik-rgs`) |

The descriptions are the short descriptions (`Omschrijving (verkort)`) of the workbook,
with white space collapsed and nothing else changed.

## Regenerate

Download the workbook above, then:

    python3 -I scripts/build-rgs-subset.py "Definitieve versie RGS 3.6.xlsx" references/rgs-3.6-mkb.csv

The script refuses a workbook with another SHA-256. Moving to a newer RGS version is a new
pin: change the URL, the hash and the version here and in the script, and review the
codes the skills name in `SKILL.md`.
