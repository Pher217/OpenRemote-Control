import os

from .base import *

DEBUG = True

SECRET_KEY = os.environ.get("SECRET_KEY", "local-dev-key-not-for-production")

ALLOWED_HOSTS = ["*"]

CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]

LOGGING["loggers"]["django.db.backends"]["level"] = "DEBUG"
