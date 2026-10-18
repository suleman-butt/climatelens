# Notebooks

Step-by-step walkthrough of the ClimateLens machine-learning workflow, from raw
DWD data to the deployed service. Each notebook calls the same `climatelens`
package code that runs in production on Cloud Run, and is saved with its outputs
so it can be read directly on GitLab.

| Notebook | Covers |
|---|---|
| [01 Data collection and preparation](01_data_collection_and_preparation.ipynb) | data source, city and station selection, live DWD download, validation, data quality, exploration, preparation plan |
| [02 Feature engineering](02_feature_engineering.ipynb) | targets, the 42 features, missing values, feature-target relationships, look-ahead and gap checks |
| [03 Model training and evaluation](03_model_training_and_evaluation.ipynb) | baselines vs LightGBM, walk-forward validation, results per target, horizon and city, calibration, final training, feature importance, quality gate |
| [04 Deployment and testing](04_deployment_and_testing.ipynb) | cloud architecture and release flow, testing plan, test suite, live post-deployment checks, live forecasts and served-model accuracy |

Figures used in the project report are written to [`figures/`](figures/).

## Run them yourself

```bash
make install
pip install -r notebooks/requirements.txt
climatelens ingest --backfill-years 3   # once: about 3 years of DWD data into ./data
jupyter lab notebooks/
```

The notebooks read the local `./data` folder (no cloud access needed). Notebook 1
also downloads a few days live from DWD, and notebook 4 calls the public service at
<https://climatelens.sulemanb.com>. Notebook 3 refits every model in every fold and
takes a few minutes.
