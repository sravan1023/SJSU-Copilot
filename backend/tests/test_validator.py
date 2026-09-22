"""
Tests for services/validator.py, the post-generation checks that decide whether
an answer is rewritten (concise mode overrun) or gets a warning appended
(domain advice without a disclaimer, absolute-certainty language).

Run from backend/ with:
    python -m pytest tests/test_validator.py
"""
from services.validator import (
    CONCISE_WORD_LIMIT,
    DOMAIN_WARNING,
    UNVERIFIED_WARNING,
    run_validators,
)

CONCISE = {"response_length": "concise"}


def _rules(result):
    return [v["rule"] for v in result["violations"]]


def test_clean_answer_passes():
    result = run_validators("Registration opens on April 6 in MySJSU.", CONCISE)
    assert result == {"violations": [], "action": None, "warning_text": None, "repair_prompt": None}


def test_concise_limit_is_inclusive():
    at_limit = " ".join(["word"] * CONCISE_WORD_LIMIT)
    over = " ".join(["word"] * (CONCISE_WORD_LIMIT + 1))
    assert run_validators(at_limit, CONCISE)["action"] is None
    result = run_validators(over, CONCISE)
    assert _rules(result) == ["response_too_long_for_concise"]
    assert result["action"] == "rewrite"
    assert result["repair_prompt"].endswith(over)
    assert result["warning_text"] is None


def test_length_only_matters_in_concise_mode():
    long_answer = " ".join(["word"] * (CONCISE_WORD_LIMIT * 2))
    for behavior in (None, {}, {"response_length": "detailed"}, {"response_length": "balanced"}):
        assert run_validators(long_answer, behavior)["action"] is None


def test_domain_advice_without_disclaimer_warns():
    result = run_validators("Take this medication twice a day for the symptom.", None)
    assert _rules(result) == ["domain_advice_without_disclaimer"]
    assert result["action"] == "warn"
    assert result["warning_text"] == DOMAIN_WARNING


def test_disclaimer_suppresses_the_domain_warning():
    answer = "That medication may help, but this is not medical advice; consult a doctor."
    assert run_validators(answer, None)["action"] is None


def test_certainty_language_warns():
    result = run_validators("I am absolutely sure the deadline is Friday.", None)
    assert _rules(result) == ["absolute_certainty_language"]
    assert result["warning_text"] == UNVERIFIED_WARNING


def test_domain_warning_replaces_the_unverified_warning():
    result = run_validators("I guarantee this investment always works.", None)
    assert set(_rules(result)) == {"domain_advice_without_disclaimer", "absolute_certainty_language"}
    assert result["warning_text"] == DOMAIN_WARNING  # not both warnings stacked


def test_rewrite_wins_over_warnings():
    over = " ".join(["word"] * (CONCISE_WORD_LIMIT + 1)) + " I guarantee it."
    result = run_validators(over, CONCISE)
    assert "absolute_certainty_language" in _rules(result)
    assert result["action"] == "rewrite"
    assert result["warning_text"] is None


def test_matching_is_word_bounded():
    # "treatment" is a domain word; "retreatments" shouldn't be, nor "diagnostics".
    assert run_validators("Campus retreatments and diagnostics lab hours.", None)["action"] is None
