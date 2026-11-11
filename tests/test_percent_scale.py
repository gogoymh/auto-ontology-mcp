"""Percent-vs-fraction checks: in run_sql as well as validate_sql, and against percentages in the question."""

from __future__ import annotations

LINES = "SELECT COUNT(*) FROM order_lines l WHERE l.discount_pct {op} {value}"


def test_run_sql_warns_about_a_percent_against_a_fraction_without_validate_sql(modeled):
    # discount_pct holds fractions from 0 to 0.3; 20 can only mean 20 %.
    out = modeled.run_sql(LINES.format(op=">", value=20))
    assert any("Did you mean 0.2" in w for w in out["warnings"])


def test_percent_in_the_question_is_checked_against_the_column_scale(modeled):
    question = "How many order lines had a discount under 0.2%?"
    # 0.2 fits the 0-0.3 range, so only the question shows it is 20 % rather than 0.2 %.
    warned = modeled.validate_sql(LINES.format(op="<", value=0.2), question=question)["warnings"]
    assert any("the question says 0.2%" in w and "Did you mean 0.002" in w for w in warned)
    assert any("Did you mean 0.002" in w for w in modeled.run_sql(LINES.format(op="<", value=0.2), question=question)["warnings"])
    # Written at the right scale, or without the question, nothing is said.
    assert not any("the question says" in w for w in modeled.validate_sql(LINES.format(op="<", value=0.002), question=question)["warnings"])
    assert not any("the question says" in w for w in modeled.validate_sql(LINES.format(op="<", value=0.2))["warnings"])


def test_percent_words_and_unrelated_numbers(modeled):
    sql = LINES.format(op=">=", value=0.15)
    # "15 percent" written as 0.15 is right; a percentage that matches no compared value says nothing.
    assert not any("the question says" in w for w in modeled.validate_sql(sql, question="discount of at least 15 percent")["warnings"])
    assert not any("the question says" in w for w in modeled.validate_sql(sql, question="lines from 2024 with a 0.15 discount, top 5%")["warnings"])
