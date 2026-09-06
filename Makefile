.PHONY: install lint test build deploy destroy

install:
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]"

lint:
	python -m ruff check .
	python -m ruff format --check .

test:
	python -m pytest

build:
	docker build --target api -t climatelens:local .

deploy:
	@echo "Deployment will be enabled after the GCP infrastructure is implemented."

destroy:
	@echo "Destroy will be enabled after the GCP infrastructure is implemented."
