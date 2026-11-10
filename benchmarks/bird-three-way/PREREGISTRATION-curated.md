# Pre-registration: the layers with accumulated knowledge

Written on 2026-11-10 before any session of the runs below, after a pilot on one database (`toxicology` builds and two questions with Haiku, to check the scripts; no accuracy was looked at). The script is `curated.py`, the analysis `analyze_curated.py`, both committed with this file.

## Why

The fourth round (`PREREGISTRATION.md`) left the ontology 1.8 points above plain access, inconclusive. All rounds so far gave the layers only what one unreviewed build pass extracted from the schema and the column descriptions. A semantic layer is meant to be curated: verified question/SQL pairs and the definitions behind them accumulate as it is used. This round tests the setups in that state, with the ontology configured as its documentation intends (answering steps named in the prompt, so `validate_sql` and the example search are used) and with `describe_table` showing the ontology's column descriptions (experiment server commit `0aba40f`: #10 and #12 merged, plus that change).

## Design

All 500 Mini-Dev questions (2026 annotations, 11 databases), split per database into two folds at random (`questions_cv2026.json`, seed 20261111). A question of one fold is answered with the knowledge of the other fold only: the other fold's questions, evidence notes and gold SQL, as `docs/verified_queries.md`. No setup sees a question's own note or SQL. The 60 questions the server changes of #10 were designed against are included; this is stated, not corrected for.

Every condition has the database's column descriptions and `verified_queries.md` in `docs/`:

| Condition | What it adds |
| --- | --- |
| `plain-notes` | nothing: the database through `sqlite3`, and `docs/` |
| `wiki-curated` | the LLM Wiki of the earlier rounds after Sonnet ingested `verified_queries.md` as a new source |
| `ontology-curated` | the MCP server with the `answer` tools; the pairs saved with `save_example_query`, then Sonnet carried the notes into glossary entries, SQL attributes and descriptions; the prompt names the server's answering steps |

Runs: `curated-sonnet` (Sonnet 5.5, one trial per question and condition) and `curated-haiku` (Haiku 5.5, two trials). The curation builds are made by Sonnet for both.

## Primary endpoint and decision rule

Sonnet, `ontology-curated − plain-notes`: mean over the 500 questions of the difference in execution accuracy, paired bootstrap over questions (20,000 resamples, seed 20261111). Better if the 95% interval lies above 0, worse if below, equivalent within 3 points if neither and the 90% interval lies inside −3 to +3, otherwise inconclusive.

## Secondary

The same for `ontology-curated − wiki-curated` and `wiki-curated − plain-notes` (Sonnet), and all three with Haiku. Cost per question. Descriptive only: each condition against plain access without accumulated knowledge from the fourth round (440 shared questions), to show what the knowledge itself adds.

## Handling problems

As in `PREREGISTRATION.md`: errored sessions are re-run once and left out of cost; sessions that read outside their workspace are excluded and reported; a failed build is reported, not dropped.
