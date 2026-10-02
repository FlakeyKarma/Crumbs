"""Settings for Crumbs — recipe management and viewing.

Everything that differs between a laptop and a real server is read from the
environment, so the same checkout runs in both places. See .env.example.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_flag(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --- Core -------------------------------------------------------------------

SECRET_KEY = os.environ.get("CRUMBS_SECRET_KEY", "insecure-development-key-replace-me")
DEBUG = env_flag("CRUMBS_DEBUG", True)
ALLOWED_HOSTS = env_list("CRUMBS_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
CSRF_TRUSTED_ORIGINS = env_list("CRUMBS_CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "recipes",
    "pantry",
    "health",
    "nutrition",
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

ROOT_URLCONF = "crumbs.urls"
WSGI_APPLICATION = "crumbs.wsgi.application"
ASGI_APPLICATION = "crumbs.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "recipes.context_processors.site",
            ],
        },
    },
]

# --- Data -------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("CRUMBS_DB_PATH", BASE_DIR / "db.sqlite3"),
        "OPTIONS": {"init_command": "PRAGMA journal_mode=WAL;"},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Accounts ---------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "recipes:list"
LOGOUT_REDIRECT_URL = "recipes:list"

# --- Locale -----------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("CRUMBS_TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

# --- Files ------------------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.environ.get("CRUMBS_MEDIA_ROOT", BASE_DIR / "media"))

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Photos of dinner do not need to be enormous.
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# --- Theme ------------------------------------------------------------------
#
# A profile needs five things:
#
#   name          what it is called, shown wherever themes are listed
#   description   one line, so future-you remembers why it exists
#   roundness     corner radius on buttons, inputs, photos and chips —
#                 a number of pixels, or a string like "0", "6px", "0.4rem"
#   primary       links, primary buttons, step numbers, the active accent
#   secondary     running timers and warnings, and nothing else
#
# Everything else is worked out from those: the hover shade, lighter versions
# for dark mode, and black-or-white text on top of the primary, chosen by
# luminance. Pin any of them yourself by adding the key to the profile:
#
#   primary_hover  primary_dark  primary_dark_hover
#   secondary_dark  on_primary  on_primary_dark
#
# `python manage.py check` validates all of this and names the offending key,
# so a mistyped colour is a startup message rather than a mystery on the page.

CRUMBS_THEMES = {
    "enamel": {
        "name": "Enamel",
        "description": (
            "The default. Chalk, slate and a jar of preserves — cool enough "
            "that food photos look like food rather than like the background."
        ),
        "roundness": "3px",
        "primary": "#8e2c3f",
        "secondary": "#b6801c",
        # Pinned rather than derived: the dark-mode pink is deliberately more
        # saturated than a straight lightening would give.
        "primary_dark": "#e0899a",
        "primary_dark_hover": "#f0a5b3",
        "secondary_dark": "#e3b44a",
    },
    "orchard": {
        "name": "Orchard",
        "description": (
            "Softer edges and a garden green. Reads as friendlier, and suits "
            "a box that is mostly vegetables and baking."
        ),
        "roundness": "10px",
        "primary": "#2f6b4f",
        "secondary": "#c2791f",
    },
    "inkwell": {
        "name": "Inkwell",
        "description": (
            "Square corners and a printer's navy. The most restrained option, "
            "and the one that prints best."
        ),
        "roundness": "0",
        "primary": "#1f3a5f",
        "secondary": "#8a6410",
    },
    "clementine": {
        "name": "Clementine",
        "description": (
            "Warm and loud, with generous corners. Good on a kitchen tablet "
            "across the room; a lot at a desk."
        ),
        "roundness": "14px",
        "primary": "#b8461a",
        "secondary": "#3f7d6a",
    },
}

#: Which profile is in use. Unknown names fall back to the first one defined.
CRUMBS_THEME = os.environ.get("CRUMBS_THEME", "enamel")


# --- The Pantry -------------------------------------------------------------
#
# A user's FoodData Central key is theirs, not ours, so it is encrypted at
# rest. Set this to something of its own: with it unset the key is derived
# from SECRET_KEY, which means rotating SECRET_KEY silently invalidates every
# stored API key. Recoverable — each user pastes theirs again — but it should
# not be a surprise, so `manage.py check` warns while the fallback is in use.
#
#   python -c "import secrets; print(secrets.token_urlsafe(48))"

PANTRY_ENCRYPTION_KEY = os.environ.get("CRUMBS_PANTRY_KEY", "")

# --- Messages ---------------------------------------------------------------

from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {
    message_constants.DEBUG: "note",
    message_constants.INFO: "note",
    message_constants.SUCCESS: "good",
    message_constants.WARNING: "warn",
    message_constants.ERROR: "bad",
}

# --- Hardening (only bites once DEBUG is off) -------------------------------

if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    X_FRAME_OPTIONS = "DENY"

    # Set CRUMBS_BEHIND_TLS=1 once the site is actually served over https,
    # otherwise you will lock yourself out of a plain-http LAN deployment.
    if env_flag("CRUMBS_BEHIND_TLS", False):
        SECURE_SSL_REDIRECT = True
        SESSION_COOKIE_SECURE = True
        CSRF_COOKIE_SECURE = True
        SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
