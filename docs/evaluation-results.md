# Evaluation Results

Model bundle `20261017T131809Z`. Live scores of the currently served model:
[climatelens.sulemanb.com/metrics](https://climatelens.sulemanb.com/metrics).

Walk-forward evaluation, expanding window, monthly test folds. Model trained 2026-10-17. Lower Brier/MAE is better; skill = 1 - score / reference baseline score.

## Overall, by forecast horizon

| Forecast | Horizon | Served model | Score | Persistence | Climatology | Majority | Skill vs reference | Accuracy | RMSE | Test rows | Folds | Test period |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Frost | 1 d | lightgbm | Brier 0.056 | 0.091 | 0.115 | 0.176 | +50.8% vs climatology | 92.2% | - | 8939 | 25 | 2024-10-01 - 2026-10-15 |
| Frost | 2 d | lightgbm | Brier 0.102 | 0.137 | 0.114 | 0.176 | +11.0% vs climatology | 86.0% | - | 8927 | 25 | 2024-10-01 - 2026-10-14 |
| Frost | 3 d | climatology | Brier 0.114 | 0.160 | 0.114 | 0.176 | +0.0% vs climatology | 84.2% | - | 8915 | 25 | 2024-10-01 - 2026-10-13 |
| Sunshine (kWh/m²) | 1 d | lightgbm | MAE 0.843 | 0.942 | 1.051 | - | +10.5% vs persistence | - | 1.126 | 7716 | 22 | 2024-10-01 - 2026-07-30 |
| Sunshine (kWh/m²) | 2 d | lightgbm | MAE 0.963 | 1.115 | 1.050 | - | +13.6% vs persistence | - | 1.292 | 7704 | 22 | 2024-10-01 - 2026-07-29 |
| Sunshine (kWh/m²) | 3 d | lightgbm | MAE 1.022 | 1.204 | 1.049 | - | +15.0% vs persistence | - | 1.363 | 7693 | 22 | 2024-10-01 - 2026-07-28 |
| Wind (m/s) | 1 d | lightgbm | MAE 0.829 | 0.920 | 0.997 | - | +9.9% vs persistence | - | 1.074 | 8752 | 25 | 2024-10-01 - 2026-10-15 |
| Wind (m/s) | 2 d | lightgbm | MAE 0.965 | 1.116 | 0.996 | - | +13.5% vs persistence | - | 1.244 | 8740 | 25 | 2024-10-01 - 2026-10-14 |
| Wind (m/s) | 3 d | climatology | MAE 0.996 | 1.173 | 0.996 | - | +15.1% vs persistence | - | 1.290 | 8728 | 25 | 2024-10-01 - 2026-10-13 |

## Per city - 2 days ahead (the horizon used for tomorrow)

| City | Frost Brier | Frost skill vs climatology | Frost accuracy | Sunshine MAE | Sunshine skill vs persistence | Wind MAE | Wind skill vs persistence |
|---|---|---|---|---|---|---|---|
| Berlin | 0.098 | +14.4% | 86.6% | 0.937 | +16.1% | 1.105 | +14.6% |
| Hamburg | 0.102 | +13.6% | 86.6% | 0.876 | +11.9% | 1.209 | +17.8% |
| Munich | 0.085 | +20.7% | 87.2% | 1.128 | +9.5% | 0.782 | +6.0% |
| Cologne | 0.108 | +3.7% | 86.4% | 0.952 | +13.3% | 0.949 | +16.5% |
| Frankfurt am Main | 0.107 | +4.0% | 84.7% | 0.903 | +14.5% | 0.708 | +3.4% |
| Stuttgart | 0.087 | +9.3% | 87.5% | 1.030 | +8.3% | 0.743 | +14.2% |
| Essen | 0.066 | +11.3% | 91.5% | 0.956 | +13.0% | 0.906 | +13.2% |
| Leipzig | 0.121 | +11.2% | 82.8% | 0.944 | +19.1% | 0.705 | +10.1% |
| Bremen | 0.108 | +12.9% | 86.0% | 0.892 | +13.0% | 1.319 | +15.4% |
| Dresden | 0.112 | +14.0% | 84.4% | 1.023 | +15.5% | 1.115 | +15.8% |
| Hannover | 0.102 | +13.3% | 86.3% | 0.872 | +18.1% | 1.181 | +13.7% |
| Nuremberg | 0.127 | +3.6% | 82.3% | 1.042 | +11.2% | 0.899 | +12.7% |

## Per city - 1 day ahead

| City | Frost Brier | Frost skill vs climatology | Frost accuracy | Sunshine MAE | Sunshine skill vs persistence | Wind MAE | Wind skill vs persistence |
|---|---|---|---|---|---|---|---|
| Berlin | 0.056 | +51.2% | 92.3% | 0.822 | +13.1% | 0.945 | +12.8% |
| Hamburg | 0.056 | +52.5% | 92.2% | 0.800 | +11.0% | 1.032 | +14.0% |
| Munich | 0.047 | +56.6% | 93.2% | 0.955 | +11.8% | 0.635 | +6.8% |
| Cologne | 0.061 | +46.0% | 92.2% | 0.850 | +6.4% | 0.847 | +10.9% |
| Frankfurt am Main | 0.061 | +45.4% | 90.9% | 0.782 | +12.8% | 0.571 | +1.5% |
| Stuttgart | 0.044 | +54.4% | 94.0% | 0.877 | +9.7% | 0.661 | +10.9% |
| Essen | 0.035 | +53.7% | 95.7% | 0.838 | +7.7% | 0.788 | +8.2% |
| Leipzig | 0.065 | +52.2% | 91.5% | 0.819 | +11.6% | 0.608 | +5.1% |
| Bremen | 0.061 | +50.7% | 91.5% | 0.816 | +6.0% | 1.112 | +12.3% |
| Dresden | 0.056 | +56.9% | 92.0% | 0.882 | +12.4% | 0.985 | +7.7% |
| Hannover | 0.059 | +49.9% | 91.3% | 0.768 | +13.4% | 1.026 | +12.4% |
| Nuremberg | 0.077 | +41.7% | 89.0% | 0.900 | +9.4% | 0.774 | +8.2% |

## Top features of the served LightGBM models (gain, 1 day ahead)

- **Frost:** temperature_air_min_2m, temperature_air_mean_2m, temperature_air_min_0_05m, temperature_air_mean_2m_mean3d, temperature_air_max_2m
- **Sunshine (kWh/m²):** day_of_year_cos, day_of_year_sin, temperature_air_max_2m, precipitation_height, cloud_cover_total
- **Wind (m/s):** wind_speed, wind_speed_mean3d, day_of_year_cos, wind_speed_mean7d, day_of_year_sin
