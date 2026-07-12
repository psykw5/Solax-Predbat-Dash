# SolaX Failed Workbook Investigation

Investigation date: 2026-07-12

Scope: investigate the single workbook previously recorded as `file_read_error` during SolaX Plant Report ingestion.

## Result

The workbook is not corrupted and is not a different report type.

It is the same SolaX Daily Plant Report structure, but the expected header row is blank. The data rows still use the normal seven-column order:

1. Row number
2. Update time
3. Daily PV yield
4. Daily inverter output
5. Daily exported energy
6. Daily consumed energy
7. Daily imported energy

The file contains interval data from `2026-04-03 00:00:00` to `2026-04-30 23:55:00`.

## Root Cause

Parser defect.

The previous reader required row 2 to contain the standard SolaX headers. This workbook has a blank row 2, so the parser rejected it even though the data table itself is compatible with the existing Plant Report format.

## Fix

The reader now supports a narrow fallback for this alternate export shape:

- Row 2 is blank.
- The workbook has at least the known seven Plant Report columns.
- The timestamp column in row 3 onward parses as timestamps.
- The known SolaX column order is then applied explicitly.

This does not weaken handling for genuinely different workbook types because the fallback only activates when the blank-header layout still matches the known Plant Report column order.

## Verification

The formerly failed workbook now parses successfully:

- Parsed rows: `7774`
- Earliest parsed timestamp: `2026-04-03 00:00:00`
- Latest parsed timestamp: `2026-04-30 23:55:00`

Full ETL result after the fix:

- Files found: `43`
- Files processed: `43`
- Files failed: `0`
- Canonical intervals: `361079`
- Validation events: `12602`

Automated test result:

```text
Ran 18 tests
OK
```

## Data Safety

The original workbook was not modified.

No account names, email addresses, API tokens, or inverter serial numbers are included in this report.
