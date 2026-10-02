from django.apps import AppConfig


class PantryConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pantry"
    verbose_name = "The Pantry"

    def ready(self):
        from . import checks  # noqa: F401  (registers the encryption-key check)
