# BTW rubriek to Exact VAT code

Decide the rubriek of the Dutch VAT return (aangifte omzetbelasting) for each line, then
pick the division's VAT code with `list_vat_codes`. The code must match the percentage, the
side (`use: purchase` or `sales`) and the reverse charge flag; a blocked code is never used.

## Sales

| Situation | Rubriek | Percentage | Reverse charge |
|---|---|---|---|
| domestic, standard rate | 1a | 21 | no |
| domestic, reduced rate | 1b | 9 | no |
| domestic, 0% or not taxed in NL | 1e | 0 | no |
| domestic, VAT shifted to the customer (verlegd) | 2a | 0 | yes |
| goods to a business in another EU country | 3b | 0 | yes (ICP) |
| services to a business in another EU country | 3b | 0 | yes (ICP) |
| export outside the EU | 3a | 0 | no |

A customer in another EU country needs a valid VAT number on the account for 3b; without
it, stop and report.

## Purchases

| Situation | Rubriek | Percentage | Reverse charge |
|---|---|---|---|
| domestic supplier, standard rate | 5b | 21 | no |
| domestic supplier, reduced rate | 5b | 9 | no |
| domestic supplier, no VAT (exempt, KOR) | none | 0 | no |
| EU supplier, invoice without VAT, goods or services | 4b and 5b | 21 or 9 | yes |
| non-EU supplier of services, invoice without VAT | 4a and 5b | 21 or 9 | yes |
| domestic, VAT shifted to us (verlegd, e.g. subcontracting) | 2a and 5b | 21 | yes |

- Reverse charge: the code books the output VAT and the input VAT in one line; the line
  amount is excl. VAT and no VAT amount is paid to the supplier.
- VAT that may not be deducted (private use, entertainment above the limit, an exempt
  activity) goes to cost: RGS `WBedAdlNbo`, or a VAT code that is not deductible when the
  division has one.
- Invoice VAT that does not equal percentage times base within 1 cent per rate: stop and
  report, never adjust the amount.

## Memoriaal

General journal entries (depreciation, payroll, accruals) carry no VAT code, except a
correction of a VAT booking, which uses the original code.
