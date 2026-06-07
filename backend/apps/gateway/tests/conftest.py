from django.conf import settings


def pytest_configure(config):
    if "apps.gateway" not in settings.INSTALLED_APPS:
        settings.INSTALLED_APPS = list(settings.INSTALLED_APPS) + ["apps.gateway"]
