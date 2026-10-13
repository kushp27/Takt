"""settings.py refuses to start outside DEBUG without a real DJANGO_SECRET_KEY."""

import os
import subprocess
import sys

import pytest
from django.conf import settings

PROBE = "import django; django.setup()"


def _start(**env):
    full = {k: v for k, v in os.environ.items() if k not in ("DJANGO_SECRET_KEY", "DJANGO_DEBUG")}
    full.update(env, DJANGO_SETTINGS_MODULE="takt.settings")
    return subprocess.run([sys.executable, "-c", PROBE], env=full, cwd=settings.BASE_DIR,
                          capture_output=True, text=True)


@pytest.mark.parametrize("key", [None, "", "dev-only-insecure-key", "change-me-to-a-long-random-string"])
def test_refuses_missing_or_placeholder_key_when_not_debug(key):
    r = _start(**({} if key is None else {"DJANGO_SECRET_KEY": key}))
    assert r.returncode != 0 and "ImproperlyConfigured" in r.stderr


def test_starts_with_a_real_key_or_in_debug():
    assert _start(DJANGO_SECRET_KEY="a-real-secret-value").returncode == 0
    assert _start(DJANGO_DEBUG="true").returncode == 0
