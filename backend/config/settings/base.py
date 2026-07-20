import os
from pathlib import Path

import structlog
from dotenv import load_dotenv

from apps.observe.validators import validate_observe_delivery_mode

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load local secrets (e.g. TELEGRAM_BOT_TOKEN) into os.environ. Reads the
# repo-root .env first (where docker-compose and the project .env live), then
# backend/.env which overrides if present. Both are gitignored; no-op if absent.
load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env", override=True)

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-key-do-not-use-in-production")

DEBUG = False

ALLOWED_HOSTS = os.environ.get("ALLOWED_HOSTS", "").split(",")
ALLOWED_HOSTS = [h.strip() for h in ALLOWED_HOSTS if h.strip()] or ["localhost", "127.0.0.1"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "channels",
    "apps.accounts",
    "apps.hosts",
    "apps.projects",
    "apps.policies",
    "apps.threads",
    "apps.tier2",
    "apps.approvals",
    "apps.audit",
    "apps.skills",
    "apps.slash",
    "apps.telegram",
    "apps.observe",
    "apps.prompts",
    "apps.connectors",
    "apps.gateway",
    "apps.hostlink",
    "apps.supervisor",
    "apps.setup",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "openremote_control"),
        "USER": os.environ.get("POSTGRES_USER", "acc_user"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "acc_password"),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        },
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [REDIS_URL],
        },
    },
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

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

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ALLOWED_CHAT_IDS = {
    int(x)
    for x in os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "").replace(" ", "").split(",")
    if x
}
TELEGRAM_DEFAULT_MODEL = os.environ.get("TELEGRAM_DEFAULT_MODEL", "kimi-k2.6:cloud")
TELEGRAM_FORUM_CHAT_ID = os.environ.get("TELEGRAM_FORUM_CHAT_ID", "")
TELEGRAM_USER_LABEL = os.environ.get("TELEGRAM_USER_LABEL", "You")
TELEGRAM_ASSISTANT_LABEL = os.environ.get("TELEGRAM_ASSISTANT_LABEL", "Claude")

# Controls how a driveable session's assistant turns are streamed to Telegram.
# "progress"       — collapse consecutive assistant turns into one edited message (default).
# "all"            — post a new silent message per turn (legacy behaviour).
# "milestones_only"— drop assistant turns entirely; only user turns + session-start notify.
OBSERVE_DELIVERY_MODE = os.environ.get("OBSERVE_DELIVERY_MODE", "progress")
validate_observe_delivery_mode(OBSERVE_DELIVERY_MODE)

# Universal MCP connector bridge (apps.connectors): shared bearer token a connector
# must present, and the chat id where connector Prompts (ask/approve) are delivered.
ORC_CONNECTOR_TOKEN = os.environ.get("ORC_CONNECTOR_TOKEN", "")
ORC_PROMPT_CHAT_ID = os.environ.get("ORC_PROMPT_CHAT_ID") or os.environ.get(
    "TELEGRAM_FORUM_CHAT_ID", ""
)
# Multi-host (apps.hostlink): pre-shared one-time enrollment secret a host daemon
# presents once to receive a per-host token.
ORC_ENROLL_SECRET = os.environ.get("ORC_ENROLL_SECRET", "")

# First-run setup wizard (apps.setup). The wizard is token-gated and reachable
# only on loopback: ORC_SETUP_ALLOWED_HOSTS pins the Host header, which defeats
# DNS rebinding from a malicious page (a REMOTE_ADDR check would instead break
# under Docker, where the peer address is the bridge gateway, not 127.0.0.1).
ORC_SETUP_ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ORC_SETUP_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if h.strip()
]
# Base URL printed by `manage.py setup_token` — where the operator's browser opens.
ORC_SETUP_BASE_URL = os.environ.get("ORC_SETUP_BASE_URL", "http://127.0.0.1:8000")
# The .env file the wizard writes collected credentials into.
ORC_SETUP_ENV_FILE = os.environ.get("ORC_SETUP_ENV_FILE", str(BASE_DIR.parent / "deploy" / ".env"))

# Public backend URL embedded in QR pairing payloads (`/pair`, `manage.py orc_pair`).
# Empty => the pairing payload degrades to just the code (manual --backend still works).
ORC_PUBLIC_BASE_URL = os.environ.get("ORC_PUBLIC_BASE_URL", "")

# The single messaging app of choice. ORC is a single-platform aggregator: every
# session and prompt the operator follows flows to ONE app. One of:
# telegram | whatsapp | slack | signal | imessage | discord. Defaults to telegram.
ORC_MESSAGING_PLATFORM = (os.environ.get("ORC_MESSAGING_PLATFORM") or "telegram").strip().lower()

# Messaging gateway (apps.gateway). Node sidecar delivers to WhatsApp/Slack/Discord/Signal/iMessage.
MESSAGING_GATEWAY_TOKEN = os.environ.get("MESSAGING_GATEWAY_TOKEN", "")
ORC_PROMPT_WHATSAPP = os.environ.get("ORC_PROMPT_WHATSAPP", "")
ORC_PROMPT_SLACK    = os.environ.get("ORC_PROMPT_SLACK", "")
ORC_PROMPT_DISCORD  = os.environ.get("ORC_PROMPT_DISCORD", "")
ORC_PROMPT_SIGNAL   = os.environ.get("ORC_PROMPT_SIGNAL", "")
ORC_PROMPT_IMESSAGE = os.environ.get("ORC_PROMPT_IMESSAGE", "")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

CORS_ALLOWED_ORIGINS = os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
CORS_ALLOWED_ORIGINS = [o.strip() for o in CORS_ALLOWED_ORIGINS if o.strip()]
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = os.environ.get("CSRF_TRUSTED_ORIGINS", "http://localhost:3000").split(",")
CSRF_TRUSTED_ORIGINS = [o.strip() for o in CSRF_TRUSTED_ORIGINS if o.strip()]

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.dev.ConsoleRenderer(),
            "foreign_pre_chain": [
                structlog.stdlib.add_log_level,
                structlog.stdlib.add_logger_name,
                structlog.processors.TimeStamper(fmt="iso"),
            ],
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.db.backends": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        # httpx/httpcore log each request URL at INFO; those URLs can carry
        # secrets (e.g. the Telegram bot token in the path). Keep them quiet.
        "httpx": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "httpcore": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}

structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.ConsoleRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)
