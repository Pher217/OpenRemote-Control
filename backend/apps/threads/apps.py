from django.apps import AppConfig


class ThreadsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.threads"

    def ready(self):
        import apps.threads.signals  # noqa: F401
