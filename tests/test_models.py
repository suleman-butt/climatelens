"""Offline tests for baselines, models, walk-forward evaluation and model bundles."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from climatelens.config import CITIES_BY_SLUG
from climatelens.data import latest_model_version, read_model_bundle, write_model_bundle
from climatelens.evaluate import (
    classification_metrics,
    evaluate_all,
    regression_metrics,
    summarise,
    train_final,
    walk_forward_folds,
    walk_forward_predictions,
)
from climatelens.features import TARGETS, build_features
from climatelens.models import (
    Climatology,
    LightGBMModel,
    MajorityClass,
    Persistence,
    candidate_models,
    make_model,
)


def _seasonal_raw(city="berlin", start="2022-01-01", days=900, seed=0):
    """Synthetic daily weather with a seasonal cycle, AR(1) noise and frost winters."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(start, periods=days, freq="D")
    season = np.cos(2 * np.pi * (dates.dayofyear.to_numpy() - 200) / 365.25)
    noise = np.zeros(days)
    for i in range(1, days):
        noise[i] = 0.7 * noise[i - 1] + rng.normal(0, 2.0)
    tmean = 10.0 + 9.0 * season + noise
    wind = np.clip(3.0 + 0.5 * rng.standard_normal(days) - 0.5 * season, 0.3, None)
    return pd.DataFrame(
        {
            "city": city,
            "station_id": CITIES_BY_SLUG[city].station_id,
            "date": dates,
            "temperature_air_mean_2m": tmean,
            "temperature_air_min_2m": tmean - 5.0,
            "temperature_air_max_2m": tmean + 5.0,
            "temperature_air_min_0_05m": tmean - 6.0,
            "wind_speed": wind,
            "wind_gust_max": wind * 2.5,
            "humidity": 75.0 - 10.0 * season,
            "pressure_air_site": 1010.0 + rng.normal(0, 5.0, days),
            "cloud_cover_total": np.clip(5.0 - 2.0 * season + rng.normal(0, 1.0, days), 0, 8),
            "precipitation_height": rng.exponential(1.5, days),
            "radiation_global": np.clip(1200 + 1000 * season + rng.normal(0, 200, days), 0, None),
        }
    )


@pytest.fixture(scope="module")
def features():
    raw = pd.concat(
        [_seasonal_raw("berlin", seed=1), _seasonal_raw("munich", seed=2)], ignore_index=True
    )
    return build_features(raw)


# --- Baselines ----------------------------------------------------------------


def _frame(city, dates, **cols):
    return pd.DataFrame({"city": city, "date": pd.to_datetime(dates), **cols})


def test_climatology_is_per_city_day_of_year_mean():
    train = pd.concat(
        [
            _frame("berlin", ["2022-07-01", "2023-07-01"], target_wind_speed=[2.0, 4.0]),
            _frame("munich", ["2022-07-01"], target_wind_speed=[10.0]),
        ]
    )
    model = Climatology("target_wind_speed", window_days=0).fit(train)
    pred = model.predict(_frame(["berlin", "munich"], ["2024-07-01", "2024-07-01"]))
    # 2024 is a leap year: Jul 1 must still map to the same slot as 2022/2023.
    assert pred.tolist() == [3.0, 10.0]


def test_climatology_window_wraps_around_new_year():
    train = _frame("berlin", ["2023-01-02"], target_wind_speed=[5.0])
    model = Climatology("target_wind_speed", window_days=3).fit(train)
    assert model.predict(_frame("berlin", ["2023-12-31"]))[0] == 5.0


def test_climatology_falls_back_for_unseen_city_and_day():
    train = _frame("berlin", ["2023-01-01", "2023-01-02"], target_wind_speed=[2.0, 4.0])
    model = Climatology("target_wind_speed", window_days=0).fit(train)
    pred = model.predict(_frame(["berlin", "hamburg"], ["2023-06-01", "2023-01-01"]))
    assert pred.tolist() == [3.0, 3.0]  # berlin mean; global mean for unseen city


def test_persistence_uses_today_and_falls_back_when_missing():
    train = _frame("berlin", ["2023-01-01"], target_solar_kwh_m2=[1.5])
    model = Persistence("target_solar_kwh_m2").fit(train)
    test = _frame("berlin", ["2023-01-01", "2023-01-02"], solar_kwh_m2=[0.8, np.nan])
    assert model.predict(test).tolist() == [0.8, 1.5]


def test_majority_class_predicts_no_frost_when_frost_is_rare():
    train = _frame("berlin", ["2023-01-01"] * 5, target_frost=[1.0, 0.0, 0.0, 0.0, 0.0])
    model = MajorityClass("target_frost").fit(train)
    assert model.predict(train).tolist() == [0.0] * 5
    with pytest.raises(ValueError):
        MajorityClass("target_wind_speed")


def test_candidate_models_per_target():
    names = {t: [m.name for m in candidate_models(t)] for t in TARGETS}
    assert names["target_frost"] == ["persistence", "climatology", "majority_class", "lightgbm"]
    assert names["target_wind_speed"] == ["persistence", "climatology", "lightgbm"]
    assert make_model("target_frost", "lightgbm").name == "lightgbm"
    with pytest.raises(ValueError):
        make_model("target_wind_speed", "majority_class")


def test_lightgbm_outputs_probabilities_and_non_negative_values(features):
    frost = LightGBMModel("target_frost").fit(features)
    p = frost.predict(features)
    assert ((p >= 0) & (p <= 1)).all()
    solar = LightGBMModel("target_solar_kwh_m2").fit(features)
    assert (solar.predict(features) >= 0).all()
    assert len(solar.feature_importance()) > 0


# --- Metrics ------------------------------------------------------------------


def test_regression_metrics():
    m = regression_metrics(np.array([1.0, 2.0, 3.0]), np.array([2.0, 2.0, 1.0]))
    assert m["mae"] == pytest.approx(1.0)
    assert m["rmse"] == pytest.approx(np.sqrt(5 / 3))


def test_classification_metrics():
    m = classification_metrics(np.array([1.0, 0.0, 0.0, 1.0]), np.array([0.9, 0.2, 0.6, 0.4]))
    assert m["accuracy"] == 0.5
    assert m["brier"] == pytest.approx((0.01 + 0.04 + 0.36 + 0.36) / 4)
    assert m["positive_rate"] == 0.5


# --- Walk-forward -------------------------------------------------------------


def test_folds_are_monthly_expanding_and_non_overlapping():
    dates = pd.Series(pd.date_range("2022-01-01", "2023-12-15", freq="D"))
    folds = walk_forward_folds(dates, min_train_days=365)
    assert folds[0].test_start == pd.Timestamp("2023-01-01")
    assert folds[-1].test_end == pd.Timestamp("2023-12-16")
    for a, b in zip(folds, folds[1:], strict=False):
        assert a.test_end == b.test_start
        assert b.test_start.day == 1


class _Spy:
    """Records what it was trained on, to prove no test row leaks into training."""

    name = "spy"
    seen: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    def fit(self, train):
        self.train_max = train["date"].max()
        return self

    def predict(self, frame):
        _Spy.seen.append((self.train_max, frame["date"].min()))
        return np.zeros(len(frame))


def test_walk_forward_trains_strictly_before_test(features):
    _Spy.seen = []
    preds = walk_forward_predictions(
        features, "target_wind_speed", model_factory=lambda t: [_Spy()], min_train_days=365
    )
    assert len(_Spy.seen) == preds["fold"].nunique() > 1
    assert all(train_max < test_min for train_max, test_min in _Spy.seen)


def test_lightgbm_beats_climatology_on_learnable_signal(features):
    results = evaluate_all(features, targets=("target_frost",), min_train_days=365)
    frost = results["target_frost"]
    assert frost.folds > 1
    assert set(frost.per_city) == {"berlin", "munich"}
    assert frost.overall["lightgbm"]["brier"] < frost.overall["climatology"]["brier"]
    assert frost.overall["climatology"]["brier_skill_vs_climatology"] == 0.0


def test_summarise_picks_lowest_error():
    preds = pd.DataFrame(
        {
            "fold": 0,
            "city": "berlin",
            "date": pd.Timestamp("2024-01-01"),
            "model": ["persistence", "lightgbm"],
            "y_true": [3.0, 3.0],
            "y_pred": [5.0, 3.5],
        }
    )
    ev = summarise(preds, "target_wind_speed")
    assert ev.best_model == "lightgbm"
    assert ev.overall["lightgbm"]["mae_skill_vs_persistence"] == pytest.approx(0.75)


# --- Final training and bundles -------------------------------------------------


def test_train_final_and_bundle_round_trip(features, tmp_path):
    evaluations = {t: summarise(pd.DataFrame(), t) for t in TARGETS}
    with pytest.raises(ValueError):
        train_final(features, evaluations)
    for ev in evaluations.values():
        ev.best_model = "climatology"
    trained = train_final(features, evaluations)
    assert trained["target_solar_kwh_m2"].train_rows < len(features)  # last day has no target

    base = str(tmp_path / "models")
    manifest = {"version": "20260101T000000Z", "targets": list(trained)}
    write_model_bundle({t: x.model for t, x in trained.items()}, manifest, {}, base)
    assert latest_model_version(base) == "20260101T000000Z"

    models, back_manifest, _ = read_model_bundle(base)
    assert back_manifest == manifest
    sample = features.tail(3)
    expected = trained["target_wind_speed"].model.predict(sample)
    np.testing.assert_allclose(models["target_wind_speed"].predict(sample), expected)

    with pytest.raises(FileExistsError):  # bundles are immutable
        write_model_bundle({}, manifest, {}, base)


def test_read_model_bundle_without_training(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_model_bundle(str(tmp_path / "models"))
