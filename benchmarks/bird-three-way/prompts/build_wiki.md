`LLM_WIKI.md` in this folder describes the LLM Wiki pattern. Set up and fill a wiki for this folder following it.

The raw sources are:

- `db.sqlite`: a SQLite database (`{db}`). Query it read-only with the `sqlite3` command.
- `docs/`: CSV files that describe each table's columns and the meaning of their values.

Write the schema file as `wiki/SCHEMA.md` and the wiki pages in `wiki/`, with `wiki/index.md` and `wiki/log.md`. Ingest every source.

People will later ask this wiki data questions about this database that are answered with SQL, so the wiki should hold what someone writing those queries needs to know. Work on your own: there is no one to discuss takeaways with. Do not modify `db.sqlite` or `docs/`. When you are done, reply with a short summary of the pages you wrote.
