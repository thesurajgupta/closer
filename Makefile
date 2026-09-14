.PHONY: help setup install run serve ui dev test lint demo schedule report clean

PY := .venv/bin/python
PIP := .venv/bin/pip

help:
	@echo "CLOSER"
	@echo ""
	@echo "  make setup     create the venv, install everything, build the UI"
	@echo "  make demo      one CLOSER run in the terminal"
	@echo "  make serve     start the app at http://127.0.0.1:8000"
	@echo "  make dev       API + Vite dev server with hot reload"
	@echo "  make test      the full test suite"
	@echo "  make schedule  run the background scheduler in the foreground"
	@echo "  make report    print the latest run's evidence bundle"
	@echo "  make clean     remove the local store and build output"

setup: install ui

install:
	python3 -m venv .venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements-dev.txt
	$(PIP) install -q -e .

ui:
	cd frontend && npm install --silent && npm run build

demo:
	PYTHONPATH=backend $(PY) -m closer run --quiet

decisions:
	PYTHONPATH=backend $(PY) -m closer decisions

serve: ui
	PYTHONPATH=backend $(PY) -m closer serve

dev:
	PYTHONPATH=backend $(PY) -m closer serve --reload & \
	cd frontend && npm run dev

schedule:
	PYTHONPATH=backend $(PY) -m closer schedule --interval 10

report:
	PYTHONPATH=backend $(PY) -m closer report

test:
	$(PY) -m pytest

clean:
	rm -f closer.db closer.db-wal closer.db-shm
	rm -rf frontend/dist .pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
