"""Backing up and putting it back."""

import json
import tarfile
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from recipes.management.commands.backup import EXCLUDED, build_manifest, write_archive
from recipes.management.commands.restore import check_manifest
from recipes.models import Recipe


class ArchiveTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        Recipe.objects.create(title="Dal", author=cls.cook, is_shared=True)
        Recipe.objects.create(title="Chilli", author=cls.cook)

    def archive(self):
        handle = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
        handle.close()
        manifest = write_archive(handle.name)
        return Path(handle.name), manifest

    def test_it_writes_a_manifest_and_a_data_file(self):
        path, _ = self.archive()
        with tarfile.open(path) as archive:
            names = archive.getnames()
        self.assertIn("MANIFEST.json", names)
        self.assertIn("data.json", names)

    def test_the_rows_are_in_there(self):
        path, _ = self.archive()
        with tarfile.open(path) as archive:
            data = json.loads(archive.extractfile("data.json").read())
        titles = [
            row["fields"]["title"] for row in data if row["model"] == "recipes.recipe"
        ]
        self.assertCountEqual(titles, ["Dal", "Chilli"])

    def test_sessions_and_permissions_are_left_out(self):
        """Worthless by morning, or rebuilt from the code — and the usual
        reason a restore fails on a content-type primary key."""
        path, _ = self.archive()
        with tarfile.open(path) as archive:
            data = json.loads(archive.extractfile("data.json").read())
        models = {row["model"] for row in data}
        for unwanted in ("sessions.session", "contenttypes.contenttype", "auth.permission"):
            self.assertNotIn(unwanted, models)

    def test_the_accounts_are_kept(self):
        """Without them every row in the backup points at a missing user."""
        path, _ = self.archive()
        with tarfile.open(path) as archive:
            data = json.loads(archive.extractfile("data.json").read())
        self.assertIn("auth.user", {row["model"] for row in data})

    def test_the_manifest_says_what_it_came_from(self):
        manifest = build_manifest()
        self.assertEqual(manifest["format"], 1)
        self.assertIn("recipes", manifest["apps"])
        self.assertIn("pantry", manifest["apps"])
        self.assertIn("recipes.Recipe", manifest["counts"])

    def test_the_manifest_records_which_key_the_secrets_are_tied_to(self):
        with self.settings(PANTRY_ENCRYPTION_KEY=""):
            self.assertEqual(build_manifest()["encryption"], "SECRET_KEY")
        with self.settings(PANTRY_ENCRYPTION_KEY="a-key-of-its-own"):
            self.assertEqual(build_manifest()["encryption"], "PANTRY_ENCRYPTION_KEY")

    @override_settings(MEDIA_ROOT=tempfile.mkdtemp())
    def test_uploaded_files_travel_with_the_data(self):
        """A database alone restores recipes pointing at missing pictures."""
        from django.conf import settings

        photo = Path(settings.MEDIA_ROOT) / "recipes" / "dal.jpg"
        photo.parent.mkdir(parents=True, exist_ok=True)
        photo.write_bytes(b"\xff\xd8pretend")

        path, _ = self.archive()
        with tarfile.open(path) as archive:
            self.assertIn("media/recipes/dal.jpg", archive.getnames())


class ManifestCheckTests(TestCase):
    def test_a_future_format_is_refused(self):
        with self.assertRaises(CommandError):
            check_manifest({"format": 99})

    def test_a_key_mismatch_warns_rather_than_refusing(self):
        """The rows still restore; the API keys just will not decrypt."""
        with self.settings(PANTRY_ENCRYPTION_KEY="mine"):
            warnings = check_manifest({"format": 1, "encryption": "SECRET_KEY"})
        self.assertTrue(warnings)
        self.assertIn("paste their key again", " ".join(warnings))

    def test_a_matching_key_is_quiet(self):
        with self.settings(PANTRY_ENCRYPTION_KEY=""):
            self.assertEqual(check_manifest({"format": 1, "encryption": "SECRET_KEY"}), [])


class RoundTripTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")

    def test_a_deleted_recipe_comes_back(self):
        Recipe.objects.create(title="Dal", author=self.cook)
        handle = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
        handle.close()
        write_archive(handle.name)

        Recipe.objects.all().delete()
        self.assertEqual(Recipe.objects.count(), 0)

        call_command("restore", handle.name, no_safety_backup=True, verbosity=0)
        self.assertEqual(Recipe.objects.get().title, "Dal")

    def test_merging_keeps_what_was_added_since(self):
        Recipe.objects.create(title="Dal", author=self.cook)
        handle = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
        handle.close()
        write_archive(handle.name)

        Recipe.objects.create(title="Added later", author=self.cook)
        call_command("restore", handle.name, no_safety_backup=True, verbosity=0)

        self.assertCountEqual(
            Recipe.objects.values_list("title", flat=True), ["Dal", "Added later"]
        )

    def test_a_file_that_is_not_a_backup_is_refused(self):
        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as handle:
            with tarfile.open(handle.name, "w:gz") as archive:
                info = tarfile.TarInfo("something.txt")
                info.size = 0
                archive.addfile(info)
            path = handle.name
        with self.assertRaises(CommandError):
            call_command("restore", path, no_safety_backup=True, verbosity=0)

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(CommandError):
            call_command("restore", "/nowhere/at/all.tar.gz", verbosity=0)


class HostileArchiveTests(TestCase):
    """A backup is a file somebody hands you."""

    def evil(self):
        handle = tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False)
        handle.close()
        import io

        with tarfile.open(handle.name, "w:gz") as archive:
            manifest = json.dumps({"format": 1, "encryption": "SECRET_KEY", "counts": {}})
            for name, body in (("MANIFEST.json", manifest), ("data.json", "[]")):
                payload = body.encode()
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            payload = b"pwned"
            info = tarfile.TarInfo("media/../../../../tmp/crumbs-escaped.txt")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        return handle.name

    def test_a_traversing_member_is_refused_and_nothing_escapes(self):
        escaped = Path("/tmp/crumbs-escaped.txt")
        escaped.unlink(missing_ok=True)

        with self.assertRaises(CommandError):
            call_command("restore", self.evil(), no_safety_backup=True, verbosity=0)

        self.assertFalse(escaped.exists())

    def test_the_database_is_untouched_when_the_archive_is_refused(self):
        """Media is staged before loaddata runs, so a refusal changes nothing."""
        cook = get_user_model().objects.create_user("cook", password="hunter2hunter2")
        Recipe.objects.create(title="Still here", author=cook)

        with self.assertRaises(CommandError):
            call_command("restore", self.evil(), no_safety_backup=True, verbosity=0)

        self.assertEqual(Recipe.objects.get().title, "Still here")


class ButtonTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.admin = User.objects.create_user(
            "admin", password="hunter2hunter2", is_staff=True
        )
        cls.cook = User.objects.create_user("cook", password="hunter2hunter2")

    def test_both_buttons_are_on_the_settings_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("recipes:settings"))
        self.assertContains(response, reverse("recipes:settings-backup"))
        self.assertContains(response, reverse("recipes:settings-restore"))

    def test_backing_up_hands_over_a_file(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("recipes:settings-backup"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("crumbs-backup-", response["Content-Disposition"])

    def test_backing_up_is_a_post(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("recipes:settings-backup")).status_code, 405)

    def test_only_staff_may_back_up_or_restore(self):
        self.client.force_login(self.cook)
        self.assertEqual(self.client.post(reverse("recipes:settings-backup")).status_code, 403)
        self.assertEqual(self.client.post(reverse("recipes:settings-restore")).status_code, 403)

    def test_restoring_without_typing_the_word_does_nothing(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("recipes:settings-restore"), {"confirm": "yes"})
        self.assertEqual(response.status_code, 302)
        from django.contrib.messages import get_messages

        self.assertIn(
            "Type RESTORE", " ".join(str(m) for m in get_messages(response.wsgi_request))
        )

    def test_restoring_without_a_file_says_so(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("recipes:settings-restore"), {"confirm": "RESTORE"}
        )
        from django.contrib.messages import get_messages

        self.assertIn(
            "Choose a backup file",
            " ".join(str(m) for m in get_messages(response.wsgi_request)),
        )

    def test_the_excluded_list_is_what_it_should_be(self):
        self.assertEqual(
            set(EXCLUDED),
            {"contenttypes", "auth.Permission", "sessions.Session", "admin.LogEntry"},
        )
