"""Property-based tests for hargaikan.config secret resolution."""

from __future__ import annotations

import os

import pytest
import streamlit
from hypothesis import given
from hypothesis import strategies as st

from hargaikan.config import get_secret


class _MissingSecretsFile:
    """Behaves like st.secrets when no secrets.toml exists: any access raises."""

    def __contains__(self, name: object) -> bool:
        raise FileNotFoundError("No secrets files found.")

    def __getitem__(self, name: str) -> str:
        raise FileNotFoundError("No secrets files found.")


# Valid environment-variable names, prefixed so they cannot clash with real settings.
secret_names = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_", min_size=1, max_size=20).map(
    lambda s: f"HARGAIKAN_PBT_{s}"
)

# Values: arbitrary printable text (no NUL/control/surrogates, which env vars cannot hold),
# plus explicit blank and whitespace-only values that must count as undefined.
non_blank_values = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=40
).filter(lambda v: v.strip() != "")
blank_values = st.sampled_from(["", " ", "   ", "\u3000", " \u00a0 "])
values = st.one_of(non_blank_values, blank_values)

# st.secrets state: secrets file absent, file present without the key, or file with the key.
st_secret_states = st.one_of(
    st.just(("no_file", None)),
    st.just(("no_key", None)),
    values.map(lambda v: ("has_key", v)),
)
# Environment state: unset, or set to a value.
env_states = st.one_of(st.none(), values)


def _expected(st_state: tuple[str, str | None], env_value: str | None) -> str | None:
    """Oracle: st.secrets value if defined, else env value if defined, else None (blank = undefined)."""
    kind, st_value = st_state
    if kind == "has_key" and st_value is not None and st_value.strip():
        return st_value
    if env_value is not None and env_value.strip():
        return env_value
    return None


# Feature: hargaikan-v2, Property 1: Secret resolution precedence
@given(name=secret_names, st_state=st_secret_states, env_value=env_states)
def test_secret_resolution_precedence(
    name: str, st_state: tuple[str, str | None], env_value: str | None
) -> None:
    """**Validates: Requirements 1.1**"""
    kind, st_value = st_state
    if kind == "no_file":
        fake_secrets: object = _MissingSecretsFile()
    elif kind == "no_key":
        fake_secrets = {"SOME_OTHER_KEY": "other"}
    else:
        fake_secrets = {name: st_value}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(streamlit, "secrets", fake_secrets)
        if env_value is None:
            mp.delenv(name, raising=False)
        else:
            mp.setenv(name, env_value)
        assert os.environ.get(name) == env_value
        assert get_secret(name) == _expected(st_state, env_value)
