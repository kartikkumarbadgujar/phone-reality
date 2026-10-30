"""Unit tests for the priority formula."""

import pytest

import scoring


def test_formula_maximum():
    result = scoring.calculate_priority(5, 5, 5)
    assert result["raw"] == 125
    assert result["percent"] == 100.0
    assert result["band"] == "High"


def test_formula_example():
    result = scoring.calculate_priority(4, 4, 5)  # ambiguous upload on slow network
    assert result["raw"] == 80
    assert result["percent"] == 64.0
    assert result["formula"] == "4 x 4 x 5 = 80 / 125 = 64.0% (High)"


def test_formula_minimum():
    result = scoring.calculate_priority(1, 1, 1)
    assert result["raw"] == 1
    assert result["percent"] == 0.8
    assert result["band"] == "Low"


@pytest.mark.parametrize("percent, band", [(50, "High"), (49.9, "Medium"), (25, "Medium"), (24.9, "Low")])
def test_bands(percent, band):
    assert scoring.priority_band(percent) == band


@pytest.mark.parametrize("bad", [0, 6, 2.5, "3", True])
def test_rejects_values_outside_1_to_5(bad):
    with pytest.raises(ValueError):
        scoring.calculate_priority(bad, 3, 3)


def test_score_uses_assumptions_and_explains_them():
    result = scoring.score_finding(5, "slow_network", "upload", scoring.DEFAULT_ASSUMPTIONS)
    assert result["raw"] == 5 * 4 * 5
    assert "assumed for profile 'slow_network'" in result["explanation"]
    assert "not measurements" in result["explanation"]


def test_changed_assumption_changes_score():
    assumptions = {"likelihood": {"slow_network": 1}, "impact_weight": {"upload": 1}}
    assert scoring.score_finding(5, "slow_network", "upload", assumptions)["raw"] == 5
