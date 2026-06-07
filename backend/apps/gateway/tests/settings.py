# Test settings for apps.gateway that extends the base test settings.
# Usage: DJANGO_SETTINGS_MODULE=apps.gateway.tests.settings pytest apps/gateway/
from config.settings.test import *  # noqa: F401, F403

INSTALLED_APPS = list(INSTALLED_APPS) + ["apps.gateway"]  # noqa: F405

ROOT_URLCONF = "apps.gateway.tests.urls"
