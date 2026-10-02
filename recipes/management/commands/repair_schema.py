"""`manage.py repair_schema` — add columns a migration promised but never made.

For one specific situation, which `doctor` diagnoses: every migration is
recorded as applied, the models match the migration files, and yet the
tables are missing columns.

That happens when the migration files are regenerated from scratch after the
database has already recorded `0001_initial` as applied — deleting them and
re-running `makemigrations`, or extracting a release over a checkout whose
migrations had gone. The new `0001_initial` describes the current models, but
Django matches migrations by name, sees the old row in `django_migrations`,
and skips it. `migrate` then has nothing to do, for ever.

The repair is to bring the database up to the models directly, using the same
schema editor a migration would. Only columns Django can add safely are
touched — a NOT NULL column with no default cannot be added to a table with
rows in it, and this refuses rather than guessing a value.

Data is not touched. On SQLite, adding a column rebuilds the table and copies
the rows, which Django's schema editor does in a transaction.
"""

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from .doctor import OUR_APPS


class Command(BaseCommand):
    help = "Add columns that the migration history says exist but the tables lack."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Say what would change and stop.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        plan, refused = self.build_plan()

        if refused:
            self.stdout.write(self.style.ERROR("Cannot add these automatically:"))
            for model, field, why in refused:
                self.stdout.write(f"  {model._meta.db_table}.{field.column} — {why}")
            self.stdout.write(
                "  A column like that needs a migration that says what the "
                "existing rows should hold."
            )
            self.stdout.write("")

        if not plan:
            self.stdout.write(self.style.SUCCESS("Nothing to add — every column is there."))
            return

        for model, fields in plan.items():
            self.stdout.write(
                f"{model._meta.db_table}: {', '.join(field.column for field in fields)}"
            )

        if dry_run:
            self.stdout.write("")
            self.stdout.write("Dry run — nothing was changed.")
            return

        added = 0
        # One transaction: a half-repaired schema is worse than an unrepaired
        # one, because `doctor` would then report a shorter list and look
        # like progress.
        with transaction.atomic():
            with connection.schema_editor() as editor:
                for model, fields in plan.items():
                    for field in fields:
                        editor.add_field(model, field)
                        added += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Added {added} column(s)."))
        self.stdout.write("Run `manage.py doctor` to confirm, then carry on as normal.")

    def build_plan(self):
        """Which fields are missing, and which of them can safely be added."""
        plan = {}
        refused = []

        with connection.cursor() as cursor:
            tables = set(connection.introspection.table_names(cursor))

            for model in apps.get_models():
                if model._meta.app_label not in OUR_APPS:
                    continue
                table = model._meta.db_table
                if table not in tables:
                    # A missing table is a different problem: `migrate` makes
                    # tables, and this command is for the case where it won't.
                    continue

                present = {
                    column.name
                    for column in connection.introspection.get_table_description(
                        cursor, table
                    )
                }

                for field in model._meta.local_fields:
                    if not field.column or field.column in present:
                        continue
                    reason = self.unsafe_reason(field)
                    if reason:
                        refused.append((model, field, reason))
                    else:
                        plan.setdefault(model, []).append(field)

        return plan, refused

    @staticmethod
    def unsafe_reason(field):
        """Why a column cannot be bolted onto a table that already has rows."""
        if field.null:
            return None
        if field.has_default():
            return None
        if field.empty_strings_allowed and not field.null:
            # Django supplies "" as the effective default for text columns.
            return None
        return "NOT NULL with no default"
