from django.apps import AppConfig


class RecipesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "recipes"
    verbose_name = "Recipes"

    def ready(self):
        from . import checks  # noqa: F401  (registers the theme checks)
