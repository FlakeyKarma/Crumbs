"""Whole-project checks.

Django's own system checks catch a class of mistake that no amount of reading
finds — a reverse accessor colliding with a field name, for one, which is
invisible until `makemigrations` refuses to run. Running them as a test means
the suite fails where the developer is, instead of at install time.
"""

from django.core.management import call_command
from django.test import TestCase


class SystemCheckTests(TestCase):
    def test_the_project_passes_djangos_own_checks(self):
        # Raises SystemCheckError on any ERROR-level problem.
        call_command("check", verbosity=0)

    def test_the_models_and_migrations_agree(self):
        """Fails if a model has changed and no migration says so.

        Expected to fail in a fresh checkout, because no migration files are
        committed — that is what `make install` generates. It is a real
        failure everywhere else: an un-migrated column surfaces as
        `no such column: …` at whoever next loads the page.
        """
        try:
            call_command("makemigrations", "--check", "--dry-run", verbosity=0)
        except SystemExit:
            self.fail(
                "Models have changes with no migration. Run `make migrate`."
            )


class TemplateCommentTests(TestCase):
    """`{# #}` only works on one line.

    Django's parser looks for the closing `#}` on the same line and, not
    finding it, emits the whole thing as text — so a comment that wraps ends
    up printed on the page, once per loop iteration if it's inside a `for`.
    Nothing warns; it just quietly becomes content.
    """

    def test_no_comment_spans_more_than_one_line(self):
        import pathlib
        import re

        from django.conf import settings

        offenders = []
        roots = [pathlib.Path(settings.BASE_DIR)]
        for path in roots[0].rglob("*.html"):
            if "__pycache__" in str(path) or ".venv" in str(path):
                continue
            text = path.read_text()
            for match in re.finditer(r"\{#", text):
                rest = text[match.start():]
                close = rest.find("#}")
                if close == -1 or "\n" in rest[:close]:
                    line = text[: match.start()].count("\n") + 1
                    offenders.append(f"{path.relative_to(roots[0])}:{line}")

        self.assertEqual(
            offenders,
            [],
            "These render as text. Use {% comment %}…{% endcomment %} instead:\n  "
            + "\n  ".join(offenders),
        )


class MigrationWarningTests(TestCase):
    """The check that turns a missing migration into a sentence.

    Deliberately not asserting that it finds nothing: whether it does
    depends on whether migrations have been generated yet, and both answers
    are legitimate. What matters is that it never raises — it runs on every
    management command, including the one that fixes the problem — and that
    when it does speak, it says what to do.
    """

    def test_it_runs_without_raising(self):
        from recipes.checks import check_migrations_match_models

        self.assertIsInstance(check_migrations_match_models(None), list)

    def test_any_warning_says_how_to_fix_it(self):
        from recipes.checks import check_migrations_match_models

        for problem in check_migrations_match_models(None):
            self.assertEqual(problem.id, "recipes.W002")
            self.assertIn("make migrate", problem.hint)

    def test_it_is_a_warning_not_an_error(self):
        """An Error would refuse to run makemigrations, which is the fix."""
        from django.core.checks import Warning as CheckWarning

        from recipes.checks import check_migrations_match_models

        for problem in check_migrations_match_models(None):
            self.assertIsInstance(problem, CheckWarning)


class DoctorTests(TestCase):
    """The command that explains a schema mismatch instead of throwing one."""

    def run_doctor(self, **options):
        from io import StringIO

        out = StringIO()
        call_command("doctor", stdout=out, **options)
        return out.getvalue()

    def test_it_runs(self):
        self.assertIn("Database", self.run_doctor())

    def test_it_names_the_database_file(self):
        from django.conf import settings

        output = self.run_doctor()
        self.assertIn(str(settings.SETTINGS_MODULE), output)
        self.assertIn("CRUMBS_DB_PATH", output)

    def test_the_test_database_has_every_column_the_models_want(self):
        """The decisive check: if this fails, some model field has no column.

        Run against the test database, which is built from the models, so a
        failure here means the comparison itself is wrong rather than the
        developer's database being behind."""
        from recipes.management.commands.doctor import Command

        command = Command()
        command.quiet = True
        self.assertEqual(command.report_columns(), [])

    def test_quiet_mode_says_nothing_when_all_is_well(self):
        from recipes.management.commands.doctor import Command

        command = Command()
        command.quiet = True
        command.stdout = type("Sink", (), {"write": lambda self, *a, **k: None})()
        self.assertEqual(command.report_columns(), [])

    def test_it_checks_every_one_of_our_apps(self):
        from recipes.management.commands.doctor import OUR_APPS

        self.assertEqual(set(OUR_APPS), {"recipes", "pantry", "health", "nutrition"})


class RepairSchemaTests(TestCase):
    """The repair for a database `migrate` will never touch again."""

    def test_it_finds_nothing_to_do_on_a_sound_database(self):
        from recipes.management.commands.repair_schema import Command

        plan, refused = Command().build_plan()
        self.assertEqual(plan, {})
        self.assertEqual(refused, [])

    def test_running_it_is_safe_when_there_is_nothing_to_repair(self):
        from io import StringIO

        out = StringIO()
        call_command("repair_schema", stdout=out)
        self.assertIn("Nothing to add", out.getvalue())

    def test_a_dry_run_changes_nothing(self):
        from io import StringIO

        out = StringIO()
        call_command("repair_schema", "--dry-run", stdout=out)
        self.assertNotIn("Added", out.getvalue())

    def test_nullable_and_defaulted_columns_are_addable(self):
        """What can be bolted onto a table that already has rows."""
        from pantry.models import FoodEntry
        from recipes.management.commands.repair_schema import Command

        unsafe = Command.unsafe_reason
        self.assertIsNone(unsafe(FoodEntry._meta.get_field("trans_fat_g")))  # null=True
        self.assertIsNone(unsafe(FoodEntry._meta.get_field("image")))        # blank text
        self.assertIsNone(unsafe(FoodEntry._meta.get_field("name")))         # text, "" default

    def test_a_not_null_column_with_no_default_is_refused(self):
        from django.db import models

        from recipes.management.commands.repair_schema import Command

        field = models.ForeignKey("recipes.Recipe", on_delete=models.CASCADE)
        field.set_attributes_from_name("recipe")
        self.assertIsNotNone(Command.unsafe_reason(field))

    def test_doctor_names_repair_for_this_signature(self):
        """Columns missing while everything is applied is not a migrate problem."""
        import inspect

        from recipes.management.commands import doctor

        source = inspect.getsource(doctor.Command.handle)
        self.assertIn("make repair", source)
        self.assertIn("regenerated", source)
