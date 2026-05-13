"""Parser round-trip tests."""

from __future__ import annotations

import json
import pytest

from app.services.equity.parser import ParseError, parse


def _good_payload() -> dict:
    sections = {
        "step_1_snapshot":          {"title": "Snapshot",          "analysis": "x"},
        "step_2_financial_quality": {"title": "Financial Quality", "analysis": "x"},
        "step_3_competitive":       {"title": "Competitive",       "analysis": "x"},
        "step_4_bull":              {"title": "Bull",              "analysis": "x"},
        "step_5_bear":              {"title": "Bear",              "analysis": "x"},
        "step_6_sentiment":         {"title": "Sentiment",         "analysis": "x"},
        "step_7_valuation":         {
            "title": "Valuation", "analysis": "x",
            "bull_target": 405, "base_target": 324, "bear_target": 210,
        },
        "step_8_scorecard":         {"title": "Scorecard",         "analysis": "x"},
        "step_9_thesis":            {"title": "Thesis",            "analysis": "x"},
    }
    scores = {
        "Business Quality": 4.8, "Financial Quality": 4.6, "Competitive Positioning": 4.4,
        "Growth Potential": 4.3, "Risk Profile": 3.1, "Sentiment & Positioning": 4.0,
        "Valuation Outlook": 3.6,
    }
    return {
        "ticker": "GOOGL",
        "company_name": "Alphabet",
        "anchor_price": 308.12,
        "steps": sections,
        "scores": scores,
        "investment_stance": "Accumulate",
        "invalidation_triggers": ["X", "Y"],
    }


def test_parse_happy_path():
    p = parse(json.dumps(_good_payload()))
    assert p.ticker == "GOOGL"
    assert p.bull_target == 405
    assert p.scores["Risk Profile"] == 3.1


def test_parse_handles_fenced_json():
    raw = "```json\n" + json.dumps(_good_payload()) + "\n```"
    p = parse(raw)
    assert p.ticker == "GOOGL"


def test_parse_rejects_missing_step():
    data = _good_payload()
    del data["steps"]["step_5_bear"]
    with pytest.raises(ParseError):
        parse(json.dumps(data))


def test_parse_rejects_out_of_range_score():
    data = _good_payload()
    data["scores"]["Business Quality"] = 7.0
    with pytest.raises(Exception):
        parse(json.dumps(data))
