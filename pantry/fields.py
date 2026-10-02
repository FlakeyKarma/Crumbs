"""An encrypted text field, for the one secret this app stores.

A user's FoodData Central key is theirs, not ours, and it sits in a table an
admin can read. `django-fernet-fields` is the usual answer and is unmaintained
against current Django, so this is a small hand-rolled wrapper over the
`cryptography` package's Fernet.

The key comes from settings.PANTRY_ENCRYPTION_KEY, falling back to a key
derived from SECRET_KEY. That fallback means rotating SECRET_KEY invalidates
every stored API key — recoverable (the user pastes it again) but worth
knowing, which is why `manage.py check` warns when it is in use.
"""

import base64
import hashlib

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise ImproperlyConfigured(
            "Storing an API key needs the `cryptography` package. "
            "It is in requirements.txt."
        ) from exc

    raw = getattr(settings, "PANTRY_ENCRYPTION_KEY", "") or settings.SECRET_KEY
    # Fernet wants 32 url-safe base64 bytes; a SECRET_KEY is neither length
    # nor alphabet, so derive rather than demand a particular format.
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


class EncryptedTextField(models.TextField):
    """Ciphertext in the column, plaintext in Python.

    Not searchable and not indexable, by construction — every row encrypts to
    different bytes. That is fine for a credential looked up by its owner.
    """

    def get_prep_value(self, value):
        if value in (None, ""):
            return value
        return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")

    def from_db_value(self, value, expression, connection):
        if value in (None, ""):
            return value
        try:
            return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
        except Exception:
            # A rotated SECRET_KEY, or a row written before encryption was on.
            # Returning "" rather than raising keeps the settings page usable
            # so the owner can simply paste the key again.
            return ""
