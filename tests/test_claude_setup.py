"""Project integration tests use only synthetic directories and local Python."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import venv

from retulo.integrations import claude


class ClaudeSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        self.options = dict(project=self.project, engine_config=self.root / "engine.json",
                            workspace_home=self.root / "workspaces", snapshot_home=self.root / "snapshots")
        self.mcp = self.project / ".mcp.json"
        self.guidance = self.project / "CLAUDE.md"

    def observed(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_preview_native_server_and_preservation_of_existing_project(self):
        initial = {"mcpServers": {"other": {"type": "stdio", "command": "other", "env": {"A": "B"}}},
                   "custom": {"futureSetting": [1, 2, False]}}
        self.mcp.write_text(json.dumps(initial, separators=(",", ":")))
        before = b"# Project rules\r\nKeep these exact bytes: \xff\r\n"
        self.guidance.write_bytes(before)
        self.guidance.chmod(0o640)
        observed = self.observed()
        preview = claude.install(**self.options)
        self.assertEqual(observed, self.observed())
        self.assertIsNone(preview["snapshot"])
        applied = claude.install(**self.options, apply=True)
        configured = json.loads(self.mcp.read_bytes())
        server = configured["mcpServers"].pop("retulo")
        self.assertEqual(configured, initial)
        self.assertEqual(server, {"type": "stdio", "command": sys.executable,
            "args": ["-I", "-m", "retulo", "--config", str(self.options["engine_config"]), "mcp"]})
        self.assertTrue(self.guidance.read_bytes().startswith(before))
        self.assertEqual(self.guidance.stat().st_mode & 0o777, 0o640)
        settings = json.loads(self.options["engine_config"].read_bytes())
        self.assertEqual(settings["allowed_source_roots"], [str(self.project)])
        self.assertTrue(Path(applied["snapshot"]).is_dir())
        self.assertFalse((self.root / ".claude").exists())
        self.assertTrue(claude.status(**self.options)["managed"])

    def test_noop_keeps_every_file_byte_and_snapshot_count(self):
        claude.install(**self.options, apply=True)
        mcp = json.loads(self.mcp.read_bytes())
        self.mcp.write_text("\n" + json.dumps(mcp, separators=(",", ":")) + "\n\n")
        config = json.loads(self.options["engine_config"].read_bytes())
        self.options["engine_config"].write_text(json.dumps(config, indent="\t"))
        before = self.observed()
        paths = set(self.root.rglob("*"))
        result = claude.install(**self.options, apply=True)
        self.assertIsNone(result["snapshot"])
        self.assertFalse(any(row["changed"] for row in result["files"]))
        self.assertEqual(self.observed(), before)
        self.assertEqual(set(self.root.rglob("*")), paths)

    def test_existing_config_keeps_custom_state_and_other_allowlists(self):
        value = {"workspace_home": str(self.options["workspace_home"]),
                 "state_home": str(self.root / "chosen-state"),
                 "allowed_workspace_roots": [str(self.root / "other-workspace")],
                 "allowed_source_roots": [str(self.root / "other-source")]}
        self.options["engine_config"].write_text(json.dumps(value))
        claude.install(**self.options, apply=True)
        after = json.loads(self.options["engine_config"].read_bytes())
        self.assertEqual(after, {**value, "allowed_source_roots": [str(self.root / "other-source"), str(self.project)]})
        before = self.observed()
        with self.assertRaisesRegex(ValueError, "workspace_home"):
            claude.install(**{**self.options, "workspace_home": self.root / "wrong"}, apply=True)
        self.assertEqual(self.observed(), before)

    def test_managed_runtime_and_template_upgrade_then_guarded_snapshot_undo(self):
        first = claude.install(**self.options, apply=True)
        old_guidance = self.guidance.read_bytes()
        alternate = self.root / "alternate python"
        alternate.symlink_to(sys.executable)
        template = claude._template().replace(b"file workflow", b"file workflow revision two")
        with mock.patch.object(claude, "_template", return_value=template):
            preview = claude.install(**self.options, runtime_python=alternate)
            self.assertEqual(preview["components"]["mcp"]["status"], "managed_upgrade")
            self.assertEqual(preview["components"]["guidance"]["status"], "managed_upgrade")
            upgraded = claude.install(**self.options, runtime_python=alternate, apply=True)
        self.assertEqual(json.loads(self.mcp.read_bytes())["mcpServers"]["retulo"]["command"], str(alternate))
        self.assertIn(b"revision two", self.guidance.read_bytes())
        config_before = self.options["engine_config"].read_bytes()
        result = claude.disconnect(**self.options, snapshot=upgraded["snapshot"], apply=True)
        self.assertEqual(result["status"], "restored")
        self.assertEqual(self.guidance.read_bytes(), old_guidance)
        self.assertEqual(self.options["engine_config"].read_bytes(), config_before)
        self.assertEqual(claude.disconnect(**self.options, snapshot=upgraded["snapshot"], apply=True)["status"],
                         "already_disconnected")
        self.assertEqual(json.loads(Path(first["managed_baseline"]).read_bytes())["snapshot"], first["snapshot"])

    def test_legacy_managed_command_upgrades_to_isolated_python_without_losing_other_fields(self):
        # Build a genuine managed snapshot using the previous shipped argument prefix.
        with mock.patch.object(claude, "PYTHON_MODULE_ARGS", ("-m", "retulo")):
            first = claude.install(**self.options, apply=True)
        value = json.loads(self.mcp.read_bytes())
        value["mcpServers"]["unrelated"] = {"command": "keep"}
        value["custom"] = [True, {"nested": "preserved"}]
        self.mcp.write_text(json.dumps(value))
        preview = claude.install(**self.options)
        self.assertEqual(preview["components"]["mcp"]["status"], "managed_upgrade")
        upgraded = claude.install(**self.options, apply=True)
        after = json.loads(self.mcp.read_bytes())
        self.assertEqual(after["mcpServers"]["retulo"]["args"][:3], ["-I", "-m", "retulo"])
        self.assertEqual(after["mcpServers"]["unrelated"], {"command": "keep"})
        self.assertEqual(after["custom"], value["custom"])
        self.assertNotEqual(upgraded["snapshot"], first["snapshot"])
        self.assertTrue(claude.status(**self.options)["ready"])
        self.assertIsNone(claude.install(**self.options, apply=True)["snapshot"])

    def test_generated_installed_command_ignores_hostile_project_and_pythonpath_modules(self):
        # A local package copy in a clean venv exercises normal installed-module lookup.
        # No network, pip, host configuration, or Claude invocation is involved.
        runtime_home = self.root / "installed-runtime"
        venv.EnvBuilder(with_pip=False).create(runtime_home)
        python = runtime_home / "bin/python"
        purelib = subprocess.check_output([str(python), "-I", "-c",
            "import sysconfig;print(sysconfig.get_path('purelib'))"], text=True).strip()
        source = Path(__file__).resolve().parents[1] / "src/retulo"
        shutil.copytree(source, Path(purelib) / "retulo", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        claude.install(**self.options, runtime_python=python, apply=True)
        server = json.loads(self.mcp.read_bytes())["mcpServers"]["retulo"]
        malicious_home = self.root / "hostile-pythonpath"
        malicious_home.mkdir()
        project_marker = self.root / "project-module-executed"
        environment_marker = self.root / "environment-module-executed"
        startup_marker = self.root / "environment-startup-executed"
        def payload(marker):
            return f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\nraise RuntimeError('hostile module ran')\n"
        hostile_project = self.project / "retulo.py"
        hostile_project.write_text(payload(project_marker))
        (malicious_home / "retulo.py").write_text(payload(environment_marker))
        (malicious_home / "sitecustomize.py").write_text(payload(startup_marker))
        environment = {**os.environ, "PYTHONPATH": str(malicious_home)}
        for project_module_present in (True, False):
            with self.subTest(project_module_present=project_module_present):
                if not project_module_present:
                    hostile_project.unlink()
                # --help proves the actual installed entry point loads while avoiding
                # MCP dependencies and a long-running server in this regression test.
                result = subprocess.run([server["command"], *server["args"], "--help"],
                    cwd=self.project, env=environment, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)
                self.assertFalse(project_marker.exists())
                self.assertFalse(environment_marker.exists())
                self.assertFalse(startup_marker.exists())

    def test_disconnect_restores_original_bytes_and_retains_engine_state(self):
        initial_mcp = b'\n{"future":true,"mcpServers":{"other":{"command":"keep"}}}\n'
        initial_guidance = b"Keep existing instruction without a newline"
        self.mcp.write_bytes(initial_mcp)
        self.guidance.write_bytes(initial_guidance)
        claude.install(**self.options, apply=True)
        self.options["workspace_home"].mkdir()
        (self.options["workspace_home"] / "original.txt").write_text("keep")
        config = self.options["engine_config"].read_bytes()
        before = self.observed()
        claude.disconnect(**self.options)
        self.assertEqual(self.observed(), before)
        result = claude.disconnect(**self.options, apply=True)
        self.assertEqual(result["status"], "disconnected")
        self.assertEqual(self.mcp.read_bytes(), initial_mcp)
        self.assertEqual(self.guidance.read_bytes(), initial_guidance)
        self.assertEqual(self.options["engine_config"].read_bytes(), config)
        self.assertEqual((self.options["workspace_home"] / "original.txt").read_text(), "keep")
        self.assertEqual(claude.disconnect(**self.options, apply=True)["status"], "already_disconnected")

    def test_disconnect_new_project_deletes_only_created_project_files(self):
        claude.install(**self.options, apply=True)
        claude.disconnect(**self.options, apply=True)
        self.assertFalse(self.mcp.exists())
        self.assertFalse(self.guidance.exists())
        self.assertTrue(self.options["engine_config"].exists())

    def test_unrelated_edits_survive_upgrade_and_disconnect(self):
        self.guidance.write_bytes(b"original\r\n")
        claude.install(**self.options, apply=True)
        value = json.loads(self.mcp.read_bytes())
        value["anotherSetting"] = {"meaningful": 7}
        value["mcpServers"]["newOther"] = {"command": "keep"}
        self.mcp.write_bytes(json.dumps(value).encode())
        self.guidance.write_bytes(b"added prefix\n" + self.guidance.read_bytes() + b"added suffix\n")
        updated = claude._template().replace(b"file workflow", b"file workflow v2")
        with mock.patch.object(claude, "_template", return_value=updated):
            claude.install(**self.options, apply=True)
        claude.disconnect(**self.options, apply=True)
        self.assertEqual(json.loads(self.mcp.read_bytes()), {"anotherSetting": {"meaningful": 7},
                         "mcpServers": {"newOther": {"command": "keep"}}})
        self.assertEqual(self.guidance.read_bytes(), b"added prefix\noriginal\r\nadded suffix\n")

    def test_foreign_components_and_user_modified_owned_components_are_refused(self):
        self.mcp.write_text('{"mcpServers":{"retulo":{"command":"foreign"}}}')
        before = self.observed()
        for apply in (False, True):
            with self.assertRaises(claude.InstallConflict):
                claude.install(**self.options, apply=apply)
            self.assertEqual(self.observed(), before)
        self.mcp.unlink()
        claude.install(**self.options, apply=True)
        guidance = self.guidance.read_bytes()
        self.guidance.write_bytes(guidance.replace(b"file workflow", b"user customized workflow"))
        before = self.observed()
        for action in (claude.install, claude.disconnect):
            for apply in (False, True):
                with self.assertRaises(claude.InstallConflict):
                    action(**self.options, apply=apply)
                self.assertEqual(self.observed(), before)
        status = claude.status(**self.options)
        self.assertTrue(status["conflicts"])
        self.assertEqual(status["components"]["guidance"]["status"], "unmanaged")
        self.guidance.write_bytes(guidance)
        value = json.loads(self.mcp.read_bytes())
        value["mcpServers"]["retulo"]["env"] = {"CUSTOM": "user edit"}
        self.mcp.write_text(json.dumps(value))
        with self.assertRaises(claude.InstallConflict):
            claude.disconnect(**self.options, apply=True)

    def test_exact_preexisting_components_without_managed_ownership_are_refused(self):
        server = {"type": "stdio", "command": sys.executable,
                  "args": ["-I", "-m", "retulo", "--config", str(self.options["engine_config"]), "mcp"]}
        fixtures = [(self.mcp, b"\n" + json.dumps({"custom": "keep", "mcpServers": {"retulo": server}}).encode() + b"\n\n"),
                    (self.guidance, b"existing instruction\r\n" + claude._template() + b"\ntrailing instruction\r\n")]
        for path, content in fixtures:
            with self.subTest(component=path.name):
                path.write_bytes(content)
                before = self.observed()
                paths = set(self.root.rglob("*"))
                for apply in (False, True):
                    with self.assertRaisesRegex(claude.InstallConflict, "without verified ownership"):
                        claude.install(**self.options, apply=apply)
                    self.assertEqual(self.observed(), before)
                    self.assertEqual(set(self.root.rglob("*")), paths)
                with self.assertRaisesRegex(ValueError, "no managed baseline"):
                    claude.disconnect(**self.options, apply=True)
                self.assertEqual(self.observed(), before)
                path.unlink()

    def test_duplicate_json_keys_markers_and_nonregular_files_fail_without_writes(self):
        invalid = [b'{"mcpServers":{},"mcpServers":{}}',
                   b'{"mcpServers":{"other":{"command":"a","command":"b"}}}']
        for content in invalid:
            self.mcp.write_bytes(content)
            before = self.observed()
            with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
                claude.install(**self.options, apply=True)
            self.assertEqual(self.observed(), before)
        self.mcp.unlink()
        self.guidance.write_bytes(claude.START + b"\n" + claude.END + b"\n" + claude.START + b"\n" + claude.END)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            claude.install(**self.options, apply=True)
        self.guidance.unlink()
        os.mkfifo(self.mcp)
        with self.assertRaisesRegex(ValueError, "regular file"):
            claude.install(**self.options, apply=True)
        self.mcp.unlink()
        outside = self.root / "outside.json"
        outside.write_text("{}")
        self.mcp.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            claude.install(**self.options, apply=True)
        self.assertEqual(outside.read_text(), "{}")
        self.assertFalse(self.options["snapshot_home"].exists())

    def test_failure_rolls_back_all_written_targets(self):
        self.guidance.write_bytes(b"original rules")
        original = claude.atomic
        def fail_metadata(path, raw, mode=0o600, **kwargs):
            if path.name.startswith("managed-claude-"):
                raise OSError("injected metadata failure")
            return original(path, raw, mode, **kwargs)
        with mock.patch.object(claude, "atomic", side_effect=fail_metadata):
            with self.assertRaisesRegex(OSError, "metadata failure"):
                claude.install(**self.options, apply=True)
        self.assertEqual(self.guidance.read_bytes(), b"original rules")
        self.assertFalse(self.mcp.exists())
        self.assertFalse(self.options["engine_config"].exists())
        receipts = list(self.options["snapshot_home"].glob("*/manifest.json"))
        self.assertEqual(len(receipts), 1)
        self.assertTrue(json.loads(receipts[0].read_bytes())["rollback_verified"])

    def test_concurrent_unrelated_edit_is_preserved_and_earlier_writes_rollback(self):
        original = claude.atomic
        def interfere(path, raw, mode=0o600, **kwargs):
            if path == self.guidance:
                path.write_bytes(b"concurrent user's instruction")
            return original(path, raw, mode, **kwargs)
        with mock.patch.object(claude, "atomic", side_effect=interfere):
            with self.assertRaisesRegex(ValueError, "Concurrent"):
                claude.install(**self.options, apply=True)
        self.assertEqual(self.guidance.read_bytes(), b"concurrent user's instruction")
        self.assertFalse(self.mcp.exists())
        self.assertFalse(self.options["engine_config"].exists())

    def test_snapshot_undo_rejects_arbitrary_targets_and_retains_config(self):
        result = claude.install(**self.options, apply=True)
        manifest = Path(result["snapshot"]) / "manifest.json"
        record = json.loads(manifest.read_bytes())
        unrelated = self.root / "do-not-touch"
        unrelated.write_text("original")
        record["files"][0]["path"] = str(unrelated)
        manifest.write_text(json.dumps(record))
        before = self.observed()
        with self.assertRaisesRegex(ValueError, "explicit target"):
            claude.disconnect(**self.options, snapshot=result["snapshot"], apply=True)
        self.assertEqual(self.observed(), before)
        self.assertEqual(unrelated.read_text(), "original")

    def test_status_is_readonly_and_detects_missing_config_runtime_and_project_allowlist(self):
        before = self.observed()
        self.assertFalse(claude.status(**self.options)["ready"])
        self.assertEqual(self.observed(), before)
        alternate = self.root / "removable-python"
        alternate.symlink_to(sys.executable)
        claude.install(**self.options, runtime_python=alternate, apply=True)
        self.assertTrue(claude.status(**self.options)["ready"])
        alternate.unlink()
        self.assertIn("existing executable", " ".join(claude.status(**self.options)["conflicts"]))
        alternate.symlink_to(sys.executable)
        config = self.options["engine_config"]
        value = json.loads(config.read_bytes())
        value["allowed_source_roots"] = []
        config.write_text(json.dumps(value))
        self.assertIn("project root", " ".join(claude.status(**self.options)["conflicts"]))
        config.unlink()
        self.assertIn("configuration is missing", " ".join(claude.status(**self.options)["conflicts"]))

    def test_incomplete_or_tampered_managed_receipt_is_not_adopted(self):
        installed = claude.install(**self.options, apply=True)
        manifest = Path(installed["snapshot"]) / "manifest.json"
        record = json.loads(manifest.read_bytes())
        record["status"] = "prepared"
        manifest.write_text(json.dumps(record))
        before = self.observed()
        for action in (claude.install, claude.disconnect):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                action(**self.options, apply=True)
        self.assertEqual(self.observed(), before)
        record["status"] = "complete"
        manifest.write_text(json.dumps(record))
        metadata = Path(installed["managed_baseline"])
        value = json.loads(metadata.read_bytes())
        value["originals"]["mcp"]["sha256"] = "0" * 64
        metadata.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, "completed setup snapshot"):
            claude.disconnect(**self.options, apply=True)

    def test_disconnect_failure_restores_installed_state(self):
        claude.install(**self.options, apply=True)
        before = {path: path.read_bytes() for path in (self.mcp, self.guidance, self.options["engine_config"])}
        original = claude.atomic
        failed = False
        def fail_completion(path, raw, mode=0o600, **kwargs):
            nonlocal failed
            if path.name == "manifest.json":
                value = json.loads(raw)
                if value.get("operation") == "disconnect" and value.get("status") == "complete" and not failed:
                    failed = True
                    raise OSError("injected disconnect completion failure")
            return original(path, raw, mode, **kwargs)
        with mock.patch.object(claude, "atomic", side_effect=fail_completion):
            with self.assertRaisesRegex(OSError, "disconnect completion"):
                claude.disconnect(**self.options, apply=True)
        self.assertEqual({path: path.read_bytes() for path in before}, before)
        self.assertTrue(claude.status(**self.options)["ready"])

    def test_two_process_installers_share_locks_and_finish_with_consistent_baseline(self):
        options = {key: str(value) for key, value in self.options.items()}
        code = "from retulo.integrations.claude import install;import json,sys;print(json.dumps(install(**json.loads(sys.argv[1]),apply=True)))"
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        jobs = [subprocess.Popen([sys.executable, "-c", code, json.dumps(options)], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env=environment) for _ in range(2)]
        for job in jobs:
            self.addCleanup(lambda job=job: job.kill() if job.poll() is None else None)
        results = []
        for job in jobs:
            stdout, stderr = job.communicate(timeout=15)
            self.assertEqual(job.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sum(item["snapshot"] is not None for item in results), 1)
        self.assertFalse(claude.status(**self.options)["conflicts"])
        self.assertIsNone(claude.install(**self.options, apply=True)["snapshot"])


if __name__ == "__main__":
    unittest.main()
