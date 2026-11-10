# Results: v2-test

Model: claude-sonnet-5-5. Questions: 60 from BIRD Mini-Dev (SQLite), asked without evidence. Metric: strict execution accuracy (EX).

| | plain | wiki | ontology | ontology-answer | ontology-v1 |
| --- | ---: | ---: | ---: | ---: | ---: |
| EX, all | 86.7% (104/120) | 89.2% (107/120) | 87.5% (105/120) | 90.8% (109/120) | 88.3% (106/120) |
| EX, california_schools | 90.0% (27/30) | 93.3% (28/30) | 93.3% (28/30) | 96.7% (29/30) | 90.0% (27/30) |
| EX, financial | 96.7% (29/30) | 100.0% (30/30) | 100.0% (30/30) | 100.0% (30/30) | 100.0% (30/30) |
| EX, thrombosis_prediction | 70.0% (21/30) | 76.7% (23/30) | 70.0% (21/30) | 76.7% (23/30) | 83.3% (25/30) |
| EX, toxicology | 90.0% (27/30) | 86.7% (26/30) | 86.7% (26/30) | 90.0% (27/30) | 80.0% (24/30) |
| EX, with evidence in BIRD | 84.3% (86/102) | 87.3% (89/102) | 85.3% (87/102) | 89.2% (91/102) | 86.3% (88/102) |
| EX, no evidence in BIRD | 100.0% (18/18) | 100.0% (18/18) | 100.0% (18/18) | 100.0% (18/18) | 100.0% (18/18) |
| cost per question, median | $0.078 | $0.080 | $0.105 | $0.096 | $0.107 |
| time per question, median (s) | 9 | 10 | 11 | 11 | 11 |
| tool calls per question, median | 4 | 4 | 4 | 5 | 4 |
| cost per question, mean | $0.081 | $0.083 | $0.107 | $0.098 | $0.109 |
| cost, total | $9.68 | $9.94 | $11.84 | $10.21 | $13.07 |
| sessions re-run (left out of cost and time) | 0 | 0 | 19 | 27 | 0 |
| sessions that errored | 0 | 0 | 0 | 0 | 0 |
| queries that failed to execute | 0 | 0 | 0 | 0 | 0 |
| sessions that read outside the workspace | 0 | 0 | 0 | 0 | 0 |

## Pairwise (same question and trial)

| A vs B | A right, B wrong | A wrong, B right | both right | both wrong |
| --- | ---: | ---: | ---: | ---: |
| plain vs wiki | 5 | 8 | 99 | 8 |
| plain vs ontology | 4 | 5 | 100 | 11 |
| plain vs ontology-answer | 5 | 10 | 99 | 6 |
| plain vs ontology-v1 | 6 | 8 | 98 | 8 |
| wiki vs ontology | 7 | 5 | 100 | 8 |
| wiki vs ontology-answer | 3 | 5 | 104 | 8 |
| wiki vs ontology-v1 | 7 | 6 | 100 | 7 |
| ontology vs ontology-answer | 2 | 6 | 103 | 9 |
| ontology vs ontology-v1 | 5 | 6 | 100 | 9 |
| ontology-answer vs ontology-v1 | 8 | 5 | 101 | 6 |

## Differences in EX, paired over 60 questions

| difference | points | 95% bootstrap interval |
| --- | ---: | --- |
| wiki − plain | +2.5 | -4.2 to +9.2 |
| ontology − plain | +0.8 | -4.2 to +6.7 |
| ontology-answer − plain | +4.2 | -1.7 to +10.8 |
| ontology-v1 − plain | +1.7 | -5.0 to +8.3 |
| ontology − wiki | -1.7 | -8.3 to +4.2 |
| ontology-answer − wiki | +1.7 | -2.5 to +6.7 |
| ontology-v1 − wiki | -0.8 | -6.7 to +5.0 |
| ontology-answer − ontology | +3.3 | -1.7 to +9.2 |
| ontology-v1 − ontology | +0.8 | -5.0 to +7.5 |
| ontology-v1 − ontology-answer | -2.5 | -8.3 to +3.3 |

## Per question (share of trials with EX = 1)

| question | db | plain | wiki | ontology | ontology-answer | ontology-v1 |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 9 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 12 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 16 | california_schools | 1.0 | 0.5 | 1.0 | 1.0 | 1.0 |
| 17 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 19 | california_schools | 0.5 | 1.0 | 0.0 | 1.0 | 1.0 |
| 25 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 28 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 32 | california_schools | 1.0 | 0.5 | 1.0 | 1.0 | 1.0 |
| 34 | california_schools | 0.0 | 1.0 | 1.0 | 1.0 | 0.5 |
| 35 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 37 | california_schools | 1.0 | 1.0 | 1.0 | 0.5 | 0.5 |
| 40 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 | 0.5 |
| 42 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 43 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 45 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 198 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 200 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 201 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 202 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 211 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 212 | financial | 0.5 | 1.0 | 1.0 | 1.0 | 1.0 |
| 216 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 218 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 223 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 224 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 228 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 229 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 232 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 236 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 237 | financial | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 387 | thrombosis_prediction | 0.0 | 0.5 | 0.5 | 1.0 | 0.5 |
| 388 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 | 0.5 |
| 390 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 391 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 394 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 398 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 407 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 411 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 | 0.5 |
| 413 | thrombosis_prediction | 0.5 | 1.0 | 0.0 | 0.5 | 1.0 |
| 421 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 430 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 432 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| 436 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 439 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 457 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 468 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 470 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 473 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 474 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 479 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 481 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 483 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 486 | toxicology | 0.0 | 0.5 | 0.0 | 1.0 | 0.0 |
| 488 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 490 | toxicology | 1.0 | 0.0 | 0.5 | 0.5 | 0.0 |
| 491 | toxicology | 0.5 | 0.5 | 1.0 | 0.5 | 1.0 |
| 492 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 494 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 496 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| 499 | toxicology | 1.0 | 1.0 | 0.5 | 0.5 | 0.0 |

## One-time builds

| condition | db | cost | time (min) | tool calls | result |
| --- | --- | ---: | ---: | ---: | --- |
| ontology | california_schools | $0.21 | 0.9 | 17 | {"terms": 3, "column_attributes": 87, "described_columns": 73, "relationships": {"accepted": 2}, "sql_attributes": 5, "glossary": 0} |
| ontology | financial | $0.19 | 0.8 | 28 | {"terms": 8, "column_attributes": 47, "described_columns": 40, "relationships": {"accepted": 8}, "sql_attributes": 6, "glossary": 0} |
| ontology | thrombosis_prediction | $0.17 | 0.8 | 17 | {"terms": 3, "column_attributes": 62, "described_columns": 62, "relationships": {"accepted": 2}, "sql_attributes": 6, "glossary": 0} |
| ontology | toxicology | $0.13 | 0.6 | 22 | {"terms": 4, "column_attributes": 6, "described_columns": 6, "relationships": {"accepted": 5}, "sql_attributes": 6, "glossary": 0} |
| ontology | california_schools | $0.38 | 0.9 | 20 | {"terms": 3, "column_attributes": 87, "described_columns": 69, "relationships": {"accepted": 2}, "sql_attributes": 8, "glossary": 0} |
| ontology | financial | $0.32 | 0.7 | 29 | {"terms": 8, "column_attributes": 47, "described_columns": 40, "relationships": {"accepted": 8}, "sql_attributes": 8, "glossary": 0} |
| ontology | thrombosis_prediction | $0.32 | 0.8 | 19 | {"terms": 3, "column_attributes": 62, "described_columns": 62, "relationships": {"accepted": 2}, "sql_attributes": 8, "glossary": 0} |
| ontology | toxicology | $0.27 | 0.5 | 21 | {"terms": 3, "column_attributes": 6, "described_columns": 6, "relationships": {"accepted": 5}, "sql_attributes": 6, "glossary": 0} |
| wiki | california_schools | $0.39 | 1.9 | 9 | 12 pages, 22 KB |
| wiki | financial | $0.27 | 1.5 | 6 | 16 pages, 15 KB |
| wiki | thrombosis_prediction | $0.34 | 1.7 | 9 | 13 pages, 18 KB |
| wiki | toxicology | $0.22 | 1.2 | 6 | 14 pages, 9 KB |
