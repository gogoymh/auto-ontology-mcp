# Plain vs LLM Wiki vs auto-ontology MCP on BIRD Mini-Dev

This benchmark asks the same data questions three ways and scores the SQL each way produces:

| Condition | What the model gets |
| --- | --- |
| `plain` | The SQLite database (through `sqlite3` in Bash) and BIRD's column description CSVs (`docs/`) |
| `wiki` | The same, plus `wiki/`: an [LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) that the same model built from those sources |
| `ontology` | The auto-ontology MCP server with an ontology the same model built from those sources, plus `docs/`. No Bash, so SQL goes through `run_sql` |

All three use the same model through headless Claude Code (`claude -p`), with the user's CLAUDE.md, plugins, skills and other MCP servers switched off. Each question runs in a fresh copy of its workspace (and, for `ontology`, of the ontology store), so nothing an answer writes reaches the next question.

## Data

[BIRD Mini-Dev](https://huggingface.co/datasets/birdsql/bird_mini_dev), SQLite split, 2026 annotations (`Mini-Dev 1024`, revision `colleague_review_fixes_20261025`). The databases and their `database_description/*.csv` files come from the original `minidev.zip`; all 213 gold queries for the four databases below run without errors on them.

`questions.json` holds 60 questions: 15 sampled at random (seed `20261109`) from each of `california_schools`, `financial`, `thrombosis_prediction` and `toxicology`. 52 of them carry an evidence hint in BIRD. **The hint is not given to any condition**: the knowledge it carries (what a code means, a normal range, a formula) has to come from the docs, the wiki or the ontology. That is the gap a knowledge layer is supposed to fill. `questions_test.json` holds 60 more, sampled the same way (seed `20261110`) with none shared, for the second round below.

The question files list ids only. `bench.py` reads the question text and gold SQL from BIRD's `mini_dev_sqlite.jsonl` at run time; BIRD Mini-Dev is © the BIRD team under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) and is not redistributed here.

## Builds

Both knowledge layers are built once per database by the same model, from the same sources, with no knowledge of the questions:

- **wiki**: `prompts/build_wiki.md` with Karpathy's gist as `LLM_WIKI.md` in the folder (downloaded on first use; `bench.py` notes when it differs from the revision these results used). The model writes `wiki/SCHEMA.md`, `wiki/index.md`, `wiki/log.md` and the pages.
- **ontology**: the datasource is added and scanned with the CLI, then the model gets the server's own `build_ontology` prompt (`prompts/build_ontology.md`) and reads `docs/` for the descriptions.

## Scoring

BIRD's strict execution accuracy (EX): a prediction is right when the set of rows its SQL returns equals the set the gold SQL returns. The model returns its final query as structured output (`sql`, `answer`); the scorer runs it against a read-only copy of the database with a 60 s limit.

## Running it

Needs `claude` (Claude Code, logged in), `uv sync` in the repository root, and about 1 GB of disk. Data goes to `$BENCH_ROOT` (default `~/auto-ontology-bench`).

```bash
mkdir -p ~/auto-ontology-bench/download ~/auto-ontology-bench/hf/data
curl -L -o ~/auto-ontology-bench/download/minidev.zip https://bird-bench.oss-cn-beijing.aliyuncs.com/minidev.zip
curl -L -o ~/auto-ontology-bench/hf/data/mini_dev_sqlite.jsonl \
  https://huggingface.co/datasets/birdsql/bird_mini_dev/resolve/main/data/mini_dev_sqlite.jsonl

python3 bench.py prepare                       # extract 4 databases, sample questions.json
python3 bench.py prepare --set test            # 60 held-out questions, none shared with questions.json
python3 bench.py build --condition wiki        # 4 LLM Wiki builds
python3 bench.py build --condition ontology    # 4 ontology builds
python3 bench.py run --name main --conditions plain wiki ontology --trials 2 --jobs 4
python3 bench.py score --name main
python3 bench.py report --name main            # writes results/main/summary.md
```

Add `--set test` to `run`, `score` and `report` for the held-out questions, or `--set bird2025` for the 2025 release (save `archive/bird_mini_dev_2025-07-04/data/mini_dev_sqlite-00000-of-00001.json` from the same Hugging Face repository as `~/auto-ontology-bench/hf/archive/mini_dev_sqlite_2025.json`). `BENCH_SERVER` points the ontology conditions at another server build. The conditions are `plain`, `plain-evidence` (plain with BIRD's hint appended to the question), `wiki`, `ontology`, `ontology-answer` (the same ontology served with `AUTO_ONTOLOGY_TOOLSETS=answer`, which needs the server from #10; an older server ignores the variable and exposes every tool) and `ontology-v1` (the server from `BENCH_SERVER_V1`, default `~/auto-ontology-bench/server-v1`, a `git worktree` of `d6bf3d0`, with the ontologies in `builds/ontology-run1`).

`BENCH_MODEL` picks the model (default `sonnet`). `run` resumes: sessions already in `results/<name>/answers.jsonl` are skipped.

## Results (run `main`, 2026-11-09)

Model `claude-sonnet-5-5` in every condition and build; auto-ontology-mcp at `main` (`d6bf3d0`). 60 questions × 2 trials per condition. Full tables, including every question: [`results/main/summary.md`](results/main/summary.md).

| | plain | wiki | ontology |
| --- | ---: | ---: | ---: |
| EX | 79.2% (95/120) | 80.0% (96/120) | 75.8% (91/120) |
| Difference from plain, 95% bootstrap interval | | +0.8 (−4.2 to +5.8) | −3.3 (−10.0 to +3.3) |
| Cost per question, median | $0.082 | $0.085 | $0.116 |
| Input tokens per question, median | 66K | 83K | 162K |

- **No condition is measurably better.** Every interval includes zero. Toxicology came out at 90% in all three; the conditions split on 14 of the 60 questions (6 in `financial`, 6 in `california_schools`, 2 in `thrombosis_prediction`).
- **The ontology condition costs about 40% more per question.** Its sessions start with about 38K tokens of context against 19K for the others (the server's tool definitions and instructions), and `get_query_context` output adds more.
- **The model skipped most of the ontology workflow.** `validate_sql` was called in 0 of 120 sessions and `check_answerable` in 3; the usual path was `get_query_context` → `run_sql` (which still runs the static checks).
- **Most splits are readings of the question, not missing knowledge.** Examples: on q219 the ontology runs computed the right numbers twice but appended the region-wide figures as extra rows instead of columns; on q29 they used `schools.FundingType` and `DOC = '00'` where the gold uses the meal-program file's charter funding type and district type; on q204 the plain and wiki runs added an owner-only filter the question did not ask for, and the ontology runs did not.
- **A build-time guess carried into answers.** `trans.type` holds `VYBER`, which the docs do not list. Both builds treated it as a withdrawal (the ontology as its "Withdrawal Transaction" SQL attribute, flagged in the build summary for review), and on q221 the wiki and ontology runs counted it in 4 of 4 answers, against the gold's `VYDAJ` only. Plain got it right once.
- **Knowledge not in the docs stayed missing everywhere.** "Severe anemia" (hemoglobin below 7.0) is only in BIRD's hint; plain and wiki used other thresholds in all four runs, and the ontology runs got it once.
- Builds took 0.5 to 2 minutes and $0.22 to $0.39 each. The wikis have 12 to 16 pages (9 to 22 KB); the ontologies have 3 to 8 Terms and 6 to 8 SQL attributes, with no glossary entries.

`inspect_question.py --name main <id>` prints a question's gold SQL and result next to every condition's.

## Second round: server changes, checked on held-out questions (2026-11-10)

The first run pointed at three causes in the ontology condition: verbose tool output and tool definitions (cost), draft SQL attributes that encoded a builder's guess (`VYBER` as a withdrawal), and `=` against a column that stores lists (`Diagnosis = 'SLE'` where values look like `'SLE, SjS'`). #10 changes the server:

- `get_query_context` lists one line per column instead of a JSON object, shows three short samples instead of five, drops empty flags, and returns the answer checklist once instead of with every `run_sql`. It also notes fields that two tables in the context both hold (`schools.FundingType` and `frpm."Charter Funding Type"`).
- Complete value sets now reach `get_term` and `get_query_context` as `all_values`. Before, the query that fed them did not select `value_set_complete`, so a complete list of codes was labeled as samples.
- Profiling marks text columns that store lists, `get_term` says so, and `validate_sql` and `run_sql` warn about `=` or `IN` against them.
- The SQL rules tell the model to check a draft SQL attribute against the question before reusing it, and the SQL attribute rules keep codes the documentation does not explain out of new attributes.
- `AUTO_ONTOLOGY_TOOLSETS=answer` exposes only the 13 question-answering tools and the matching part of the handshake instructions (tool definitions drop from about 52,000 to 12,000 characters).

The ontologies were rebuilt with the new server (`builds/ontology`; the first ones are in `builds/ontology-run1`). Because the changes were made while looking at the 60 questions above, they were checked on 60 other questions (`questions_test.json`, 15 per database, none shared with `questions.json`), with the first-round server (`ontology-v1`, a checkout of `d6bf3d0` with its own ontologies) run on the same questions. Two trials each.

| Held-out questions | plain | wiki | ontology-v1 | ontology | ontology, `answer` tools |
| --- | ---: | ---: | ---: | ---: | ---: |
| EX | 86.7% (104/120) | 89.2% (107/120) | 88.3% (106/120) | 87.5% (105/120) | 88.3% (106/120) |
| Difference from plain, 95% interval | | +2.5 (−4.2 to +9.2) | +1.7 (−5.8 to +8.3) | +0.8 (−6.7 to +8.3) | +1.7 (−4.2 to +7.5) |
| Cost per question, mean | $0.081 | $0.083 | $0.109 | $0.108 | $0.098 |
| Input tokens per question, median | 81K | 82K | 162K | 165K | 101K |

Sources: `results/v2-test` (plain, wiki, ontology-v1) and `results/v3-test` (the two ontology columns, final server). The final server differs from `results/v2-test`'s ontology runs by one rule: a "summary figures go in columns, not rows" rule fixed one question in the first set but made the model pivot per-sex counts into columns in the held-out set (q413), so it was removed; with it the two ontology columns scored 87.5% and 90.8%.

- **Accuracy did not measurably change.** On the first 60 questions the new server scored 85.8% against 75.8% (`results/v2-dev`), but that is the set the changes were made against. On the held-out set every condition, before and after, falls between 86.7% and 90.8%, and every interval includes zero.
- **Cost fell about 10% with the `answer` tool group** ($0.109 to $0.098 per question), leaving it 21% above plain. With all tools exposed it stayed at $0.108: about 13,000 tokens of tool definitions are read again on every turn, and that outweighs the shorter tool output.
- **Re-run sessions are left out of cost.** 46 ontology sessions of `v2-test` started while a server edit was briefly on disk and were re-run with the tested code. Re-running in the same workspace path hits the prompt cache of the first attempt and costs about half, so cost and time come from first attempts only (`rerun` in `answers.jsonl`).

`compare.py` puts conditions from different runs side by side, for example `python3 compare.py v2-test:plain v2-test:ontology-v1 v3-test:ontology-answer`.

## Third round: terse questions in business terms (2025 release, 2026-11-10)

The 2026 annotations spell definitions out in the question, which leaves little for a knowledge layer to add. The 2025-07-04 release of BIRD Mini-Dev asks the same databases tersely ("accounts who choose issuance after transaction", "eligible free rate") and puts the meaning in the evidence hint, which is the setting of the BIRD paper's ablation without external knowledge. This round asks all 152 questions of the four databases from that release (`questions_bird2025.json`) without the hint, two trials each, and once more with the hint given to the plain condition (`plain-evidence`) as an upper bound. The ontology condition is #10's server with the `answer` tool group (`BENCH_SERVER` pointing at a checkout of `0268919`) and the ontologies in `builds/ontology`. Results: `results/bird2025`.

| 152 questions, 2025 release | plain | wiki | ontology, `answer` tools | plain with the hint |
| --- | ---: | ---: | ---: | ---: |
| EX | 53.3% (162/304) | 52.3% (159/304) | 53.6% (163/304) | 58.6% (89/152) |
| Difference from plain, 95% interval | | −1.0 (−3.6 to +1.6) | +0.3 (−3.6 to +4.3) | +5.3 (−1.3 to +11.8) |
| EX, california_schools | 36.7% | 28.3% | 35.0% | 60.0% |
| EX, toxicology | 52.5% | 53.8% | 60.0% | 55.0% |
| Cost per question, mean | $0.076 | $0.078 | $0.095 | $0.071 |

- **Neither knowledge layer recovered what the hints carry.** Both stay within a point of plain. Even the hint itself adds only 5.3 points, and its interval includes zero.
- **Most of what the hint adds is the annotator's convention, not domain knowledge.** Of the 17 questions the hinted run got right and the ontology runs missed both times, three compute a rate from counts (`FRPM Count (K-12)` / `Enrollment (K-12)`) where the database already stores one (`Percent (%) Eligible FRPM (K-12)` and similar); the model used the stored column, which holds a rounded fraction where two of the three gold queries want a percent, and others sort by `ABS(longitude)`, divide a decrease by the later month, or count laboratory rows rather than patients. q1198 is #10's list-column handling: the ontology runs matched 'APS' inside lists ('APS, SLE'), while the 2025 gold uses `Diagnosis = 'APS'`.
- **63 of 152 questions stay wrong with the hint.** The 2025 release is the one the 2026 revision corrected; its gold SQL carries conventions and errors no setup can infer, so it measures agreement with the annotators more than the use of knowledge.
- The ontology condition costs 25% more than plain per question, as in the second round.

## Fourth round: pre-registered, 440 questions (2026-11-10)

To settle the question with enough questions, the design, endpoint and decision rule were written down and pushed before any session ran ([`PREREGISTRATION.md`](PREREGISTRATION.md), commit `d9bc707`). It uses every 2026 Mini-Dev question of all 11 databases except the 60 the server changes were designed against: 440 questions, two trials per condition, evidence withheld. The ontology condition is #10 and #12 merged (local commit `d480777`) with the `answer` tool group. The wikis and ontologies of the seven new databases were built the same way as the others. Results: `results/final-sonnet`, `results/final-haiku`, and `results/final-summary.md` from `analyze_prereg.py`.

| 440 questions | A | B | B − A | 95% interval | 90% interval | questions B better / worse | verdict |
| --- | ---: | ---: | ---: | --- | --- | ---: | --- |
| **Primary**: Sonnet, ontology (B) − plain (A) | 85.7% | 87.5% | +1.8 | −0.7 to +4.3 | −0.2 to +4.0 | 38 / 27 | inconclusive |
| Sonnet, wiki − plain | 85.7% | 86.5% | +0.8 | −1.2 to +3.0 | −1.0 to +2.6 | 32 / 27 | equivalent within 3 points |
| Sonnet, ontology − wiki | 86.5% | 87.5% | +1.0 | −1.0 to +3.1 | −0.7 to +2.7 | 31 / 22 | equivalent within 3 points |
| Haiku, ontology − plain | 80.8% | 82.6% | +1.8 | −0.8 to +4.4 | −0.5 to +4.0 | 55 / 34 | inconclusive |

| Cost per question | plain | wiki | ontology |
| --- | ---: | ---: | ---: |
| Sonnet 5.5 | $0.077 | $0.080 | $0.095 |
| Haiku 5.5 | $0.006 | | $0.008 |

- **Verdict: inconclusive.** With Sonnet the ontology answered 1.8 points more questions, but the 95% interval reaches −0.7, so the run cannot call it better; and the 90% interval reaches +4.0, so it cannot call the two equivalent within 3 points either. What it does rule out: the ontology being worse by more than 0.7 points or better by more than 4.3.
- **Haiku shows the same +1.8 points**, also inconclusive by the pre-registered rule.
- **The wiki and plain access are equivalent within 3 points, and so are the ontology and the wiki.**
- **Cost**: the ontology costs 23% more than plain with Sonnet and 25% more with Haiku. Haiku with the ontology (82.6%, $0.008) costs about a tenth of Sonnet with plain access (85.7%, $0.077).
- Sessions: none errored. One Haiku ontology session (q309, trial 2) listed the data folder outside its workspace (it holds the same database and description CSVs, not the gold SQL) and is excluded, as the pre-registration says.

Exploratory, not pre-registered:

- Averaging each question's difference over the two models gives +1.8 points (95% interval −0.1 to +3.7). Counting questions, Haiku's ontology runs did better on 55 and worse on 34 (two-sided sign test p = 0.033); Sonnet's on 38 and 27 (p = 0.22). The direction is consistent; the size is small.
- Questions the ontology won in both Sonnet trials while plain lost both include a diagnosis stored as a list (q383, #10's `holds_lists`), a work-rate column whose stored values include junk besides low, medium and high (q175, found with `lookup_values`), and per-group rows where plain pivoted the counts into columns (q349).
- Confirming a 1.8-point difference with 80% power needs about 1,750 questions at the spread seen here, four times this run.

## Fifth round: the layers with accumulated knowledge (pre-registered, 2026-11-10)

The earlier rounds gave the layers only what one build pass drew from the schema and column descriptions. This round tests them holding curated knowledge, and with the ontology set up as its documentation intends ([`PREREGISTRATION-curated.md`](PREREGISTRATION-curated.md), commit `78c36cc`, pushed before the runs; `curated.py`). The 500 questions are split into two folds per database, and a question is answered with the other fold's questions, evidence notes and gold SQL: as a file in `docs/` (`plain-notes`), ingested into the wiki (`wiki-curated`), or saved as verified example queries, glossary entries and SQL attributes (`ontology-curated`, which is also prompted with the server's answering steps and runs a server where `describe_table` shows the ontology's column descriptions, local commit `0aba40f`). The raw file stays in `docs/` for every condition. Results: `results/curated-sonnet` (one trial), `results/curated-haiku` (two trials), `results/curated-summary.md`.

| 500 questions | plain-notes | wiki-curated | ontology-curated | ontology − plain-notes, 95% interval | verdict |
| --- | ---: | ---: | ---: | --- | --- |
| **Primary**: Sonnet 5.5 | 89.0% | 87.0% | 88.4% | −0.6 (−3.2 to +2.0) | equivalent within 3 points |
| Haiku 5.5 | 85.2% | 81.8% | 82.1% | −3.1 (−5.7 to −0.6) | worse |
| Cost per question, Sonnet / Haiku | $0.090 / $0.007 | $0.089 / $0.008 | $0.107 / $0.009 | | |

- **The knowledge helps; the layer it sits in does not.** Against plain access without it (fourth round, 440 shared questions), the file alone adds 3.6 points with Sonnet and 4.9 with Haiku (both intervals above zero), and the curated ontology adds 3.0 and 1.2.
- **With Sonnet the ontology is equivalent to the file; with Haiku the ontology and the wiki are both about 3 points worse than the file.** A model reading 7 to 43 verified queries straight from a file does at least as well as one reaching them through a layer.
- The ontology sessions did follow the documented steps this time (`validate_sql`, `get_query_context` with the question), at 19% (Sonnet) and 26% (Haiku) more cost than the file.
- No session errored or left its workspace. All 500 gold pairs were accepted by `save_example_query`; curation cost $12.5.
- Limits: at most 43 verified queries per database, which fits in one file read; whether retrieval from a layer pays off with thousands of entries is not tested here.

### Follow-up on fold 0: why the file does at least as well (not pre-registered)

Looking only at fold 0 of the Haiku run, the questions the ontology lost showed no single defect. Two patterns recurred: plain access looks at the stored values with `sqlite3` and writes `Admission LIKE '+%'` where the ontology runs wrote `= '+'` (three rows hold `'+('`), and the longer tool sequence came with more slips in the output (an unrequested `ROUND`, an extra column). Two changes were tried on fold 0 with Haiku, two trials (`results/tune-haiku`): a server warning when an equality literal leaves out dirty variants of the same value, and `ontology-addon`, which gives the model everything `plain-notes` has (Bash, `sqlite3`, `docs/`) plus the MCP server.

| Fold 0, Haiku, 253 questions | plain-notes | ontology-curated | ontology-addon |
| --- | ---: | ---: | ---: |
| EX | 85.4% | 83.6% | 83.0% |
| Difference from plain-notes, 95% interval | | −1.8 (−5.3 to +1.6) | −2.4 (−5.3 to +0.6) |
| Cost per question | $0.008 | $0.011 | $0.010 |

Adding the server to plain access did not raise accuracy, so no configuration was carried to fold 1 for confirmation. Server warnings fired in 12% of the add-on sessions and those sessions were not more accurate. Five add-on sessions listed the data folder outside their workspace (the same database and descriptions).

## What to keep in mind

- 60 questions is a sample. A difference of a few questions between conditions is within noise; the pairwise table in the summary shows how many questions each condition alone got right.
- The 2026 annotations spell most output contracts out in the question (rounding, tie-breaks, columns), so the questions lean on careful SQL more than on hidden domain knowledge.
- Only `ontology` lacks Bash. That is how each setup is used in practice, but it means the comparison is of setups, not of the knowledge layer alone.
- Builds are single attempts by one model. A person reviewing the wiki or certifying the ontology would change both.
- Transcripts are checked for paths outside the workspace (`leaks` in the results); BIRD is public, so a model may also have seen these databases in training, equally in every condition.
