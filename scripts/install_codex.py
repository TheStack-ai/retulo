#!/usr/bin/env python3
"""Compatibility entry point; the guarded implementation ships inside Retulo."""
from pathlib import Path
import sys

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
if (_SOURCE_ROOT/'src/retulo').is_dir():
    sys.path.insert(0, str(_SOURCE_ROOT/'src'))
from retulo.integrations import codex as _backend


def __getattr__(name):
    return getattr(_backend, name)


def main(argv=None):
    return _backend.main(argv, default_repo=_SOURCE_ROOT)


if __name__ == '__main__':
    sys.exit(main())
