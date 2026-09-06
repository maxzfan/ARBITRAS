"""Put the repo root on sys.path for every pytest invocation.

`console` and `backend` are namespace packages -- neither has an __init__.py --
so `from console import missions` only resolves when the repo root is on
sys.path. `python -m pytest` puts it there itself (the -m flag prepends the
working directory); a bare `pytest` does not, and pytest's own prepend-import
insertion stops at `console/`, because `console/tests/__init__.py` exists but
`console/__init__.py` does not.

That divergence is what broke the deploy: .github/workflows/deploy.yml gates
the Pages publish on `pytest -q console/tests/test_static_build.py`, which
failed collection with ModuleNotFoundError while the same file passed locally
under `python -m pytest`. A root conftest.py is imported before collection and
its directory is prepended, so both invocations now agree.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
