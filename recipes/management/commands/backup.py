"""`manage.py backup` — everything you would be sad to lose, in one file.

An archive holding three things:

    MANIFEST.json   what this is, and what it came from
    data.json       every row, as `dumpdata` writes them
    media/          the photos, which the database only holds paths to

Media matters: a database dump on its own restores a recipe that points at
a picture that is not there any more.

**What is deliberately left out.** Sessions, permissions, content types and
admin log entries. Sessions are worthless by tomorrow; the other three are
rebuilt from the code on any install and including them is the classic way
to make `loaddata` fail on a content-type primary key that does not line up.

**What is deliberately left in, and is worth knowing about.** Password
hashes, and the encrypted FoodData Central keys. The hashes are hashes, but
they are still worth protecting. The API keys are encrypted with a key
derived from `PANTRY_ENCRYPTION_KEY`, or from `SECRET_KEY` when that is
unset — so restoring onto an install with a different key leaves them
unreadable. The manifest records which of the two was in use, and `restore`
says so rather than letting it be discovered weeks later.
"""

import json
import os
import tarfile
import tempfile
from io import StringIO
from pathlib import Path

import django
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

#: Rebuilt from the code, or worthless by morning. See the module docstring.
EXCLUDED = [
    "contenttypes",
    "auth.Permission",
    "sessions.Session",
    "admin.LogEntry",
]

#: Bumped when the archive's own layout changes, so a future `restore` can
#: tell "old backup" from "not a backup".
FORMAT = 1


def archive_name(when=None):
    when = when or timezone.now()
    return f"crumbs-backup-{when:%Y%m%d-%H%M%S}.tar.gz"


def build_manifest():
    return {
        "format": FORMAT,
        "created": timezone.now().isoformat(),
        "django": django.get_version(),
        "apps": sorted(
            config.label
            for config in apps.get_app_configs()
            if not config.name.startswith("django.")
        ),
        # Not the key itself — just which one the ciphertext is tied to.
        "encryption": (
            "PANTRY_ENCRYPTION_KEY"
            if getattr(settings, "PANTRY_ENCRYPTION_KEY", "")
            else "SECRET_KEY"
        ),
        "build": _build_stamp(),
        "counts": _counts(),
    }


def _build_stamp():
    stamp = Path(settings.BASE_DIR) / "BUILD.txt"
    return stamp.read_text().strip() if stamp.exists() else ""


def _counts():
    """Row counts, so a restore can say what it is about to do."""
    counts = {}
    for model in apps.get_models():
        label = model._meta.label
        if any(label.startswith(prefix) for prefix in ("django.", "contenttypes")):
            continue
        try:
            counts[label] = model._default_manager.count()
        except Exception:
            continue
    return counts


def write_archive(destination):
    """Write a backup to ``destination`` (a path). Returns the manifest."""
    manifest = build_manifest()

    data = StringIO()
    call_command(
        "dumpdata",
        *[f"--exclude={label}" for label in EXCLUDED],
        natural_foreign=True,
        natural_primary=True,
        indent=2,
        stdout=data,
    )
    payload = data.getvalue().encode("utf-8")

    media_root = Path(settings.MEDIA_ROOT)

    with tempfile.TemporaryDirectory() as scratch:
        scratch = Path(scratch)
        (scratch / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
        (scratch / "data.json").write_bytes(payload)

        with tarfile.open(destination, "w:gz") as archive:
            archive.add(scratch / "MANIFEST.json", arcname="MANIFEST.json")
            archive.add(scratch / "data.json", arcname="data.json")
            if media_root.exists():
                for path in sorted(media_root.rglob("*")):
                    if path.is_file():
                        archive.add(path, arcname=f"media/{path.relative_to(media_root)}")

    manifest["bytes"] = os.path.getsize(destination)
    return manifest


class Command(BaseCommand):
    help = "Write a backup archive: every row, plus the uploaded files."

    def add_arguments(self, parser):
        parser.add_argument(
            "--to",
            default=None,
            help="Where to write it. Defaults to a timestamped name in the project root.",
        )

    def handle(self, *args, **options):
        destination = Path(options["to"] or Path(settings.BASE_DIR) / archive_name())
        destination.parent.mkdir(parents=True, exist_ok=True)

        manifest = write_archive(destination)
        rows = sum(manifest["counts"].values())

        self.stdout.write(self.style.SUCCESS(f"Wrote {destination}"))
        self.stdout.write(f"  {rows} rows across {len(manifest['counts'])} tables")
        self.stdout.write(f"  {manifest['bytes'] // 1024} KB")
        if manifest["encryption"] == "SECRET_KEY":
            self.stdout.write(
                self.style.WARNING(
                    "  Stored API keys are tied to SECRET_KEY. Restoring onto an "
                    "install with a different SECRET_KEY will leave them unreadable."
                )
            )
