from django.apps import AppConfig


class SubscriptionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "subscription"

    def ready(self):
        from . import signals  # noqa: F401

