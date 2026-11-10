# Results: main

Model: claude-sonnet-5-5. Questions: 60 from BIRD Mini-Dev (SQLite), asked without evidence. Metric: strict execution accuracy (EX).

| | plain | wiki | ontology |
| --- | ---: | ---: | ---: |
| EX, all | 79.2% (95/120) | 80.0% (96/120) | 75.8% (91/120) |
| EX, california_schools | 73.3% (22/30) | 76.7% (23/30) | 66.7% (20/30) |
| EX, financial | 70.0% (21/30) | 73.3% (22/30) | 63.3% (19/30) |
| EX, thrombosis_prediction | 83.3% (25/30) | 80.0% (24/30) | 83.3% (25/30) |
| EX, toxicology | 90.0% (27/30) | 90.0% (27/30) | 90.0% (27/30) |
| EX, with evidence in BIRD | 77.9% (81/104) | 77.9% (81/104) | 73.1% (76/104) |
| EX, no evidence in BIRD | 87.5% (14/16) | 93.8% (15/16) | 93.8% (15/16) |
| cost per question, median | $0.082 | $0.085 | $0.116 |
| time per question, median (s) | 9 | 10 | 11 |
| tool calls per question, median | 3 | 4 | 4 |
| cost, total | $10.13 | $10.51 | $14.13 |
| sessions that errored | 0 | 0 | 0 |
| queries that failed to execute | 0 | 0 | 0 |
| sessions that read outside the workspace | 0 | 0 | 0 |

## Pairwise (same question and trial)

| A vs B | A right, B wrong | A wrong, B right | both right | both wrong |
| --- | ---: | ---: | ---: | ---: |
| plain vs wiki | 5 | 6 | 90 | 19 |
| plain vs ontology | 12 | 8 | 83 | 17 |
| wiki vs ontology | 11 | 6 | 85 | 18 |

## Differences in EX, paired over 60 questions

| difference | points | 95% bootstrap interval |
| --- | ---: | --- |
| wiki − plain | +0.8 | -4.2 to +5.8 |
| ontology − plain | -3.3 | -10.0 to +3.3 |
| ontology − wiki | -4.2 | -11.7 to +3.3 |

## Per question (share of trials with EX = 1)

| question | db | plain | wiki | ontology |
| ---: | --- | ---: | ---: | ---: |
| 0 | california_schools | 0.5 | 0.0 | 0.0 |
| 3 | california_schools | 1.0 | 1.0 | 1.0 |
| 6 | california_schools | 0.0 | 0.0 | 0.0 |
| 8 | california_schools | 0.5 | 0.5 | 0.5 |
| 11 | california_schools | 1.0 | 1.0 | 0.5 |
| 13 | california_schools | 1.0 | 1.0 | 1.0 |
| 18 | california_schools | 1.0 | 1.0 | 1.0 |
| 23 | california_schools | 1.0 | 1.0 | 1.0 |
| 27 | california_schools | 1.0 | 1.0 | 1.0 |
| 29 | california_schools | 0.5 | 1.0 | 0.0 |
| 31 | california_schools | 0.5 | 1.0 | 1.0 |
| 36 | california_schools | 0.0 | 0.5 | 0.0 |
| 39 | california_schools | 1.0 | 1.0 | 1.0 |
| 46 | california_schools | 1.0 | 0.5 | 1.0 |
| 48 | california_schools | 1.0 | 1.0 | 1.0 |
| 199 | financial | 1.0 | 1.0 | 1.0 |
| 204 | financial | 0.0 | 0.0 | 1.0 |
| 205 | financial | 0.5 | 1.0 | 1.0 |
| 206 | financial | 1.0 | 1.0 | 1.0 |
| 207 | financial | 1.0 | 1.0 | 1.0 |
| 210 | financial | 1.0 | 1.0 | 1.0 |
| 213 | financial | 0.5 | 0.5 | 0.5 |
| 214 | financial | 1.0 | 1.0 | 0.5 |
| 217 | financial | 0.5 | 0.5 | 0.5 |
| 219 | financial | 1.0 | 1.0 | 0.0 |
| 220 | financial | 0.0 | 0.0 | 0.0 |
| 221 | financial | 0.5 | 0.0 | 0.0 |
| 235 | financial | 0.5 | 1.0 | 0.0 |
| 238 | financial | 1.0 | 1.0 | 1.0 |
| 239 | financial | 1.0 | 1.0 | 1.0 |
| 392 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 399 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 402 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 404 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 406 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 420 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 422 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 424 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 427 | thrombosis_prediction | 0.0 | 0.0 | 0.5 |
| 440 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 441 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 443 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 452 | thrombosis_prediction | 0.0 | 0.0 | 0.0 |
| 454 | thrombosis_prediction | 0.5 | 0.0 | 0.0 |
| 463 | thrombosis_prediction | 1.0 | 1.0 | 1.0 |
| 469 | toxicology | 1.0 | 1.0 | 1.0 |
| 471 | toxicology | 1.0 | 1.0 | 1.0 |
| 472 | toxicology | 1.0 | 1.0 | 1.0 |
| 475 | toxicology | 1.0 | 1.0 | 1.0 |
| 476 | toxicology | 1.0 | 1.0 | 1.0 |
| 477 | toxicology | 1.0 | 1.0 | 1.0 |
| 480 | toxicology | 1.0 | 1.0 | 1.0 |
| 482 | toxicology | 1.0 | 1.0 | 1.0 |
| 484 | toxicology | 1.0 | 1.0 | 1.0 |
| 485 | toxicology | 0.5 | 0.5 | 0.5 |
| 487 | toxicology | 1.0 | 1.0 | 1.0 |
| 489 | toxicology | 1.0 | 1.0 | 1.0 |
| 495 | toxicology | 1.0 | 1.0 | 1.0 |
| 497 | toxicology | 0.0 | 0.0 | 0.0 |
| 498 | toxicology | 1.0 | 1.0 | 1.0 |

## One-time builds

| condition | db | cost | time (min) | tool calls | result |
| --- | --- | ---: | ---: | ---: | --- |
| ontology | california_schools | $0.38 | 0.9 | 20 | {"terms": 3, "column_attributes": 87, "described_columns": 69, "relationships": {"accepted": 2}, "sql_attributes": 8, "glossary": 0} |
| ontology | financial | $0.32 | 0.7 | 29 | {"terms": 8, "column_attributes": 47, "described_columns": 40, "relationships": {"accepted": 8}, "sql_attributes": 8, "glossary": 0} |
| ontology | thrombosis_prediction | $0.32 | 0.8 | 19 | {"terms": 3, "column_attributes": 62, "described_columns": 62, "relationships": {"accepted": 2}, "sql_attributes": 8, "glossary": 0} |
| ontology | toxicology | $0.27 | 0.5 | 21 | {"terms": 3, "column_attributes": 6, "described_columns": 6, "relationships": {"accepted": 5}, "sql_attributes": 6, "glossary": 0} |
| wiki | california_schools | $0.39 | 1.9 | 9 | 12 pages, 22 KB |
| wiki | financial | $0.27 | 1.5 | 6 | 16 pages, 15 KB |
| wiki | thrombosis_prediction | $0.34 | 1.7 | 9 | 13 pages, 18 KB |
| wiki | toxicology | $0.22 | 1.2 | 6 | 14 pages, 9 KB |
