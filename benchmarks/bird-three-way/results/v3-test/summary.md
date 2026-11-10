# Results: v3-test

Model: claude-sonnet-5-5. Questions: 60 from BIRD Mini-Dev (SQLite), asked without evidence. Metric: strict execution accuracy (EX).

| | ontology | ontology-answer |
| --- | ---: | ---: |
| EX, all | 87.5% (105/120) | 88.3% (106/120) |
| EX, california_schools | 93.3% (28/30) | 90.0% (27/30) |
| EX, financial | 100.0% (30/30) | 100.0% (30/30) |
| EX, thrombosis_prediction | 73.3% (22/30) | 73.3% (22/30) |
| EX, toxicology | 83.3% (25/30) | 90.0% (27/30) |
| EX, with evidence in BIRD | 85.3% (87/102) | 86.3% (88/102) |
| EX, no evidence in BIRD | 100.0% (18/18) | 100.0% (18/18) |
| cost per question, median | $0.106 | $0.095 |
| time per question, median (s) | 12 | 12 |
| tool calls per question, median | 4 | 5 |
| cost per question, mean | $0.108 | $0.098 |
| cost, total | $12.97 | $11.71 |
| sessions that errored | 0 | 0 |
| queries that failed to execute | 0 | 0 |
| sessions that read outside the workspace | 0 | 0 |

## Pairwise (same question and trial)

| A vs B | A right, B wrong | A wrong, B right | both right | both wrong |
| --- | ---: | ---: | ---: | ---: |
| ontology vs ontology-answer | 3 | 4 | 102 | 11 |

## Differences in EX, paired over 60 questions

| difference | points | 95% bootstrap interval |
| --- | ---: | --- |
| ontology-answer − ontology | +0.8 | -5.0 to +6.7 |

## Per question (share of trials with EX = 1)

| question | db | ontology | ontology-answer |
| ---: | --- | ---: | ---: |
| 9 | california_schools | 1.0 | 1.0 |
| 12 | california_schools | 1.0 | 1.0 |
| 16 | california_schools | 1.0 | 1.0 |
| 17 | california_schools | 1.0 | 1.0 |
| 19 | california_schools | 0.0 | 1.0 |
| 25 | california_schools | 1.0 | 1.0 |
| 28 | california_schools | 1.0 | 1.0 |
| 32 | california_schools | 1.0 | 1.0 |
| 34 | california_schools | 1.0 | 0.0 |
| 35 | california_schools | 1.0 | 1.0 |
| 37 | california_schools | 1.0 | 0.5 |
| 40 | california_schools | 1.0 | 1.0 |
| 42 | california_schools | 1.0 | 1.0 |
| 43 | california_schools | 1.0 | 1.0 |
| 45 | california_schools | 1.0 | 1.0 |
| 198 | financial | 1.0 | 1.0 |
| 200 | financial | 1.0 | 1.0 |
| 201 | financial | 1.0 | 1.0 |
| 202 | financial | 1.0 | 1.0 |
| 211 | financial | 1.0 | 1.0 |
| 212 | financial | 1.0 | 1.0 |
| 216 | financial | 1.0 | 1.0 |
| 218 | financial | 1.0 | 1.0 |
| 223 | financial | 1.0 | 1.0 |
| 224 | financial | 1.0 | 1.0 |
| 228 | financial | 1.0 | 1.0 |
| 229 | financial | 1.0 | 1.0 |
| 232 | financial | 1.0 | 1.0 |
| 236 | financial | 1.0 | 1.0 |
| 237 | financial | 1.0 | 1.0 |
| 387 | thrombosis_prediction | 1.0 | 1.0 |
| 388 | thrombosis_prediction | 0.0 | 0.0 |
| 390 | thrombosis_prediction | 1.0 | 1.0 |
| 391 | thrombosis_prediction | 1.0 | 1.0 |
| 394 | thrombosis_prediction | 1.0 | 1.0 |
| 398 | thrombosis_prediction | 1.0 | 1.0 |
| 407 | thrombosis_prediction | 1.0 | 1.0 |
| 411 | thrombosis_prediction | 0.0 | 0.0 |
| 413 | thrombosis_prediction | 0.0 | 0.0 |
| 421 | thrombosis_prediction | 1.0 | 1.0 |
| 430 | thrombosis_prediction | 1.0 | 1.0 |
| 432 | thrombosis_prediction | 0.0 | 0.0 |
| 436 | thrombosis_prediction | 1.0 | 1.0 |
| 439 | thrombosis_prediction | 1.0 | 1.0 |
| 457 | thrombosis_prediction | 1.0 | 1.0 |
| 468 | toxicology | 1.0 | 1.0 |
| 470 | toxicology | 1.0 | 1.0 |
| 473 | toxicology | 1.0 | 1.0 |
| 474 | toxicology | 1.0 | 1.0 |
| 479 | toxicology | 1.0 | 1.0 |
| 481 | toxicology | 1.0 | 1.0 |
| 483 | toxicology | 1.0 | 1.0 |
| 486 | toxicology | 0.5 | 0.5 |
| 488 | toxicology | 1.0 | 1.0 |
| 490 | toxicology | 0.0 | 0.0 |
| 491 | toxicology | 1.0 | 1.0 |
| 492 | toxicology | 1.0 | 1.0 |
| 494 | toxicology | 1.0 | 1.0 |
| 496 | toxicology | 1.0 | 1.0 |
| 499 | toxicology | 0.0 | 1.0 |

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
