"""Shared fixtures for tests/tools."""

from __future__ import annotations

import importlib.util
import sys
import types

import pytest


@pytest.fixture
def stub_faster_whisper():
    """Make ``patch("faster_whisper.WhisperModel")`` resolvable without the extra.

    ``test_transcription.py`` opens by promising that "all external
    dependencies (faster_whisper, openai) are mocked", and every test in these
    classes does mock the model and patches ``_HAS_FASTER_WHISPER`` by hand.
    But ``mock.patch`` resolves a string target by IMPORTING the module named
    in it, so the package still had to be installed for a test that never
    calls a line of it.

    The ``voice`` extra is deliberately not installed — hundreds of megabytes
    of ML runtime for a feature this desk does not use — so six tests of our
    own provider dispatch failed on every local run. They were carried as
    "environmental baseline", which is the expensive part: a suite with ten
    permanently red tests teaches everyone to read red as normal, and a real
    regression then has to be noticed rather than seen.

    Installing a stub for the duration of the test makes the module's stated
    contract true and lets the dispatch logic actually be tested. Nothing
    touches the stub — each test replaces ``WhisperModel`` via ``patch``.
    """
    if importlib.util.find_spec("faster_whisper") is not None:
        yield  # the real extra is installed; leave it alone
        return

    stub = types.ModuleType("faster_whisper")
    stub.WhisperModel = object  # placeholder; patch() swaps it per test
    sys.modules["faster_whisper"] = stub
    try:
        yield
    finally:
        # Only remove what we put there — a concurrent xdist worker importing
        # the real package must not have it pulled out from under it.
        if sys.modules.get("faster_whisper") is stub:
            del sys.modules["faster_whisper"]
