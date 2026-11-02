import importlib.util
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout


spec = importlib.util.spec_from_file_location(
    "release_checks", Path(__file__).resolve().parents[1] / "scripts/check_release.py")
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class ReleaseChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "public"
        for relative in checks.REQUIRED:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Synthetic release content\n")
        (self.root / "pyproject.toml").write_text(
            '[project]\nname="retulo"\n[project.scripts]\nretulo="retulo.cli:main"\n')

    def codes(self, findings):
        return {item["code"] for item in findings}

    def wheel(self, extras=None):
        path = self.base / "retulo-0.3.0-py3-none-any.whl"
        payload = {name: b"{}" if name.endswith(".json") else b"" for name in checks.WHEEL_REQUIRED}
        payload.update({"retulo-0.3.0.dist-info/METADATA": b"Name: retulo\nVersion: 0.3.0\n",
                        "retulo-0.3.0.dist-info/licenses/LICENSE": b"Synthetic license\n"})
        payload.update(extras or {})
        with zipfile.ZipFile(path, "w") as archive:
            for name, raw in payload.items():
                archive.writestr(name, raw)
        return path

    def license_inventory(self):
        names = ("third_party/licenses/build/_dist_info/LICENSE",
                 "third_party/licenses/build/_vendor/packaging-25.0.dist-info/licenses/LICENSE")
        licenses = []
        for index, name in enumerate(names):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = f"Synthetic license {index}\n".encode()
            path.write_bytes(raw)
            licenses.append({"path": name, "sha256": hashlib.sha256(raw).hexdigest()})
        packages = [{"name": "build", "version": "1.3.0", "license_files": licenses}]
        inventory = self.root / "third_party/inventory.json"
        inventory.write_text(json.dumps(packages))
        return inventory, packages

    def test_complete_synthetic_source_and_license_contacts_pass(self):
        (self.root / "LICENSE").write_text("Copyright Example Contributor <author@example.org>\n")
        (self.root / "README.md").write_text("Use /home/example/project or ~/project.\n")
        findings, count = checks.check_source(self.root)
        self.assertEqual(findings, [])
        self.assertEqual(count, len(checks.REQUIRED))

    def test_possible_credentials_paths_and_legacy_names_are_redacted(self):
        secret = "sk-" + "SyntheticSecretValueForScannerOnly12345"
        old_name = "workspace" + "_engine"
        home = "/Users/" + "actual-person/private-project"
        (self.root / "README.md").write_text(secret + "\n" + home + "\n" + old_name)
        findings, _ = checks.check_source(self.root)
        self.assertEqual(self.codes(findings), {"credential_pattern", "personal_home_path", "legacy_branding"})
        self.assertNotIn(secret, json.dumps(findings))
        self.assertNotIn("actual-person", json.dumps(findings))
        self.assertEqual({item["line"] for item in findings}, {1, 2, 3})

    def test_migration_exception_only_applies_to_branding(self):
        (self.root / "docs").mkdir(exist_ok=True)
        (self.root / "docs/MIGRATION.md").write_text(
            "workspace" + "-engine\n" + "/home/" + "private-owner/project\n")
        findings, _ = checks.check_source(self.root)
        self.assertEqual(self.codes(findings), {"personal_home_path"})

    def test_private_names_are_flagged_without_scanning_runtime_contents(self):
        (self.root / "runtime").mkdir()
        (self.root / "runtime/secret.txt").write_text("sk-" + "X" * 40)
        (self.root / "auth.json").write_text("{}")
        (self.root / ".env").write_text("SYNTHETIC=1")
        (self.root / "sample.sqlite").write_bytes(b"not a real database")
        findings, _ = checks.check_source(self.root)
        self.assertEqual(self.codes(findings), {"private_directory", "private_filename"})
        self.assertEqual(len(findings), 4)

    def test_virtualenv_build_and_git_data_are_not_scanned(self):
        for folder in (".venv", ".git", "dist", "build", "retulo.egg-info"):
            (self.root / folder).mkdir()
            (self.root / folder / "ignored.txt").write_text("sk-" + "X" * 40)
        findings, count = checks.check_source(self.root)
        self.assertEqual(findings, [])
        self.assertEqual(count, len(checks.REQUIRED))

    def test_nested_build_and_dist_licenses_are_scanned(self):
        licenses = [self.root / "third_party/licenses" / name / "LICENSE"
                    for name in ("build", "dist")]
        for path in licenses:
            path.parent.mkdir(parents=True)
            path.write_text("Synthetic dependency license\n")
        findings, count = checks.check_source(self.root)
        self.assertEqual(findings, [])
        self.assertEqual(count, len(checks.REQUIRED) + 2)
        # The directories are traversed, rather than merely exempted from all checks.
        for path in licenses:
            path.write_text("sk-" + "SyntheticSecretValueForScannerOnly12345")
        findings, _ = checks.check_source(self.root)
        self.assertEqual(self.codes(findings), {"credential_pattern"})
        self.assertEqual({item["path"] for item in findings},
                         {str(path.relative_to(self.root)) for path in licenses})

    def test_source_symlink_is_not_followed(self):
        private = self.base / "private.txt"
        private.write_text("sk-" + "X" * 40)
        (self.root / "linked.txt").symlink_to(private)
        findings, _ = checks.check_source(self.root)
        self.assertEqual(self.codes(findings), {"symlink"})

    def test_wheel_requires_runtime_schema_and_license(self):
        path = self.wheel()
        findings, members = checks.check_archive(path)
        self.assertEqual(findings, [])
        self.assertEqual(members, len(checks.WHEEL_REQUIRED) + 2)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("retulo/__init__.py", "")
            archive.writestr("retulo-0.3.0.dist-info/METADATA", "Name: other\n")
        findings, _ = checks.check_archive(path)
        self.assertTrue({"missing_archive_file", "missing_license", "package_identity"} <= self.codes(findings))

    def test_new_runtime_contracts_cannot_be_omitted_from_wheel(self):
        for required in ("retulo/workflow_engine.py", "retulo/schemas/task-v2.schema.json",
                         "retulo/schemas/workflow.schema.json", "retulo/schemas/recipe.schema.json"):
            with self.subTest(required=required):
                path = self.wheel()
                with zipfile.ZipFile(path) as archive:
                    payload = {name: archive.read(name) for name in archive.namelist() if name != required}
                with zipfile.ZipFile(path, "w") as archive:
                    for name, raw in payload.items():
                        archive.writestr(name, raw)
                findings, _ = checks.check_archive(path)
                self.assertIn({"path": required, "code": "missing_archive_file",
                               "message": "Required distribution file is absent"}, findings)

    def test_archive_traversal_private_payload_and_links_are_rejected(self):
        path = self.wheel({"../outside.txt": b"synthetic", ".venv/auth.json": b"{}"})
        with zipfile.ZipFile(path, "a") as archive:
            link = zipfile.ZipInfo("retulo/link")
            link.external_attr = (0o120777 << 16)
            archive.writestr(link, "outside")
        findings, _ = checks.check_archive(path)
        self.assertTrue({"unsafe_archive_path", "private_archive_member", "archive_link"} <= self.codes(findings))

    def test_source_distribution_prefix_and_install_templates(self):
        path = self.base / "retulo-0.3.0.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            for name in checks.SOURCE_ARCHIVE_REQUIRED:
                raw = (self.root / name).read_bytes()
                info = tarfile.TarInfo("retulo-0.3.0/" + name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
        findings, members = checks.check_archive(path)
        self.assertEqual(findings, [])
        self.assertEqual(members, len(checks.SOURCE_ARCHIVE_REQUIRED))

    def test_source_distribution_build_license_is_not_generated_output(self):
        path = self.base / "retulo-0.3.0.tar.gz"
        payload = {name: (self.root / name).read_bytes() for name in checks.SOURCE_ARCHIVE_REQUIRED}
        payload.update({"third_party/licenses/build/LICENSE": b"Synthetic build license\n",
                        "third_party/licenses/dist/LICENSE": b"Synthetic dist license\n"})

        def write_archive():
            with tarfile.open(path, "w:gz") as archive:
                for name, raw in payload.items():
                    info = tarfile.TarInfo("retulo-0.3.0/" + name)
                    info.size = len(raw)
                    archive.addfile(info, io.BytesIO(raw))

        write_archive()
        findings, members = checks.check_archive(path)
        self.assertEqual(findings, [])
        self.assertEqual(members, len(checks.SOURCE_ARCHIVE_REQUIRED) + 2)
        payload.update({"build/generated.txt": b"Synthetic output",
                        "dist/generated.txt": b"Synthetic output"})
        write_archive()
        findings, _ = checks.check_archive(path)
        self.assertEqual(self.codes(findings), {"private_archive_member"})
        self.assertEqual({item["path"] for item in findings},
                         {"build/generated.txt", "dist/generated.txt"})

    def test_nested_private_archive_directories_remain_rejected(self):
        names = ("third_party/.git/config", "third_party/.venv/config.txt",
                 "third_party/__pycache__/cache.txt", "third_party/runtime/data.txt")
        path = self.wheel({name: b"Synthetic content" for name in names})
        findings, _ = checks.check_archive(path)
        self.assertEqual(self.codes(findings), {"private_archive_member"})
        self.assertEqual({item["path"] for item in findings}, set(names))

    def test_inventory_preserves_distinct_nested_vendor_license_bytes(self):
        self.license_inventory()
        findings, _ = checks.check_source(self.root)
        self.assertEqual(findings, [])
        path = self.base / "retulo-0.3.0.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            archive.add(self.root, arcname="retulo-0.3.0")
        findings, _ = checks.check_archive(path)
        self.assertEqual(findings, [])

    def test_inventory_rejects_flattened_duplicate_license_paths(self):
        inventory, packages = self.license_inventory()
        packages[0]["license_files"].append(dict(packages[0]["license_files"][0]))
        inventory.write_text(json.dumps(packages))
        findings, _ = checks.check_source(self.root)
        self.assertEqual(self.codes(findings), {"duplicate_license_path"})

    def test_inventory_rejects_overwritten_license_bytes(self):
        _, packages = self.license_inventory()
        item = packages[0]["license_files"][0]
        (self.root / item["path"]).write_text("Other vendored package license\n")
        findings, _ = checks.check_source(self.root)
        self.assertEqual(self.codes(findings), {"license_hash_mismatch"})
        self.assertEqual(findings[0]["path"], item["path"])
        path = self.base / "retulo-0.3.0.tar.gz"
        with tarfile.open(path, "w:gz") as archive:
            archive.add(self.root, arcname="retulo-0.3.0")
        findings, _ = checks.check_archive(path)
        self.assertEqual(self.codes(findings), {"license_hash_mismatch"})

    def test_inventory_structure_and_relative_path_boundary(self):
        inventory, _ = self.license_inventory()
        record = {"name": "example", "version": "1.0", "license_files": []}
        for name, code in (("../outside/LICENSE", "invalid_license_path"),
                           ("third_party/licenses/../../outside", "invalid_license_path"),
                           ("third_party//licenses/example/LICENSE", "invalid_license_path"),
                           ("LICENSE", "invalid_license_path"),
                           ("third_party/licenses/example/MISSING", "missing_license_file")):
            with self.subTest(path=name):
                record["license_files"] = [{"path": name, "sha256": "0" * 64}]
                inventory.write_text(json.dumps([record]))
                findings, _ = checks.check_source(self.root)
                self.assertEqual(self.codes(findings), {code})
        for value in ({"packages": []}, [{"license_files": []}],
                      [{"name": "example", "version": "1.0", "license_files": [{"path": "LICENSE", "sha256": "invalid"}]}]):
            with self.subTest(structure=value):
                inventory.write_text(json.dumps(value))
                findings, _ = checks.check_source(self.root)
                self.assertEqual(self.codes(findings), {"inventory_structure"})

    def test_cli_fails_when_both_distributions_are_not_present(self):
        (self.root / "dist").mkdir()
        output = io.StringIO()
        with redirect_stdout(output):
            status = checks.main(["--root", str(self.root), "--artifacts-dir", str(self.root / "dist")])
        result = json.loads(output.getvalue())
        self.assertEqual(status, 1)
        self.assertFalse(result["ok"])
        self.assertIn("missing_distribution", self.codes(result["findings"]))


if __name__ == "__main__":
    unittest.main()
