"""Reusable Streamlit UI helpers (layer 4).

Badges always combine an icon with text so no status relies on colour alone.
All visible strings come from ``hargaikan.domain.messages``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import streamlit as st

from hargaikan.core.verdict import Verdict
from hargaikan.domain import messages as m
from hargaikan.domain.models import SourceLabel

logger = logging.getLogger(__name__)


def verdict_badge(v: Verdict) -> str:
    """Return the verdict badge text, icon plus text, e.g. "✅ Dalam julat sejarah"."""
    return f"{v.icon} {v.text}"


def source_badge(label: SourceLabel, demo: bool = False) -> str:
    """Return the badge text for a Source_Label, e.g. "📊 Harga sejarah · Data demo"."""
    text = f"{m.SOURCE_ICONS[label.value]} {label.value}"
    if demo:
        text += f" · {m.DEMO_ICON} {m.DEMO_DATA}"
    return text


def render_section(title: str, fn: Callable[[], None]) -> bool:
    """Render one page section in isolation.

    Shows ``title`` as a subheader, then runs ``fn`` inside a placeholder. If ``fn`` raises,
    anything it drew is replaced with the section-unavailable notice for this section only,
    and the rest of the page keeps rendering. Returns True when the section rendered normally.

    Only ``Exception`` is caught, so Streamlit control flow (``st.stop``, ``st.rerun``),
    which derives from ``BaseException``, still works.
    """
    st.subheader(title)
    placeholder = st.empty()
    try:
        with placeholder.container():
            fn()
    except Exception as exc:  # noqa: BLE001 - isolation is the point of this helper
        # Log only the exception type and section: messages may carry user data or secrets.
        logger.warning("Section %r failed: %s", title, type(exc).__name__)
        placeholder.warning(m.SECTION_UNAVAILABLE, icon=m.SECTION_UNAVAILABLE_ICON)
        return False
    return True
