# ai-pen-plotter — common tasks. Activate your venv first (see README).
PY ?= python
CORE := */plotter/paths.py,*/plotter/gcode.py,*/plotter/text_layout.py,*/plotter/grbl.py,*/plotter/config.py

.PHONY: install dev test cov-core lint format typecheck check preview-sample samples gui clean

install:            ## runtime install (editable)
	$(PY) -m pip install -e .

dev:                ## runtime + dev tools + git hooks
	$(PY) -m pip install -e . -r requirements-dev.txt
	pre-commit install

test:               ## full test suite with coverage, then the ≥85% gate on core modules
	$(PY) -m pytest --cov=plotter --cov-report=term-missing
	$(PY) -m coverage report --include="$(CORE)" --fail-under=85

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

typecheck:
	mypy plotter

check: lint typecheck test

samples:            ## regenerate the synthetic sample inputs + calibration G-code
	$(PY) scripts/make_samples.py
	$(PY) scripts/make_calibration.py

preview-sample:     ## render samples → output/*.gcode + output/*.png
	plotter draw samples/lineart.png --mode lineart --out output/lineart.gcode --preview output/lineart_preview.png
	plotter draw samples/photo.jpg --mode hatch --out output/photo.gcode --preview output/photo_preview.png
	plotter write samples/sample_text.txt --out output/text.gcode --preview output/text_preview.png

gui:
	streamlit run app/streamlit_app.py

clean:
	rm -rf build dist *.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	find output -type f ! -name .gitkeep -delete
