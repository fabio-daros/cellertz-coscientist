"""Django settings for the Cellertz web layer.

This package is transport, persistence and admin. The science stays in
`cellertz`, which imports nothing from Django and is not aware of it.
Nothing here replaces the running conversation service yet: Django is added
alongside it so the stores can be migrated one at a time.

Every value that differs between a workstation and the pilot comes from the
environment. A missing production secret raises instead of falling back.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.core.management.utils import get_random_secret_key

# src/coscientist/settings.py -> repository root
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _names(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


DEBUG = _flag("CELLERTZ_DJANGO_DEBUG", default=False)

# A deployment must state its own key. A generated key is acceptable only
# while DEBUG is on, and it changes on every restart, which signs out sessions.
SECRET_KEY = os.environ.get("CELLERTZ_DJANGO_SECRET_KEY", "").strip()
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured(
            "CELLERTZ_DJANGO_SECRET_KEY is required when CELLERTZ_DJANGO_DEBUG is off."
        )
    SECRET_KEY = get_random_secret_key()

ALLOWED_HOSTS = _names("CELLERTZ_DJANGO_ALLOWED_HOSTS") or (["127.0.0.1", "localhost"] if DEBUG else [])
CSRF_TRUSTED_ORIGINS = _names("CELLERTZ_DJANGO_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "coscientist.literature",
    "coscientist.agents",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "coscientist.urls"
WSGI_APPLICATION = "coscientist.wsgi.application"
ASGI_APPLICATION = "coscientist.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "src" / "coscientist" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Django owns this database alone. The eight stores the conversation service
# writes today are migrated into it one at a time, not adopted in place.
#
# Two processes already write here: the API stores literature replies while
# the admin stores agent prompts. SQLite serialises that with a file lock,
# which holds at this size and is why it is still the default. PostgreSQL is
# selected per installation, and is what a deployment that cannot ship a file
# — a managed database, more than one host — uses instead.
SQLITE = "sqlite"
POSTGRES = "postgres"
_DATABASE_ENGINE = (os.environ.get("CELLERTZ_DJANGO_DB_ENGINE") or SQLITE).strip().lower()
if _DATABASE_ENGINE not in {SQLITE, POSTGRES}:
    raise ImproperlyConfigured("CELLERTZ_DJANGO_DB_ENGINE must be 'sqlite' or 'postgres'.")


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ImproperlyConfigured(f"{name} is required when CELLERTZ_DJANGO_DB_ENGINE is 'postgres'.")
    return value


if _DATABASE_ENGINE == POSTGRES:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _required("CELLERTZ_DJANGO_DB_NAME"),
            "USER": _required("CELLERTZ_DJANGO_DB_USER"),
            "PASSWORD": _required("CELLERTZ_DJANGO_DB_PASSWORD"),
            "HOST": os.environ.get("CELLERTZ_DJANGO_DB_HOST", "127.0.0.1").strip(),
            "PORT": os.environ.get("CELLERTZ_DJANGO_DB_PORT", "5432").strip(),
            # A connection reused across requests, closed by the service's own
            # call to close_old_connections when it is not in a request cycle.
            "CONN_MAX_AGE": 60,
        }
    }
else:
    _DATABASE_PATH = Path(
        os.environ.get("CELLERTZ_DJANGO_DB") or BASE_DIR / "var" / "cellertz-web.sqlite3"
    )
    # SQLite opens a file but does not create the directory holding it.
    _DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(_DATABASE_PATH),
            "OPTIONS": {"transaction_mode": "IMMEDIATE", "timeout": 20},
        }
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = os.environ.get("CELLERTZ_DJANGO_STATIC_ROOT") or str(BASE_DIR / "var" / "static")

# The pilot terminates TLS in nginx and proxies over loopback.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
X_FRAME_OPTIONS = "DENY"
