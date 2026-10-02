"""`manage.py doctor` — why the database and the code disagree.

Written after "no such column: pantry_foodentry.trans_fat_g" survived a
`make migrate`. When that happens the useful questions are all ones Django
will answer but never volunteers:

  * which database file is this process actually using — because migrating
    ./db.sqlite3 while the server reads /var/lib/crumbs/db.sqlite3 looks
    exactly like a migration that did nothing;
  * do migration files exist on disk for each app, and are they applied;
  * do the models have changes no migration describes;
  * and, decisively, which columns does each table actually have.

The last one is the answer. Everything else is how it got that way.
"""

from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection

#: Only this project's apps. Django's own are rarely the problem and their
#: noise buries the line that matters.
OUR_APPS = ("recipes", "pantry", "health", "nutrition")


class Command(BaseCommand):
    help = "Report on the database, the migrations, and any missing columns."

    def add_arguments(self, parser):
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Only report problems.",
        )

    def handle(self, *args, **options):
        self.quiet = options["quiet"]
        problems = []

        self.report_build()
        problems += self.report_modules()
        problems += self.report_database()

        migration_problems = self.report_migrations()
        drift_problems = self.report_drift()
        column_problems = self.report_columns()
        problems += migration_problems + drift_problems + column_problems

        self.stdout.write("")
        if not problems:
            self.stdout.write(self.style.SUCCESS("Database and code agree."))
            return

        self.stdout.write(self.style.ERROR(f"{len(problems)} problem(s):"))
        for problem in problems:
            self.stdout.write(f"  - {problem}")
        self.stdout.write("")

        # Columns missing while everything is applied and nothing has drifted
        # is its own diagnosis, and `migrate` is the wrong advice for it: from
        # Django's side there is nothing left to do.
        stuck = column_problems and not migration_problems and not drift_problems
        if stuck:
            self.stdout.write(
                self.style.WARNING(
                    "Every migration is applied and no model has drifted, yet "
                    "columns are missing."
                )
            )
            self.stdout.write(
                "  That means 0001_initial was regenerated after the database "
                "had already\n"
                "  recorded it as applied, so `migrate` skips it and always "
                "will.\n"
            )
            self.stdout.write("  Fix it with:  make repair")
        else:
            self.stdout.write("Most of these are fixed by: make migrate")

    def report_build(self):
        """Which build of the code this is.

        Added after the same two system-check errors were reported twice: the
        fix was in the release, but there was no way to tell which release
        was unpacked. Compare this against whatever the release said.
        """
        self.heading("Build")
        stamp = Path(settings.BASE_DIR) / "BUILD.txt"
        if stamp.exists():
            for line in stamp.read_text().strip().splitlines():
                self.say(line)
        else:
            self.say("no BUILD.txt — this checkout predates build stamps,")
            self.say("or it is a git working copy rather than an unpacked release")

        # Decisive when a release "did not take": the app Django imported may
        # not be the directory you unpacked into. A second copy anywhere
        # earlier on sys.path wins, and nothing says so.
        self.say("")
        self.say("loaded from:")
        for app_label in OUR_APPS:
            try:
                config = apps.get_app_config(app_label)
            except LookupError:
                self.say(f"  {app_label:10} NOT INSTALLED")
                continue
            self.say(f"  {app_label:10} {config.path}")

        duplicates = self.find_duplicate_trees()
        if duplicates:
            self.say("")
            self.say("More than one copy of an app is on disk:")
            for label, paths in duplicates.items():
                for path in paths:
                    self.say(f"  {label:10} {path}")
            self.say("Django uses the first on sys.path; the others are ignored.")

    def find_duplicate_trees(self):
        """Other copies of our apps sitting under BASE_DIR.

        Extracting a release into the project root rather than over it is the
        usual cause — you end up with both the old tree and a nested new one,
        and the old one is the one that runs.
        """
        root = Path(settings.BASE_DIR)
        found = {}
        for app_label in OUR_APPS:
            try:
                live = Path(apps.get_app_config(app_label).path).resolve()
            except LookupError:
                continue
            others = [
                candidate.parent.resolve()
                for candidate in root.rglob(f"{app_label}/apps.py")
                if candidate.parent.resolve() != live
                and ".venv" not in candidate.parts
                and "node_modules" not in candidate.parts
            ]
            if others:
                found[app_label] = others
        return found

    def report_modules(self):
        """Where each app is actually being imported from.

        Added after a fix that was verifiably in the release kept not
        appearing. Django imports by package name off sys.path, which need
        not be the directory you unpacked into — and this project's release
        archive has a top-level `crumbs/` directory with the same name as
        the settings package, so unpacking it *inside* the project puts a
        whole second copy at crumbs/nutrition, crumbs/recipes and so on.

        If a path below is not where you think you edited, that is the bug,
        and no amount of re-extracting over the wrong tree will fix it.
        """
        import importlib
        import sys

        self.heading("Where the code is")
        problems = []

        for label in OUR_APPS:
            try:
                module = importlib.import_module(label)
            except Exception as error:
                problems.append(f"Could not import {label}: {error}")
                continue

            folder = Path(module.__file__).parent
            self.say(f"{label:10} {folder}")

            # The same package sitting on sys.path twice: whichever comes
            # first wins, silently, for ever.
            copies = [
                Path(entry).resolve() / label
                for entry in sys.path
                if entry and (Path(entry) / label / "__init__.py").exists()
            ]
            unique = sorted({str(copy) for copy in copies})
            if len(unique) > 1:
                problems.append(
                    f"{label} exists in more than one place on sys.path: "
                    + "; ".join(unique)
                )

        return problems

    # --- Where the data actually is -----------------------------------------

    def heading(self, text):
        if not self.quiet:
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{text}"))

    def say(self, text):
        if not self.quiet:
            self.stdout.write(f"  {text}")

    def report_database(self):
        self.heading("Database")
        name = settings.DATABASES["default"]["NAME"]
        path = Path(name).resolve()

        self.say(f"settings module  {settings.SETTINGS_MODULE}")
        self.say(f"BASE_DIR         {settings.BASE_DIR}")
        self.say(f"file             {path}")

        if not path.exists():
            self.say("exists           no")
            return [f"The database file does not exist: {path}"]

        self.say(f"exists           yes ({path.stat().st_size // 1024} KB)")
        # The commonest cause of a migration that "did nothing": the shell
        # that ran it had a different CRUMBS_DB_PATH from the one serving.
        self.say("If this is not the file your server uses, check CRUMBS_DB_PATH")
        self.say("in the environment of *both* the migrate and the runserver.")
        return []

    # --- Migration files, and whether they ran -------------------------------

    def report_migrations(self):
        from django.db.migrations.loader import MigrationLoader

        self.heading("Migrations")
        problems = []
        try:
            loader = MigrationLoader(connection, ignore_no_migrations=True)
        except Exception as error:
            return [f"Could not read the migration graph: {error}"]

        applied = {(app, name) for app, name in loader.applied_migrations}
        for app_label in OUR_APPS:
            on_disk = sorted(
                name for app, name in loader.disk_migrations if app == app_label
            )
            unapplied = [
                name for name in on_disk if (app_label, name) not in applied
            ]

            if not on_disk:
                self.say(f"{app_label:9} no migration files at all")
                problems.append(
                    f"{app_label} has no migrations. Run `make migrate` to generate them."
                )
            elif unapplied:
                self.say(f"{app_label:9} {len(on_disk)} on disk, {len(unapplied)} NOT applied")
                problems.append(
                    f"{app_label} has unapplied migrations: {', '.join(unapplied)}"
                )
            else:
                self.say(f"{app_label:9} {len(on_disk)} on disk, all applied")
        return problems

    # --- Models that have moved on ------------------------------------------

    def report_drift(self):
        from django.db.migrations.autodetector import MigrationAutodetector
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.questioner import NonInteractiveMigrationQuestioner
        from django.db.migrations.state import ProjectState

        self.heading("Models against migrations")
        try:
            loader = MigrationLoader(None, ignore_no_migrations=True)
            changes = MigrationAutodetector(
                loader.project_state(),
                ProjectState.from_apps(apps),
                NonInteractiveMigrationQuestioner(specified_apps=set(), dry_run=True),
            ).changes(graph=loader.graph)
        except Exception as error:
            return [f"Could not compare models to migrations: {error}"]

        ours = {app: ops for app, ops in changes.items() if app in OUR_APPS}
        if not ours:
            self.say("every model change has a migration")
            return []

        for app_label in sorted(ours):
            self.say(f"{app_label:9} has changes with no migration")
        return [
            "These apps have model changes no migration describes: "
            + ", ".join(sorted(ours))
        ]

    # --- The decisive one ----------------------------------------------------

    def report_columns(self):
        self.heading("Tables")
        problems = []

        try:
            with connection.cursor() as cursor:
                tables = set(connection.introspection.table_names(cursor))

                for model in apps.get_models():
                    if model._meta.app_label not in OUR_APPS:
                        continue
                    table = model._meta.db_table

                    if table not in tables:
                        self.say(f"{table:34} MISSING")
                        problems.append(f"Table {table} does not exist.")
                        continue

                    present = {
                        column.name
                        for column in connection.introspection.get_table_description(
                            cursor, table
                        )
                    }
                    expected = {
                        field.column
                        for field in model._meta.local_fields
                        if field.column
                    }
                    missing = sorted(expected - present)

                    if missing:
                        self.say(f"{table:34} missing {len(missing)}: {', '.join(missing)}")
                        problems.append(
                            f"{table} is missing column(s): {', '.join(missing)}"
                        )
                    elif not self.quiet:
                        self.say(f"{table:34} {len(present)} columns, all present")
        except DatabaseError as error:
            return [f"Could not read the database: {error}"]

        return problems
