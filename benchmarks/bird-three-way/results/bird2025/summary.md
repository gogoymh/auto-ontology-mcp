# Results: bird2025

Model: claude-sonnet-5-5. Questions: 152 from BIRD Mini-Dev (SQLite), asked without evidence. Metric: strict execution accuracy (EX).

| | plain | wiki | ontology-answer | plain-evidence |
| --- | ---: | ---: | ---: | ---: |
| EX, all | 53.3% (162/304) | 52.3% (159/304) | 53.6% (163/304) | 58.6% (89/152) |
| EX, california_schools | 36.7% (22/60) | 28.3% (17/60) | 35.0% (21/60) | 60.0% (18/30) |
| EX, financial | 70.3% (45/64) | 71.9% (46/64) | 73.4% (47/64) | 68.8% (22/32) |
| EX, thrombosis_prediction | 53.0% (53/100) | 53.0% (53/100) | 47.0% (47/100) | 54.0% (27/50) |
| EX, toxicology | 52.5% (42/80) | 53.8% (43/80) | 60.0% (48/80) | 55.0% (22/40) |
| EX, with evidence in BIRD | 53.3% (162/304) | 52.3% (159/304) | 53.6% (163/304) | 58.6% (89/152) |
| EX, no evidence in BIRD | - | - | - | - |
| cost per question, median | $0.075 | $0.077 | $0.093 | $0.071 |
| time per question, median (s) | 8 | 8 | 10 | 6 |
| tool calls per question, median | 3 | 3 | 4 | 3 |
| cost per question, mean | $0.076 | $0.078 | $0.095 | $0.071 |
| cost, total | $23.06 | $23.75 | $28.99 | $10.83 |
| sessions that errored | 0 | 0 | 0 | 0 |
| queries that failed to execute | 0 | 0 | 0 | 0 |
| sessions that read outside the workspace | 0 | 0 | 0 | 0 |

## Pairwise (same question and trial)

| A vs B | A right, B wrong | A wrong, B right | both right | both wrong |
| --- | ---: | ---: | ---: | ---: |
| plain vs wiki | 10 | 7 | 152 | 135 |
| plain vs ontology-answer | 16 | 17 | 146 | 125 |
| plain vs plain-evidence | 11 | 20 | 69 | 52 |
| wiki vs ontology-answer | 12 | 16 | 147 | 129 |
| wiki vs plain-evidence | 11 | 22 | 67 | 52 |
| ontology-answer vs plain-evidence | 15 | 21 | 68 | 48 |

## Differences in EX, paired over 152 questions

| difference | points | 95% bootstrap interval |
| --- | ---: | --- |
| wiki − plain | -1.0 | -3.6 to +1.6 |
| ontology-answer − plain | +0.3 | -3.6 to +4.3 |
| plain-evidence − plain | +5.3 | -1.3 to +11.8 |
| ontology-answer − wiki | +1.3 | -2.6 to +5.3 |
| plain-evidence − wiki | +6.2 | -0.7 to +13.2 |
| plain-evidence − ontology-answer | +4.9 | -2.3 to +12.2 |

## Per question (share of trials with EX = 1)

| question | db | plain | wiki | ontology-answer | plain-evidence |
| ---: | --- | ---: | ---: | ---: | ---: |
| 5 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 |
| 11 | california_schools | 0.0 | 0.0 | 0.0 | 1.0 |
| 12 | california_schools | 0.5 | 0.0 | 0.5 | 1.0 |
| 17 | california_schools | 0.0 | 0.0 | 0.0 | 0.0 |
| 23 | california_schools | 0.5 | 0.0 | 0.5 | 1.0 |
| 24 | california_schools | 1.0 | 0.5 | 0.0 | 1.0 |
| 25 | california_schools | 0.0 | 0.0 | 0.0 | 0.0 |
| 26 | california_schools | 0.0 | 0.0 | 0.0 | 0.0 |
| 27 | california_schools | 0.0 | 0.0 | 0.0 | 0.0 |
| 28 | california_schools | 0.0 | 0.0 | 0.0 | 0.0 |
| 31 | california_schools | 0.5 | 0.0 | 1.0 | 1.0 |
| 32 | california_schools | 0.0 | 0.0 | 0.0 | 1.0 |
| 36 | california_schools | 0.5 | 0.0 | 0.0 | 1.0 |
| 37 | california_schools | 0.0 | 0.0 | 0.0 | 0.0 |
| 39 | california_schools | 1.0 | 1.0 | 1.0 | 0.0 |
| 40 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 |
| 41 | california_schools | 0.0 | 0.0 | 0.0 | 0.0 |
| 45 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 |
| 46 | california_schools | 0.5 | 0.5 | 1.0 | 1.0 |
| 47 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 |
| 48 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 |
| 50 | california_schools | 0.5 | 0.0 | 0.0 | 0.0 |
| 62 | california_schools | 0.0 | 0.0 | 0.0 | 1.0 |
| 72 | california_schools | 0.0 | 0.0 | 0.5 | 0.0 |
| 77 | california_schools | 0.0 | 0.0 | 0.0 | 1.0 |
| 79 | california_schools | 1.0 | 1.0 | 1.0 | 1.0 |
| 82 | california_schools | 0.0 | 0.0 | 0.0 | 1.0 |
| 83 | california_schools | 0.0 | 0.0 | 0.0 | 0.0 |
| 85 | california_schools | 0.0 | 0.0 | 0.0 | 0.0 |
| 87 | california_schools | 0.0 | 0.5 | 0.0 | 1.0 |
| 89 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 92 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 93 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 94 | financial | 0.0 | 0.0 | 0.0 | 0.0 |
| 95 | financial | 1.0 | 0.5 | 1.0 | 1.0 |
| 98 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 99 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 100 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 112 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 115 | financial | 0.0 | 0.0 | 0.0 | 0.0 |
| 116 | financial | 1.0 | 1.0 | 1.0 | 0.0 |
| 117 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 118 | financial | 1.0 | 1.0 | 1.0 | 0.0 |
| 119 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 120 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 125 | financial | 0.0 | 0.0 | 0.0 | 0.0 |
| 128 | financial | 0.0 | 0.0 | 0.5 | 1.0 |
| 129 | financial | 0.0 | 0.0 | 0.0 | 0.0 |
| 136 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 137 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 138 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 145 | financial | 0.0 | 0.0 | 0.0 | 0.0 |
| 149 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 152 | financial | 0.0 | 0.0 | 0.0 | 0.0 |
| 159 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 168 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 169 | financial | 0.0 | 0.5 | 0.5 | 1.0 |
| 173 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 186 | financial | 0.0 | 0.0 | 0.0 | 0.0 |
| 189 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 192 | financial | 1.0 | 1.0 | 1.0 | 1.0 |
| 194 | financial | 0.5 | 1.0 | 0.5 | 0.0 |
| 195 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 197 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 198 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 200 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 201 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 206 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 207 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 208 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 212 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 213 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 215 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 218 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 219 | toxicology | 1.0 | 1.0 | 1.0 | 0.0 |
| 220 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 226 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 227 | toxicology | 0.0 | 0.0 | 1.0 | 0.0 |
| 228 | toxicology | 0.0 | 0.0 | 0.5 | 0.0 |
| 230 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 231 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 232 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 234 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 236 | toxicology | 0.0 | 0.0 | 0.0 | 1.0 |
| 239 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 240 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 242 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 243 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 244 | toxicology | 0.5 | 0.5 | 1.0 | 0.0 |
| 245 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 247 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 248 | toxicology | 0.0 | 0.0 | 0.5 | 0.0 |
| 249 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 253 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 255 | toxicology | 0.0 | 0.0 | 1.0 | 0.0 |
| 260 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 263 | toxicology | 0.0 | 0.0 | 0.0 | 1.0 |
| 268 | toxicology | 0.5 | 1.0 | 0.5 | 1.0 |
| 273 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 281 | toxicology | 0.0 | 0.0 | 0.0 | 0.0 |
| 282 | toxicology | 1.0 | 1.0 | 0.5 | 1.0 |
| 327 | toxicology | 1.0 | 1.0 | 1.0 | 1.0 |
| 1149 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1150 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1152 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1153 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1155 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 1.0 |
| 1156 | thrombosis_prediction | 0.5 | 0.5 | 0.5 | 1.0 |
| 1157 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1162 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 1.0 |
| 1164 | thrombosis_prediction | 0.5 | 1.0 | 1.0 | 1.0 |
| 1166 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1168 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1169 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 1.0 |
| 1171 | thrombosis_prediction | 1.0 | 1.0 | 0.5 | 1.0 |
| 1175 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 0.0 |
| 1179 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1185 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 1.0 |
| 1187 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1189 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1192 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1195 | thrombosis_prediction | 1.0 | 1.0 | 0.0 | 1.0 |
| 1198 | thrombosis_prediction | 1.0 | 0.5 | 0.0 | 1.0 |
| 1201 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1205 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1208 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1209 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1220 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1225 | thrombosis_prediction | 0.5 | 1.0 | 0.0 | 0.0 |
| 1227 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1229 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1231 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1232 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1235 | thrombosis_prediction | 1.0 | 1.0 | 0.5 | 0.0 |
| 1238 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1239 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 0.0 |
| 1241 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1242 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 0.0 |
| 1243 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1247 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1251 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1252 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1254 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1255 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 1.0 |
| 1256 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1257 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1265 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1267 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |
| 1270 | thrombosis_prediction | 1.0 | 0.5 | 1.0 | 1.0 |
| 1275 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 0.0 |
| 1281 | thrombosis_prediction | 1.0 | 1.0 | 1.0 | 1.0 |
| 1302 | thrombosis_prediction | 0.0 | 0.0 | 0.0 | 0.0 |

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
