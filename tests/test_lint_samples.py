"""Regression check of scripts/lint_report.py on real report sentences.

tests/data/lint_samples.jsonl holds 30 samples taken from past grading notes ('채점' documents)
and mission completion reports, criteria and requests. Each line gives the text, the sentences
the lint must split it into (after quoted spans are masked, as lint() does) and the pattern ids
it must report. A sentence cut in the wrong place or a pattern flagged or missed fails here.
"""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = [
    json.loads(line)
    for line in (ROOT / "tests/data/lint_samples.jsonl").read_text(encoding="utf-8").splitlines()
    if line.strip()
]
sys.path.insert(0, str(ROOT / "scripts"))
from lint_report import lint, mask, sentences  # noqa: E402

IDS = [s["id"] for s in SAMPLES]


def test_samples_are_thirty_real_reports() -> None:
    assert len(SAMPLES) == 30
    assert len(set(IDS)) == 30
    for s in SAMPLES:
        assert re.search(r"AOM-\d+ (채점|완료 보고|완료 기준|요청)", s["source"]), s
        assert re.search(r"[가-힣]", s["text"]), s
        assert s["sentences"] and isinstance(s["kinds"], list), s
    assert any(s["kinds"] for s in SAMPLES), "keep at least one true finding in the samples"
    assert any("채점" in s["source"] for s in SAMPLES)
    assert any("완료 보고" in s["source"] for s in SAMPLES)


@pytest.mark.parametrize("sample", SAMPLES, ids=IDS)
def test_sample_sentence_split(sample: dict) -> None:
    got = sentences(mask(sample["text"]))
    assert got == sample["sentences"], f"{sample['id']} split wrongly: {got}"


@pytest.mark.parametrize("sample", SAMPLES, ids=IDS)
def test_sample_findings(sample: dict) -> None:
    got = sorted({kind for kind, _ in lint(sample["text"])})
    extra = set(got) - set(sample["kinds"])
    missed = set(sample["kinds"]) - set(got)
    assert not extra and not missed, f"{sample['id']} flagged wrongly {sorted(extra)}, missed {sorted(missed)}"


def test_inner_da_unquoted_keeps_sentence() -> None:
    """The AOM-48 sample without its quote marks: '한다 해도' and '있다 없다' stay inside one sentence."""
    sample = next(s for s in SAMPLES if s["source"] == "AOM-48 요청")
    plain = re.sub(r"'", "", sample["text"])
    assert len(sentences(plain)) == len(sample["sentences"]), sentences(plain)
    for text in ("실패한다 해도 결과는 같습니다.", "배포했다 하더라도 다시 봅니다.", "값이 있다 없다 말하기 어렵습니다."):
        assert sentences(text) == [text]
    assert sentences("로그를 남긴다 다음은 내일이다") == ["로그를 남긴다", "다음은 내일이다"]
