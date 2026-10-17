"""Quality gate: a retrain replaces the served model only if it is not worse."""

from __future__ import annotations

from climatelens.data import latest_model_version, read_bundle_metrics, write_model_bundle
from climatelens.pipeline import GATE_TOLERANCE, quality_gate


def _metrics(frost_brier: float, wind_mae: float) -> dict[str, dict]:
    return {
        "target_frost": {"best_model": "lightgbm", "overall": {"lightgbm": {"brier": frost_brier}}},
        "target_wind_speed": {"best_model": "lightgbm", "overall": {"lightgbm": {"mae": wind_mae}}},
    }


def test_gate_passes_when_nothing_is_served_yet():
    passed, checks = quality_gate(_metrics(0.06, 0.8), None)
    assert passed and all(c["served"] is None for c in checks)


def test_gate_passes_small_changes_and_improvements():
    served = _metrics(0.060, 0.80)
    worse_but_within = 0.060 * (1 + GATE_TOLERANCE * 0.9)
    passed, checks = quality_gate(_metrics(worse_but_within, 0.75), served)
    assert passed
    assert {c["target"]: c["ok"] for c in checks} == {
        "target_frost": True,
        "target_wind_speed": True,
    }


def test_gate_fails_when_any_target_gets_clearly_worse():
    passed, checks = quality_gate(_metrics(0.060, 0.90), _metrics(0.060, 0.80))
    assert not passed
    wind = next(c for c in checks if c["target"] == "target_wind_speed")
    assert wind["ok"] is False and wind["change_pct"] == 12.5


def test_new_targets_pass_the_gate():
    served = {"target_frost": _metrics(0.06, 0.8)["target_frost"]}
    passed, checks = quality_gate(_metrics(0.06, 5.0), served)
    assert passed  # wind is new in this bundle, nothing to compare against


def test_held_back_bundle_is_stored_but_not_served(tmp_path):
    base = str(tmp_path / "models")
    first = {"version": "20260101T000000Z", "targets": []}
    write_model_bundle({}, first, _metrics(0.06, 0.8), base)
    second = {"version": "20260201T000000Z", "targets": []}
    write_model_bundle({}, second, _metrics(0.06, 0.9), base, publish=False)

    assert latest_model_version(base) == "20260101T000000Z"  # still serving the first
    assert read_bundle_metrics(base, "20260201T000000Z") is not None  # but kept for review
    assert read_bundle_metrics(base)["target_wind_speed"]["overall"]["lightgbm"]["mae"] == 0.8
