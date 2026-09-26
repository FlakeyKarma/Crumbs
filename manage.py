#!/usr/bin/env python
"""Command-line entry point for Crumbs."""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "crumbs.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django is not importable. Activate your virtualenv and run "
            "`pip install -r requirements.txt`."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
