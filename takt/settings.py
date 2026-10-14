"""Django settings for Takt.

Every value that differs between the laptop, CI and the IT-hosted VM comes from
the environment. See .env.example for the full list.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).lower() in ("1", "true", "yes", "on")


DEV_SECRET_KEY = "dev-only-insecure-key"
SECRET_KEY = env("DJANGO_SECRET_KEY", DEV_SECRET_KEY)
DEBUG = env_bool("DJANGO_DEBUG", False)
# The secret signs sessions and the Gate 1 page tokens/letters (accounts.policy). Outside
# DEBUG it must be set to a real value: never the dev default or the .env.example placeholder.
if not DEBUG and SECRET_KEY in ("", DEV_SECRET_KEY, "change-me-to-a-long-random-string"):
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured("Set DJANGO_SECRET_KEY to a long random value (see .env.example).")
ALLOWED_HOSTS = [h for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
CSRF_TRUSTED_ORIGINS = [o for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if o]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "accounts",
    "ingest",
    "metrics",
    "trust",
    "disputes",
    "ops",
    "web",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    # D13: plain 503 page when the database is unreachable (before anything that queries).
    "ops.middleware.DatabaseUnavailableMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "takt.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "ops.context_processors.freshness",  # lazy per-hub freshness (Component 9, D9)
                "web.context_processors.nav",  # top nav + open-dispute badge (eng review D6)
            ],
        },
    },
]

WSGI_APPLICATION = "takt.wsgi.application"

# Postgres only: the metric layer is SQL views (eng review D8), so SQLite is never used.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "takt"),
        "USER": env("POSTGRES_USER", "takt"),
        "PASSWORD": env("POSTGRES_PASSWORD", "takt"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --------------------------------------------------------------------------
# Authentication (design Component 7)
# Pilot: Django local accounts for named pilot users only
# (`manage.py seed_demo_users` creates dev/demo accounts; never use it in production).
#
# SSO/LDAP STUB -- NOT INSTALLED, NOT ACTIVE.
# Before rollout beyond the pilot, django-auth-ldap (matching IT's other plant
# tools) replaces local accounts. Blocked on Open Question 6: which SSO or LDAP do
# IT's plant tools use? When answered:
#   1. add django-auth-ldap to requirements.txt;
#   2. AUTHENTICATION_BACKENDS = ["django_auth_ldap.backend.LDAPBackend"]
#      (keep ModelBackend only for the break-glass builder account);
#   3. AUTH_LDAP_SERVER_URI, AUTH_LDAP_BIND_DN, AUTH_LDAP_BIND_PASSWORD from env;
#   4. map LDAP groups to accounts.Profile.role and Profile.areas/shift
#      (a populate_user signal handler in accounts/), and is_superuser only for the builder.
# Until then Django's default ModelBackend is used.
# --------------------------------------------------------------------------
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "en-us"
# Eng review D2: store UTC; each hub carries its own plant timezone in the admin.
# TIME_ZONE is only the display default for the UI and admin.
USE_TZ = True
TIME_ZONE = env("TAKT_DISPLAY_TIMEZONE", "America/Chicago")
USE_I18N = False

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": env("TAKT_LOG_LEVEL", "INFO")},
}
