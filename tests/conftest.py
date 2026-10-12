import pytest


@pytest.fixture(autouse=True)
def _db_for_all(db):
    """Every Takt test runs against the real Postgres test database (eng review D8)."""


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    """Pages extend web/base.html, which uses {% static %}. The manifest storage needs
    collectstatic, which the test image doesn't run, so tests use plain static storage."""
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


@pytest.fixture(autouse=True)
def _fast_password_hasher(settings):
    """Tests create many users; PBKDF2 makes each one slow. Never used outside tests."""
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
