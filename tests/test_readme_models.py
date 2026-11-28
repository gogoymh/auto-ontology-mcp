"""The README's model table carries check_answerable precision and recall next to the disk cost."""

from __future__ import annotations

import re
from pathlib import Path

README = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")


def _model_table() -> tuple[list[str], dict[str, list[str]]]:
    lines = [ln.strip() for ln in README.splitlines() if ln.strip().startswith("| Model | Recall@3")]
    assert len(lines) == 1, "expected one model comparison table"
    start = README.splitlines().index(next(ln for ln in README.splitlines() if ln.strip() == lines[0]))
    rows = README.splitlines()[start:start + 4]
    cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
    header = cells[0]
    return header, {r[0]: r for r in cells[2:]}


def test_model_table_has_answerable_precision_recall_and_disk():
    header, rows = _model_table()
    prec = header.index("`check_answerable` precision")
    rec = header.index("`check_answerable` recall")
    disk = header.index("Disk")
    minilm = next(v for k, v in rows.items() if k.startswith("MiniLM"))
    mpnet = next(v for k, v in rows.items() if k.startswith("mpnet"))
    assert minilm[prec] == mpnet[prec] == "1.00"
    assert float(mpnet[rec]) > float(minilm[rec]) > 0
    assert minilm[disk] == "252 MB" and mpnet[disk] == "1127 MB"


def test_readme_states_the_default_model_decision_and_margins():
    assert re.search(r"MiniLM stays the default: .*disk", README)
    assert "0.30 for MiniLM and 0.40 for mpnet" in README
    assert "check_answerable_precision.py --model mpnet" in README
