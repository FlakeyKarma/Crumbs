"""Nutrition sources, one module each, behind a registry.

Importing this package registers every source. ``base`` defines the contract,
``units`` does the grams arithmetic that has nothing to do with any source.
"""

from .base import NormalizedFood, SourceError, SourceModule, all_modules, get_module  # noqa: F401
from . import fdc, off  # noqa: F401  (imported for the side effect of registering)
