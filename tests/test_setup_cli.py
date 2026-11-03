"""Public agent setup commands, using isolated settings and no model calls."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import venv
import tempfile
import unittest
from unittest import mock

from retulo import __version__, installation
from retulo.cli import METHODS, init_config, load_config, main, make_parser


class SetupCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project with spaces"
        self.project.mkdir()
        self.options = dict(project=self.project, config=self.root / "config/config.json",
                            workspace_home=self.root / "workspaces", codex_home=self.root / "codex",
                            skills_home=self.root / "skills", snapshot_home=self.root / "snapshots")

    def run_cli(self, command, agent="codex", *extra):
        arguments = ["--config", str(self.options["config"]), command, agent]
        for name in ("project", "workspace_home", "codex_home", "skills_home", "snapshot_home"):
            arguments.extend(["--" + name.replace("_", "-"), str(self.options[name])])
        output = io.StringIO()
        with redirect_stdout(output):
            status = main([*arguments, *extra])
        return status, json.loads(output.getvalue())

    def test_setup_preview_is_read_only_and_actionable(self):
        before = set(self.root.rglob("*"))
        code, result = self.run_cli("setup")
        self.assertEqual(code, 0, result)
        self.assertEqual(result["status"], "preview")
        self.assertFalse(result["apply"])
        self.assertIn("--apply", result["next_action"])
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_setup_is_idempotent_and_does_not_change_other_settings(self):
        self.options["codex_home"].mkdir()
        settings = self.options["codex_home"] / "config.toml"
        settings.write_text('model = "unchanged"\n[mcp_servers.other]\ncommand = "other"\n')
        code, result = self.run_cli("setup", "codex", "--apply")
        self.assertEqual(code, 0, result)
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        code, repeated = self.run_cli("setup", "codex", "--apply")
        self.assertEqual(code, 0, repeated)
        self.assertIsNone(repeated["snapshot"])
        self.assertFalse(any(row["changed"] for row in repeated["files"]))
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        self.assertIn('model = "unchanged"', settings.read_text())
        self.assertEqual(load_config(self.options["config"])["allowed_source_roots"], [str(self.project)])
        value = __import__("tomllib").loads(settings.read_text())["mcp_servers"]["retulo"]
        self.assertEqual(value["command"], os.path.abspath(sys.executable))
        self.assertEqual(value["args"], ["-I", "-m", "retulo", "--config", str(self.options["config"]), "mcp"])

    def test_existing_workspace_and_state_are_reused_and_roots_extended(self):
        existing_workspace = self.root / "existing workspace"
        state = self.root / "separate-state"
        first_project = self.root / "first-project"
        first_project.mkdir()
        init_config(self.options["config"], workspace_home=str(existing_workspace), state_home=str(state),
                    allowed_source_roots=[str(first_project)])
        options = {key: value for key, value in self.options.items() if key != "workspace_home"}
        preview = installation.setup("codex", **options)
        self.assertEqual(preview["workspace_home"], str(existing_workspace))
        installation.setup("codex", apply=True, **options)
        config = load_config(self.options["config"])
        self.assertEqual(config["workspace_home"], str(existing_workspace))
        self.assertEqual(config["state_home"], str(state))
        self.assertEqual(config["allowed_source_roots"], [str(first_project), str(self.project)])
        original = self.options["config"].read_bytes()
        installation.setup("codex", apply=True, **options)
        self.assertEqual(self.options["config"].read_bytes(), original)

    def test_project_guards_and_symlink_paths_leave_targets_untouched(self):
        for project in (Path.home(), Path("/"), self.root / "missing"):
            with self.subTest(project=project), self.assertRaises(installation.InstallationError):
                installation.setup("codex", **{**self.options, "project": project}, apply=True)
        alias = self.root / "alias"
        alias.symlink_to(self.project, target_is_directory=True)
        with self.assertRaisesRegex(installation.InstallationError, "symbolic links"):
            installation.setup("codex", **{**self.options, "project": alias}, apply=True)
        self.assertFalse(self.options["config"].exists())
        self.assertFalse(self.options["codex_home"].exists())

    def test_default_project_is_checked_and_relative_paths_are_absolute(self):
        previous = Path.cwd()
        try:
            os.chdir(self.project)
            target = installation.locations("codex", config="config.json", codex_home="codex",
                                            skills_home="skills", workspace_home="workspaces", snapshot_home="snapshots")
            self.assertEqual(target.project, self.project)
            self.assertEqual(target.engine_config, self.project / "config.json")
            os.chdir(Path.home())
            with self.assertRaises(installation.InstallationError):
                installation.locations("codex", config=self.options["config"])
        finally:
            os.chdir(previous)

    def test_explicit_mismatched_workspace_fails_without_overwrite(self):
        init_config(self.options["config"], workspace_home=str(self.root / "original"))
        original = self.options["config"].read_bytes()
        code, result = self.run_cli("setup", "codex", "--apply")
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(self.options["config"].read_bytes(), original)
        self.assertFalse((self.options["codex_home"] / "config.toml").exists())

    def test_doctor_reports_missing_installation_without_writing(self):
        before = set(self.root.rglob("*"))
        code, result = self.run_cli("doctor")
        self.assertEqual(code, 1, result)
        self.assertFalse(result["configured"])
        self.assertEqual(result["transport"]["status"], "not_tested")
        self.assertEqual(result["host_adoption"], "untested")
        self.assertEqual(set(self.root.rglob("*")), before)

    def test_doctor_checks_configured_entries_but_does_not_claim_host_adoption(self):
        installation.setup("codex", **self.options, apply=True)
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        code, result = self.run_cli("doctor")
        self.assertEqual(code, 0, result)
        self.assertTrue(result["configured"])
        self.assertEqual(result["transport"]["status"], "not_tested")
        self.assertEqual(result["host_adoption"], "untested")
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})

    def test_doctor_rejects_modified_hook_and_foreign_command_without_launching(self):
        installation.setup("codex", **self.options, apply=True)
        path = self.options["codex_home"] / "config.toml"
        path.write_text(path.read_text().replace(os.path.abspath(sys.executable), "/foreign/python"))
        with mock.patch.object(installation, "_probe_mcp", new_callable=mock.AsyncMock) as probe:
            result = installation.doctor("codex", **self.options, connect=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["transport"]["status"], "not_started")
        probe.assert_not_called()

    def test_doctor_connect_success_and_timeout_are_bounded_and_reported(self):
        installation.setup("codex", **self.options, apply=True)
        with mock.patch.object(installation, "_probe_mcp", new_callable=mock.AsyncMock,
                               return_value={"status": "healthy", "tool_count": len(METHODS)}) as probe:
            result = installation.doctor("codex", **self.options, connect=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["host_adoption"], "untested")
        probe.assert_awaited_once()
        with mock.patch.object(installation, "_probe_mcp", new_callable=mock.AsyncMock, side_effect=TimeoutError):
            result = installation.doctor("codex", **self.options, connect=True)
        self.assertFalse(result["ok"])
        self.assertIn("15 seconds", result["transport"]["message"])

    def test_doctor_connect_ignores_project_modules_and_pythonpath(self):
        # The already-imported CLI is trusted; its spawned MCP must ignore both attack surfaces.
        marker = self.root / "shadow-code-ran"
        attack = self.root / "pythonpath-attack"
        attack.mkdir()
        payload = f"from pathlib import Path; Path({str(marker)!r}).write_text('executed')\n"
        (self.project / "retulo.py").write_text(payload)
        (attack / "retulo.py").write_text(payload)
        (attack / "sitecustomize.py").write_text(payload)
        previous = Path.cwd()
        try:
            os.chdir(self.project)
            with mock.patch.dict(os.environ, {"PYTHONPATH": str(attack)}):
                for agent in ("codex", "claude"):
                    with self.subTest(agent=agent):
                        options = {**self.options, "snapshot_home": self.root / ("snapshots-" + agent)}
                        installation.setup(agent, **options, apply=True)
                        result = installation.doctor(agent, **options, connect=True)
                        self.assertTrue(result["ok"], result)
                        self.assertEqual(result["transport"]["status"], "healthy")
                        self.assertFalse(marker.exists())
        finally:
            os.chdir(previous)

    def test_doctor_does_not_launch_legacy_nonisolated_commands(self):
        from retulo.integrations import codex
        original_server = codex._server
        # Archive a genuine prior generated command so the later upgrade has valid provenance.
        with mock.patch.object(codex, "_server", side_effect=lambda python, engine_config, **kwargs:
                               original_server(python, engine_config, isolated=False)):
            installation.setup("codex", **self.options, apply=True)
        with mock.patch.object(installation, "_probe_mcp", new_callable=mock.AsyncMock) as probe:
            result = installation.doctor("codex", **self.options, connect=True)
        self.assertFalse(result["ok"])
        self.assertFalse(result["configured"])
        self.assertEqual(result["transport"]["status"], "not_started")
        probe.assert_not_called()
        # A verified setup upgrade is the explicit repair route.
        installation.setup("codex", **self.options, apply=True)
        self.assertTrue(installation.doctor("codex", **self.options)["configured"])

    def test_pythonpath_only_runtime_is_rejected_before_settings_writes(self):
        environment = self.root / "empty-python"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(environment)
        interpreter = environment / "bin/python"
        package_source = str(Path(installation.__file__).resolve().parents[1])
        with mock.patch.object(sys, "executable", str(interpreter)), mock.patch.dict(
                os.environ, {"PYTHONPATH": package_source}):
            code, result = self.run_cli("setup", "codex", "--apply")
        self.assertEqual(code, 2, result)
        self.assertIn("cannot load the installed Retulo package in isolation", result["error"]["message"])
        self.assertFalse(self.options["config"].exists())
        self.assertFalse(self.options["codex_home"].exists())

    def test_runtime_probe_rejects_other_installed_module_and_times_out(self):
        module = str(Path(installation.__file__).with_name("__init__.py").resolve())
        installation._installed_runtime.cache_clear()
        self.addCleanup(installation._installed_runtime.cache_clear)
        completed = __import__("subprocess").CompletedProcess([], 0,
            stdout=json.dumps({"version": __version__, "module": str(self.root / "other/retulo/__init__.py")}))
        with mock.patch.object(installation.subprocess, "run", return_value=completed) as run:
            with self.assertRaises(installation.InstallationError):
                installation._installed_runtime(sys.executable, __version__, module)
        self.assertEqual(run.call_args.args[0][1:4], ["-I", "-B", "-c"])
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        with mock.patch.object(installation.subprocess, "run", side_effect=installation.subprocess.TimeoutExpired([], 5)):
            with self.assertRaises(installation.InstallationError):
                installation._installed_runtime(sys.executable, __version__, module)

    def test_temporary_uvx_runtime_is_rejected(self):
        for executable in ("/tmp/.cache/uv/archive-v0/random/bin/python",
                           "/custom-cache/archive-v17/random/bin/python"):
            with self.subTest(executable=executable), mock.patch.object(sys, "executable", executable):
                code, result = self.run_cli("setup", "codex", "--apply")
            self.assertEqual(code, 2)
            self.assertIn(f"https://github.com/TheStack-ai/retulo/releases/download/v{__version__}/install.sh", result["error"]["details"]["action"])
            self.assertFalse(self.options["config"].exists())

    def test_disconnect_preview_and_apply_preserve_core_config_and_workspaces(self):
        installation.setup("codex", **self.options, apply=True)
        self.options["workspace_home"].mkdir(exist_ok=True)
        document = self.options["workspace_home"] / "keep.txt"
        document.write_text("task data")
        config_before = self.options["config"].read_bytes()
        settings = self.options["codex_home"] / "config.toml"
        installed = settings.read_bytes()
        code, preview = self.run_cli("disconnect")
        self.assertEqual(code, 0, preview)
        self.assertEqual(settings.read_bytes(), installed)
        code, result = self.run_cli("disconnect", "codex", "--apply")
        self.assertEqual(code, 0, result)
        self.assertFalse(settings.exists())
        self.assertEqual(self.options["config"].read_bytes(), config_before)
        self.assertEqual(document.read_text(), "task data")
        self.assertTrue(list(self.options["snapshot_home"].glob("*/manifest.json")))
        code, repeated = self.run_cli("disconnect", "codex", "--apply")
        self.assertEqual(code, 0, repeated)
        self.assertEqual(repeated["status"], "already_disconnected")

    def test_disconnect_undoes_multiple_updates_as_one_validated_chain(self):
        installation.setup("codex", **self.options, apply=True)
        second_project = self.root / "second-project"
        second_project.mkdir()
        installation.setup("codex", **{**self.options, "project": second_project}, apply=True)
        config_before = self.options["config"].read_bytes()
        result = installation.disconnect("codex", **self.options, apply=True)
        self.assertEqual(len(result["snapshots"]), 2)
        self.assertFalse((self.options["codex_home"] / "config.toml").exists())
        self.assertEqual(self.options["config"].read_bytes(), config_before)

    def test_disconnect_covers_runtime_updates_and_stops_at_current_installation(self):
        settings = self.options["codex_home"] / "config.toml"
        settings.parent.mkdir()
        settings.write_text('model = "first"\n')
        installation.setup("codex", **self.options, apply=True)
        alternate = self.root / "another-python"
        alternate.symlink_to(sys.executable)
        with mock.patch.object(installation, "_runtime", return_value=str(alternate)):
            installation.setup("codex", **self.options, apply=True)
        result = installation.disconnect("codex", **self.options, apply=True)
        self.assertEqual(len(result["snapshots"]), 2)
        self.assertEqual(settings.read_text(), 'model = "first"\n')
        settings.write_text('model = "later user preference"\n')
        installation.setup("codex", **self.options, apply=True)
        result = installation.disconnect("codex", **self.options, apply=True)
        self.assertEqual(len(result["snapshots"]), 1)
        self.assertEqual(settings.read_text(), 'model = "later user preference"\n')

    def test_invalid_codex_tables_and_symlink_settings_report_actionable_errors(self):
        settings = self.options["codex_home"] / "config.toml"
        settings.parent.mkdir()
        settings.write_text('mcp_servers = "invalid"\n')
        original = settings.read_bytes()
        code, result = self.run_cli("setup", "codex", "--apply")
        self.assertEqual(code, 2)
        self.assertEqual(result["error"]["code"], "installation_error")
        self.assertIn("must be a table", result["error"]["message"])
        self.assertEqual(settings.read_bytes(), original)
        settings.unlink()
        original_file = self.root / "original-settings.toml"
        original_file.write_text('model = "keep"\n')
        settings.symlink_to(original_file)
        code, result = self.run_cli("setup", "codex", "--apply")
        self.assertEqual(code, 2)
        self.assertIn("symbolic links", result["error"]["message"])
        self.assertEqual(original_file.read_text(), 'model = "keep"\n')
        self.assertFalse(self.options["config"].exists())

    def test_disconnect_refuses_user_edits_and_missing_snapshots(self):
        installation.setup("codex", **self.options, apply=True)
        settings = self.options["codex_home"] / "config.toml"
        edited = settings.read_bytes() + b'\n# User edit\n'
        settings.write_bytes(edited)
        code, result = self.run_cli("disconnect", "codex", "--apply")
        self.assertEqual(code, 2)
        self.assertFalse(result["ok"])
        self.assertEqual(settings.read_bytes(), edited)
        with self.assertRaisesRegex(installation.InstallationError, "No matching completed"):
            installation.disconnect("codex", **{**self.options, "snapshot_home": self.root / "missing"}, apply=True)
        self.assertEqual(settings.read_bytes(), edited)

    def test_claude_setup_doctor_disconnect_retain_foreign_entries(self):
        settings = self.project / ".mcp.json"
        original = '{"mcpServers":{"other":{"command":"other"}},"extra":true}\n'
        settings.write_text(original)
        guidance = self.project / "CLAUDE.md"
        guidance.write_text("User project instructions\n")
        before = set(self.root.rglob("*"))
        code, preview = self.run_cli("setup", "claude")
        self.assertEqual(code, 0, preview)
        self.assertEqual(set(self.root.rglob("*")), before)
        self.assertEqual(settings.read_text(), original)
        code, applied = self.run_cli("setup", "claude", "--apply")
        self.assertEqual(code, 0, applied)
        code, diagnosed = self.run_cli("doctor", "claude")
        self.assertEqual(code, 0, diagnosed)
        self.assertTrue(diagnosed["configured"])
        config_before = self.options["config"].read_bytes()
        code, disconnected = self.run_cli("disconnect", "claude", "--apply")
        self.assertEqual(code, 0, disconnected)
        self.assertEqual(settings.read_text(), original)
        self.assertEqual(guidance.read_text(), "User project instructions\n")
        self.assertEqual(self.options["config"].read_bytes(), config_before)

    def test_claude_foreign_edits_block_disconnect_and_doctor_connection(self):
        installation.setup("claude", **self.options, apply=True)
        guidance = self.project / "CLAUDE.md"
        original = guidance.read_bytes()
        guidance.write_bytes(original.replace(b"Retulo", b"Edited Retulo", 1))
        with mock.patch.object(installation, "_probe_mcp", new_callable=mock.AsyncMock) as probe:
            result = installation.doctor("claude", **self.options, connect=True)
        self.assertFalse(result["ok"])
        probe.assert_not_called()
        code, result = self.run_cli("disconnect", "claude", "--apply")
        self.assertEqual(code, 2, result)
        self.assertIn(b"Edited Retulo", guidance.read_bytes())

    def test_cli_flags_do_not_expand_the_core_or_mcp_allowlist(self):
        for name in ("setup", "doctor", "disconnect"):
            self.assertNotIn(name, METHODS)
        parser = make_parser()
        args = parser.parse_args(["--config", "/global/config.json", "setup", "codex", "--project", str(self.project)])
        self.assertEqual(args.config, "/global/config.json")
        args = parser.parse_args(["--config", "/global/config.json", "setup", "claude", "--config", "/local/config.json"])
        self.assertEqual(args.config, "/local/config.json")


if __name__ == "__main__":
    unittest.main()
