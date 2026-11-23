---
name: python-engineer
description: Senior Python engineer standards for the ai-pen-plotter. Use before writing or editing any Python file, tests, packaging (pyproject), CLI (Typer), GUI (Streamlit), or CI.
---

# Python engineer — ai-pen-plotter

## Workflow for every change
1. Read the relevant CLAUDE.md module spec (section 7) and its tests.
2. Write or adjust tests first for new behaviour (pytest; hardware-free).
3. Implement the smallest clean change.
4. Run: `ruff check . && ruff format . && mypy plotter && pytest -q --cov=plotter`.
5. Fix every failure before moving on. Never skip or xfail a test to get green.

## Code standards
- Python ≥ 3.10, `from __future__ import annotations`, full type hints, mypy --strict clean.
- Geometry = numpy float64 arrays of shape (N, 2) in mm. Vectorise; no per-point Python loops in hot paths when numpy can do it.
- Pure functions for pipeline stages; side effects (camera, serial, network, files) are isolated in their own modules.
- Config is an immutable dataclass passed explicitly. No globals, no singletons.
- Errors: raise the specific exceptions from plotter/errors.py with a message that tells the user how to fix it.
- logging in library code; rich only in cli.py and the GUI. Never print secrets.
- pathlib for paths; no OS-specific assumptions (Windows COM ports, macOS /dev/cu.*, Linux /dev/ttyACM*).
- Google-style docstrings with Args/Returns/Raises on public functions.
- Keep modules under ~400 lines; split helpers out when they grow.

## Dependencies
- Add a dependency only if it saves real work. Pin it in requirements.txt and declare it in pyproject.
- Check that a package actually exists on PyPI and works for Python 3.10–3.12 before using it.
- The Anthropic SDK is used only in ocr.py. Read the model name and key from env vars; never hardcode either.

## Testing
- Fixtures in conftest.py: default config, synthetic images drawn with OpenCV, FakeGrbl, mocked AI client.
- Test behaviour and invariants (bounds, pen-up-before-travel, buffer ≤ 127 bytes, determinism with seeds), not implementation details.
- CLI tests use typer.testing.CliRunner with --dry-run.
- Coverage ≥ 85% on core modules.

## Packaging and CI
- pyproject.toml with the console script `plotter = plotter.cli:app`; tool configs for ruff, mypy, pytest live there.
- GitHub Actions matrix: ubuntu-latest + windows-latest, Python 3.10/3.11/3.12. Install tesseract on ubuntu for the OCR test path, or mock it.
- Conventional commits; one logical change per commit.
