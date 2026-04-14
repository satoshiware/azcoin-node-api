.PHONY: venv install run run-linux test lint format precommit

venv:
	python -m venv .venv

install:
	python -m pip install --upgrade pip
	pip install -r requirements-dev.txt

run:
	set PYTHONPATH=src && uvicorn node_api.main:app --reload --host 0.0.0.0 --port 8080

run-linux:
	PYTHONPATH=src uvicorn node_api.main:app --host 0.0.0.0 --port 8080

test:
	set PYTHONPATH=src && pytest -q

lint:
	ruff check .

format:
	ruff format .

precommit:
	pre-commit install
