# Pre-registration: does the ontology answer more questions correctly than plain access?

Written on 2026-11-10, before any session of the runs below was started. Nothing in this file, the question set, the prompts, the builds already made or the server changes after the runs start. The analysis is `analyze_prereg.py`, committed with this file.

## Why another run

Three earlier rounds (README) put the ontology between −3.3 and +4.2 points of plain access, with every 95% interval including zero. Those rounds had 60 to 152 questions, which only rule out differences larger than about 4 points. This run is sized to give a decision either way at a margin of 3 points.

## Questions

`questions_full2026.json`: all 500 questions of BIRD Mini-Dev (SQLite, 2026 annotations) across its 11 databases, minus the 60 questions of `questions.json`, which the server changes in #10 were designed against. 440 questions remain. They include the 60 held-out questions of the second round, on which one rule was removed from #10. Evidence hints are withheld in every condition.

## Conditions

All conditions see the database's column description CSVs (`docs/`). Each question runs in a fresh workspace and store.

| Condition | Model | Role |
| --- | --- | --- |
| `plain` | Sonnet 5.5 | primary comparison |
| `ontology-answer` | Sonnet 5.5 | primary comparison: the server of #10 and #12 merged (local commit `d480777`), `AUTO_ONTOLOGY_TOOLSETS=answer` |
| `wiki` | Sonnet 5.5 | secondary |
| `plain`, `ontology-answer` | Haiku 5.5 | secondary: does a weaker model gain more from the ontology? |

Two trials per question and condition. Run names: `final-sonnet` and `final-haiku`.

Builds: the wikis and ontologies of the four earlier databases are reused (`builds/wiki`, `builds/ontology`, the latter made with #10's server). The other seven databases get a wiki and an ontology built by Sonnet 5.5 with the same prompts as before, the ontologies with the experiment server. The Haiku runs use the same Sonnet-built wikis and ontologies: the layer is built once and queried by a cheaper model.

## Primary endpoint and decision rule

Per question, execution accuracy (BIRD strict EX) averaged over its two trials; the endpoint is the mean over the 440 questions of `ontology-answer − plain` (Sonnet), in percentage points, with a paired bootstrap over questions (20,000 resamples, seed 20261110).

- **Ontology better**: the 95% interval lies above 0.
- **Ontology worse**: the 95% interval lies below 0.
- **Equivalent within 3 points**: neither of the above, and the 90% interval lies inside −3 to +3 (two one-sided tests at 5%).
- **Inconclusive**: none of the above.

## Secondary analyses

Reported with the same intervals, without a decision rule: `wiki − plain` and `ontology-answer − wiki` (Sonnet); `ontology-answer − plain` with Haiku; cost per question (mean over first attempts) for each condition, and the ontology's cost relative to plain; accuracy per database (descriptive).

## Handling problems

- A session that ends in an error is re-run once; the re-run counts for accuracy and is left out of cost, as before. Both counts are reported.
- A session that reads outside its workspace is excluded and reported.
- If a database's build fails, its questions are reported as missing for every condition, not dropped silently.
