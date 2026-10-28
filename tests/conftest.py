"""Shared pytest configuration.

Hypothesis does not read pyproject.toml, so its profiles are registered here.
The "default" profile runs at least 100 examples per property, as required by the design.
Select another profile with HYPOTHESIS_PROFILE (e.g. "ci" for more examples).
"""

import os

from hypothesis import HealthCheck, settings

settings.register_profile(
    "default",
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
settings.register_profile(
    "ci",
    parent=settings.get_profile("default"),
    max_examples=300,
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
