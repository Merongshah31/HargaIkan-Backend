"""Minimal checks that the scaffold is importable and the test configuration is active."""

import importlib

import pytest
from hypothesis import settings

SUBPACKAGES = ["domain", "core", "pipeline", "forecast", "search", "repositories", "services", "ui"]


@pytest.mark.parametrize("name", ["hargaikan", "hargaikan.config", *(f"hargaikan.{p}" for p in SUBPACKAGES)])
def test_package_imports(name: str) -> None:
    assert importlib.import_module(name) is not None


def test_hypothesis_profile_runs_at_least_100_examples() -> None:
    assert settings().max_examples >= 100
