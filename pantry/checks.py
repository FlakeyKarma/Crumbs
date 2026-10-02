"""System checks for the Pantry.

`fields.EncryptedTextField` falls back to deriving its key from SECRET_KEY
when PANTRY_ENCRYPTION_KEY is unset. That fallback works, but it ties every
stored API key to a value people rotate for unrelated reasons, and the
failure is silent and delayed — nobody finds out until a lookup stops
working weeks later. So it is said out loud at startup instead.
"""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def check_encryption_key(app_configs, **kwargs):
    if getattr(settings, "PANTRY_ENCRYPTION_KEY", ""):
        return []
    return [
        Warning(
            "PANTRY_ENCRYPTION_KEY is unset, so stored FoodData Central keys "
            "are encrypted with a key derived from SECRET_KEY.",
            hint=(
                "Rotating SECRET_KEY would invalidate every stored API key. "
                "Set CRUMBS_PANTRY_KEY to something of its own: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            ),
            id="pantry.W001",
        )
    ]
