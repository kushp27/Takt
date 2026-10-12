import re
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.db import connection


def test_database_is_postgres():
    assert connection.vendor == "postgresql"


def test_use_tz_is_on():
    assert settings.USE_TZ is True


def test_no_missing_migrations():
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


def test_healthz_reachable(client):
    resp = client.get("/healthz")
    assert resp.status_code in (200, 503)
    assert resp["Content-Type"].startswith("application/json")


def test_home_requires_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


def _luminance(hex_colour):
    r, g, b = (int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


@pytest.mark.parametrize("token", ["--ink", "--text-2", "--accent", "--behind", "--yield"])
def test_text_tokens_meet_4_5_to_1_on_white(token):
    """D17: all text colours meet 4.5:1 or better on white (D-T1 verify)."""
    css = (Path(settings.BASE_DIR) / "web/static/css/tokens.css").read_text()
    value = re.search(rf"{token}:\s*(#[0-9A-Fa-f]{{6}})", css).group(1)
    ratio = (1.0 + 0.05) / (_luminance(value) + 0.05)
    assert ratio >= 4.5, f"{token} {value} is {ratio:.2f}:1"
