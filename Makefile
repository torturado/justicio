PYTHON := $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

init:
	$(PYTHON) -m pip install -r requirements.txt

isort:
	$(PYTHON) -m isort --check-only src evaluation

format: isort
	$(PYTHON) -m black src evaluation

isort-fix:
	$(PYTHON) -m isort src evaluation
