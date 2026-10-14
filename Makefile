.PHONY: install lock lint test pipeline serve build run container-pipeline version release-image deploy destroy

IMAGE ?= climatelens:local
# Release version: climatelens.__version__ is the single source of truth.
VERSION := $(shell python -c "import re; print(re.search(r'__version__ = \"(.+)\"', open('src/climatelens/__init__.py').read()).group(1))")
REGISTRY ?= europe-west3-docker.pkg.dev/climatelens-iu/climatelens/climatelens
# Local data and model folders mounted into the container.
VOLUMES = -v "$(CURDIR)/data:/app/data" -v "$(CURDIR)/models:/app/models"

install:
	python -m pip install --upgrade pip
	python -m pip install --require-hashes -r requirements-dev.lock
	python -m pip install --no-deps -e .

# Re-resolve every dependency (direct and transitive) after editing pyproject.toml.
lock:
	uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes -o requirements.lock
	uv pip compile pyproject.toml --extra dev --universal --python-version 3.12 --generate-hashes -o requirements-dev.lock

lint:
	python -m ruff check .
	python -m ruff format --check .

test:
	python -m pytest

# Refresh data and models locally: recent DWD days -> features -> retrain.
pipeline:
	climatelens ingest
	climatelens features
	climatelens train

serve:
	python -m uvicorn climatelens.api:app --port 8000 --reload --reload-dir src

build:
	docker build --target api --build-arg VERSION=$(VERSION) -t $(IMAGE) .

# Serve the dashboard from the container on http://localhost:8080.
run:
	docker run --rm -p 8080:8080 $(VOLUMES) $(IMAGE)

# The same pipeline, run inside the container against the mounted folders.
container-pipeline:
	docker run --rm $(VOLUMES) $(IMAGE) climatelens ingest
	docker run --rm $(VOLUMES) $(IMAGE) climatelens features
	docker run --rm $(VOLUMES) $(IMAGE) climatelens train

version:
	@echo $(VERSION)

# Build and push v$(VERSION) to Artifact Registry. Tags are immutable there, so
# bump __version__ before releasing again. (Phase 8 moves this into CI.)
release-image:
	docker build --target api --build-arg VERSION=$(VERSION) -t $(REGISTRY):v$(VERSION) .
	docker push $(REGISTRY):v$(VERSION)

deploy:
	@echo "Deployment is added with the Terraform infrastructure (Phase 7)."

destroy:
	@echo "Destroy is added with the Terraform infrastructure (Phase 7)."
