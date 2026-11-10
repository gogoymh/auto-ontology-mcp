# Results: v2-dev

Model: claude-sonnet-5-5. Questions: 60 from BIRD Mini-Dev (SQLite), asked without evidence. Metric: strict execution accuracy (EX).

| | ontology | ontology-answer |
| --- | ---: | ---: |
| EX, all | 85.8% (103/120) | 85.0% (102/120) |
| EX, california_schools | 70.0% (21/30) | 76.7% (23/30) |
| EX, financial | 83.3% (25/30) | 76.7% (23/30) |
| EX, thrombosis_prediction | 96.7% (29/30) | 93.3% (28/30) |
| EX, toxicology | 93.3% (28/30) | 93.3% (28/30) |
| EX, with evidence in BIRD | 84.6% (88/104) | 83.7% (87/104) |
| EX, no evidence in BIRD | 93.8% (15/16) | 93.8% (15/16) |
| cost per question, median | $0.110 | $0.095 |
| time per question, median (s) | 12 | 11 |
| tool calls per question, median | 4 | 5 |
| cost per question, mean | $0.113 | $0.102 |
| cost, total | $13.59 | $12.20 |
| sessions that errored | 0 | 0 |
| queries that failed to execute | 0 | 0 |
| sessions that read outside the workspace | 0 | 0 |

## Pairwise (same question and trial)

| A vs B | A right, B wrong | A wrong, B right | both right | both wrong |
| --- | ---: | ---: | ---: | ---: |
| ontology vs ontology-answer | 8 | 7 | 95 | 10 |

## Differences in EX, paired over 60 questions

| difference | points | 95% bootstrap interval |
| --- | ---: | --- |
| ontology-answer − ontology | -0.8 | -6.7 to +4.2 |

## Per question (share of trials with EX = 1)

| question | db | ontology | ontology-answer |
| ---: | --- | ---: | ---: |
| 0 | california_schools | 0.5 | 0.0 |
| 3 | california_schools | 1.0 | 1.0 |
| 6 | california_schools | 0.0 | 0.0 |
| 8 | california_schools | 0.5 | 0.5 |
| 11 | california_schools | 1.0 | 1.0 |
| 13 | california_schools | 1.0 | 1.0 |
| 18 | california_schools | 1.0 | 1.0 |
| 23 | california_schools | 0.5 | 1.0 |
| 27 | california_schools | 1.0 | 1.0 |
| 29 | california_schools | 0.0 | 0.5 |
| 31 | california_schools | 1.0 | 1.0 |
| 36 | california_schools | 0.5 | 0.5 |
| 39 | california_schools | 1.0 | 1.0 |
| 46 | california_schools | 0.5 | 1.0 |
| 48 | california_schools | 1.0 | 1.0 |
| 199 | financial | 1.0 | 1.0 |
| 204 | financial | 1.0 | 0.5 |
| 205 | financial | 1.0 | 1.0 |
| 206 | financial | 1.0 | 1.0 |
| 207 | financial | 1.0 | 1.0 |
| 210 | financial | 1.0 | 1.0 |
| 213 | financial | 0.5 | 0.5 |
| 214 | financial | 1.0 | 1.0 |
| 217 | financial | 1.0 | 1.0 |
| 219 | financial | 1.0 | 1.0 |
| 220 | financial | 1.0 | 0.0 |
| 221 | financial | 0.0 | 0.5 |
| 235 | financial | 0.0 | 0.0 |
| 238 | financial | 1.0 | 1.0 |
| 239 | financial | 1.0 | 1.0 |
| 392 | thrombosis_prediction | 1.0 | 1.0 |
| 399 | thrombosis_prediction | 1.0 | 1.0 |
| 402 | thrombosis_prediction | 1.0 | 1.0 |
| 404 | thrombosis_prediction | 1.0 | 1.0 |
| 406 | thrombosis_prediction | 1.0 | 1.0 |
| 420 | thrombosis_prediction | 1.0 | 1.0 |
| 422 | thrombosis_prediction | 1.0 | 1.0 |
| 424 | thrombosis_prediction | 1.0 | 1.0 |
| 427 | thrombosis_prediction | 0.5 | 0.0 |
| 440 | thrombosis_prediction | 1.0 | 1.0 |
| 441 | thrombosis_prediction | 1.0 | 1.0 |
| 443 | thrombosis_prediction | 1.0 | 1.0 |
| 452 | thrombosis_prediction | 1.0 | 1.0 |
| 454 | thrombosis_prediction | 1.0 | 1.0 |
| 463 | thrombosis_prediction | 1.0 | 1.0 |
| 469 | toxicology | 1.0 | 1.0 |
| 471 | toxicology | 1.0 | 1.0 |
| 472 | toxicology | 1.0 | 1.0 |
| 475 | toxicology | 1.0 | 1.0 |
| 476 | toxicology | 1.0 | 1.0 |
| 477 | toxicology | 1.0 | 1.0 |
| 480 | toxicology | 1.0 | 1.0 |
| 482 | toxicology | 1.0 | 1.0 |
| 484 | toxicology | 1.0 | 1.0 |
| 485 | toxicology | 1.0 | 1.0 |
| 487 | toxicology | 1.0 | 1.0 |
| 489 | toxicology | 1.0 | 1.0 |
| 495 | toxicology | 1.0 | 1.0 |
| 497 | toxicology | 0.0 | 0.0 |
| 498 | toxicology | 1.0 | 1.0 |

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
