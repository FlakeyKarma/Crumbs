"""`manage.py restore` — put a backup back.

The dangerous half, so it is built to be hard to regret:

* **A safety backup is written first**, always, before anything is touched.
  Restoring the wrong archive is a mistake people make at exactly the moment
  they are least able to absorb another one.
* **Merge is the default.** Rows in the archive are written over rows with
  the same primary key, and anything added since stays. `--replace` empties
  the tables first, which is what you want after losing a disk and not what
  you want after a bad afternoon.
* **The archive is extracted with `filter="data"`.** A tar can name its
  members `../../etc/something`, and an archive is a file someone handed
  you. Media paths are checked against MEDIA_ROOT as well, because being
  wrong about this writes files anywhere the server can reach.
"""

import json
import tarfile
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from .backup import FORMAT, archive_name, write_archive


def read_manifest(archive):
    try:
        member = archive.extractfile("MANIFEST.json")
    except KeyError:
        raise CommandError("That file has no MANIFEST.json — it is not a Crumbs backup.")
    if member is None:
        raise CommandError("That archive's manifest could not be read.")
    try:
        return json.loads(member.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise CommandError("That archive's manifest is not readable JSON.")


def check_manifest(manifest):
    """Refuse what cannot work; return the warnings worth printing."""
    if manifest.get("format") != FORMAT:
        raise CommandError(
            f"That backup is format {manifest.get('format')}, and this version "
            f"reads format {FORMAT}."
        )

    warnings = []
    current = (
        "PANTRY_ENCRYPTION_KEY"
        if getattr(settings, "PANTRY_ENCRYPTION_KEY", "")
        else "SECRET_KEY"
    )
    if manifest.get("encryption") != current:
        warnings.append(
            f"The backup's API keys were encrypted against {manifest.get('encryption')} "
            f"and this install uses {current}. They will restore but not decrypt; "
            "each person will need to paste their key again."
        )
    return warnings


def stage_media(archive, members, scratch):
    """Unpack media/ into a scratch directory, before anything else happens.

    Deliberately first. `filter="data"` aborts the whole extraction when a
    member tries to escape, and doing that *after* `loaddata` would leave a
    database loaded from an archive we then refused — so the archive proves
    itself harmless before the database is touched.
    """
    try:
        archive.extractall(scratch, members=members, filter="data")
    except tarfile.TarError as error:
        raise CommandError(
            f"That archive tried to write outside its own folder: {error}. "
            "Nothing was changed."
        )
    except Exception as error:
        raise CommandError(f"That archive's media could not be read: {error}")


def install_media(scratch):
    """Copy the staged files into MEDIA_ROOT, and nowhere else."""
    media_root = Path(settings.MEDIA_ROOT).resolve()
    media_root.mkdir(parents=True, exist_ok=True)

    source = Path(scratch) / "media"
    if not source.exists():
        return 0

    restored = 0
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        target = (media_root / path.relative_to(source)).resolve()
        # Belt and braces behind filter="data": this is the only place in
        # the project that writes files from somebody else's input.
        if not target.is_relative_to(media_root):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        restored += 1
    return restored


class Command(BaseCommand):
    help = "Restore from a backup archive written by `manage.py backup`."

    def add_arguments(self, parser):
        parser.add_argument("archive", help="Path to a crumbs-backup-*.tar.gz")
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Empty the tables first. Without this, the archive is merged in.",
        )
        parser.add_argument(
            "--no-safety-backup",
            action="store_true",
            help="Skip the backup taken before restoring. Rarely a good idea.",
        )

    def handle(self, *args, **options):
        source = Path(options["archive"])
        if not source.exists():
            raise CommandError(f"No such file: {source}")

        with tarfile.open(source, "r:gz") as archive:
            manifest = read_manifest(archive)
            warnings = check_manifest(manifest)

            self.stdout.write(f"Backup taken {manifest.get('created', 'at an unknown time')}")
            self.stdout.write(
                f"  {sum(manifest.get('counts', {}).values())} rows, "
                f"{len(manifest.get('counts', {}))} tables"
            )
            for warning in warnings:
                self.stdout.write(self.style.WARNING(f"  {warning}"))

            if not options["no_safety_backup"]:
                safety = Path(settings.BASE_DIR) / f"before-restore-{archive_name()}"
                write_archive(safety)
                self.stdout.write(f"  Current state saved to {safety}")

            data = archive.extractfile("data.json")
            if data is None:
                raise CommandError("That archive has no data.json.")
            payload = data.read()

            media_members = [
                member
                for member in archive.getmembers()
                if member.name.startswith("media/")
            ]

            with tempfile.TemporaryDirectory() as scratch:
                # Media first: a hostile archive is refused here, with the
                # database still untouched.
                stage_media(archive, media_members, scratch)

                with tempfile.NamedTemporaryFile(
                    suffix=".json", delete=False, mode="wb"
                ) as handle:
                    handle.write(payload)
                    staged = handle.name

                try:
                    with transaction.atomic():
                        if options["replace"]:
                            # Takes sessions with it, so whoever is doing
                            # this gets logged out. Better than a
                            # half-replaced database.
                            call_command("flush", "--noinput", verbosity=0)
                        call_command("loaddata", staged, verbosity=0)
                finally:
                    Path(staged).unlink(missing_ok=True)

                restored = install_media(scratch)

        self.stdout.write(self.style.SUCCESS("Restored."))
        self.stdout.write(f"  {restored} media file(s)")
        if options["replace"]:
            self.stdout.write("  Tables were emptied first; you will need to sign in again.")
        else:
            self.stdout.write("  Merged — anything added since the backup is still here.")
