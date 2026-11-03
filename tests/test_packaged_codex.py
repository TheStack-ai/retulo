"""Synthetic package/runtime installation checks; never touch user settings."""
import json
import asyncio
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest import mock

from retulo.integrations import codex, codex_restore


class PackagedCodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.options = dict(codex_home=self.root/'codex', engine_config=self.root/'engine/config.json',
                            skills_home=self.root/'skills', workspace_home=self.root/'workspaces',
                            source_roots=[], snapshot_home=self.root/'snapshots')

    def tree(self):
        return {str(p.relative_to(self.root)):(p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                for p in self.root.rglob('*') if p.is_file()}

    def restore_options(self):
        return {key:self.options[key] for key in ('codex_home','engine_config','skills_home','snapshot_home')}

    def write_historical_harness_receipt(self, path, record):
        """Keep a synthetic old completed snapshot consistent with its receipt bytes."""
        path.write_text(json.dumps(record))
        manifest = Path(record['components']['hooks']['base_copy']).parent/'manifest.json'
        installation = json.loads(manifest.read_bytes())
        next(row for row in installation['files'] if row['path'] == str(path))['after_sha256'] = codex.digest(path.read_bytes())
        manifest.write_text(json.dumps(installation))

    def test_package_defaults_use_current_interpreter_and_bundled_templates(self):
        before = self.tree()
        preview = codex.install(**self.options)
        self.assertEqual(before, self.tree())
        self.assertTrue(all(row['changed'] for row in preview['files']))
        installed = codex.install(**self.options, apply=True)
        config = tomllib.loads((self.options['codex_home']/'config.toml').read_text())
        self.assertEqual(config['mcp_servers']['retulo'], codex._server(Path(sys.executable).absolute(), self.options['engine_config']))
        self.assertEqual(shlex.split(installed['harness']['command']),
                         [str(Path(sys.executable).absolute()), '-I', '-m', 'retulo', '--config',
                          str(self.options['engine_config']), 'codex-hook'])
        self.assertEqual((self.options['skills_home']/'retulo/SKILL.md').read_bytes(),
                         codex.resources.files('retulo').joinpath('resources/codex/SKILL.md').read_bytes())
        self.assertIsNone(codex.install(**self.options, apply=True)['snapshot'])

    def test_explicit_runtime_retains_venv_symlink_identity(self):
        runtime = self.root/'runtime/bin/python'
        runtime.parent.mkdir(parents=True)
        runtime.symlink_to(Path(sys.executable).resolve())
        codex.install(**self.options, runtime_python=runtime, apply=True)
        config = tomllib.loads((self.options['codex_home']/'config.toml').read_text())
        self.assertEqual(config['mcp_servers']['retulo']['command'], str(runtime))

    def test_generated_mcp_and_hooks_ignore_hostile_cwd_and_pythonpath(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        installed = codex.install(**self.options, apply=True)
        server = tomllib.loads((self.options['codex_home']/'config.toml').read_text())['mcp_servers']['retulo']
        hostile = self.root/'untrusted-project'
        hostile_path = self.root/'untrusted-pythonpath'
        marker = self.root/'hostile-code-ran'
        for directory in (hostile, hostile_path):
            directory.mkdir()
            (directory/'retulo.py').write_text(
                'import os\nfrom pathlib import Path\n'
                'Path(os.environ["RETULO_ATTACK_MARKER"]).write_text("executed")\n'
                'raise SystemExit(95)\n')
        environment = {'PYTHONPATH':str(hostile_path), 'RETULO_ATTACK_MARKER':str(marker)}

        async def connect():
            parameters = StdioServerParameters(command=server['command'], args=server['args'],
                                               cwd=str(hostile), env=environment)
            with open(os.devnull, 'w') as errors:
                async with stdio_client(parameters, errlog=errors) as streams:
                    async with ClientSession(*streams, read_timeout_seconds=10) as client:
                        initialized = await client.initialize()
                        self.assertEqual(initialized.server_info.name, 'retulo')
                        tools = await client.list_tools()
                        self.assertIn('capabilities', [tool.name for tool in tools.tools])

        asyncio.run(connect())
        self.assertFalse(marker.exists())
        hook = subprocess.run(shlex.split(installed['harness']['command']), cwd=hostile,
                              env={**os.environ, **environment}, input=json.dumps({
                                  'hook_event_name':'SessionStart', 'session_id':'hostile-cwd-fixture',
                                  'cwd':str(hostile)}), capture_output=True, text=True, timeout=10)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertEqual(json.loads(hook.stdout)['hookSpecificOutput']['hookEventName'], 'SessionStart')
        self.assertFalse(marker.exists())

    def _upgrade_nonisolated_install(self, managed_mcp):
        server, join = codex._server, shlex.join
        with mock.patch.object(codex, '_server', side_effect=lambda python, config:server(python, config, isolated=False)), \
             mock.patch.object(codex.shlex, 'join', side_effect=lambda tokens:join([token for token in tokens if token != '-I'])):
            installed = codex.install(**self.options, apply=True)
        home = self.options['codex_home']
        before = tomllib.loads((home/'config.toml').read_text())['mcp_servers']['retulo']
        self.assertEqual(before['args'][0], '-m')
        if not managed_mcp:
            receipt = Path(installed['managed_harness_baseline'])
            record = json.loads(receipt.read_bytes())
            record.pop('mcp')
            self.write_historical_harness_receipt(receipt, record)
        preview = codex.install(**self.options)
        self.assertEqual(preview['harness']['hooks']['status'], 'managed_upgrade')
        self.assertEqual(tomllib.loads((home/'config.toml').read_text())['mcp_servers']['retulo'], before)
        upgraded = codex.install(**self.options, apply=True)
        after = tomllib.loads((home/'config.toml').read_text())['mcp_servers']['retulo']
        self.assertEqual(after['command'], before['command'])
        self.assertEqual(after['args'], ['-I', *before['args']])
        self.assertEqual(shlex.split(upgraded['harness']['command'])[1:4], ['-I','-m','retulo'])
        self.assertIsNone(codex.install(**self.options, apply=True)['snapshot'])

    def test_verified_nonisolated_mcp_baseline_upgrades_to_isolated_python(self):
        self._upgrade_nonisolated_install(managed_mcp=True)

    def test_rc2_verified_hook_archive_upgrades_without_mcp_baseline(self):
        self._upgrade_nonisolated_install(managed_mcp=False)

    def test_unowned_nonisolated_mcp_is_not_claimed_during_isolation_upgrade(self):
        home = self.options['codex_home']
        home.mkdir()
        server = codex._server(Path(sys.executable), self.options['engine_config'], isolated=False)
        (home/'config.toml').write_text('[mcp_servers.retulo]\n'+'\n'.join(
            key+' = '+json.dumps(value) for key,value in server.items())+'\n')
        before = self.tree()
        with self.assertRaisesRegex(ValueError, 'Existing retulo MCP differs'):
            codex.install(**self.options, apply=True)
        self.assertEqual(self.tree(), before)

    def test_identical_unowned_mcp_is_not_adopted(self):
        home = self.options['codex_home']
        home.mkdir()
        server = codex._server(Path(sys.executable), self.options['engine_config'])
        (home/'config.toml').write_text('[mcp_servers.retulo]\n'+'\n'.join(
            key+' = '+json.dumps(value) for key,value in server.items())+'\n')
        before = self.tree()
        with self.assertRaises(codex.InstallConflict) as raised:
            codex.install(**self.options, apply=True)
        self.assertEqual(raised.exception.details['target'], 'mcp')
        self.assertEqual(raised.exception.details['reason'], 'baseline_missing')
        self.assertEqual(self.tree(), before)

    def test_identical_unowned_guidance_and_hooks_are_not_adopted(self):
        home = self.options['codex_home']
        home.mkdir()
        template = codex.resources.files('retulo').joinpath('resources','codex','AGENTS.md').read_bytes()
        _, _, hooks, _ = codex.hook_components(None, Path(sys.executable), self.options['engine_config'])
        for target, path, raw in (('agents', home/'AGENTS.md', template), ('hooks', home/'hooks.json', hooks)):
            with self.subTest(target=target):
                path.write_bytes(raw)
                before = self.tree()
                with self.assertRaises(codex.InstallConflict) as raised:
                    codex.install(**self.options, apply=True)
                self.assertEqual(raised.exception.details['target'], target)
                self.assertEqual(raised.exception.details['reason'], 'baseline_missing')
                self.assertEqual(self.tree(), before)
                path.unlink()

    def test_prepared_or_missing_snapshot_never_authorizes_a_noop_retry(self):
        installed = codex.install(**self.options, apply=True)
        manifest = Path(installed['snapshot'])/'manifest.json'
        original = json.loads(manifest.read_bytes())
        for status in ('prepared', 'missing'):
            if status == 'missing':
                manifest.unlink()
            else:
                manifest.write_text(json.dumps({**original, 'status':status}))
            before = self.tree()
            for apply in (False, True):
                with self.subTest(status=status, apply=apply), self.assertRaises(codex.InstallConflict) as raised:
                    codex.install(**self.options, apply=apply)
                self.assertEqual(raised.exception.details['reason'], 'baseline_invalid')
                self.assertEqual(self.tree(), before)

    def test_changed_managed_receipt_must_match_its_completed_manifest(self):
        installed = codex.install(**self.options, apply=True)
        for key in ('managed_baseline', 'managed_harness_baseline'):
            receipt = Path(installed[key])
            original = receipt.read_bytes()
            # Semantically identical bytes still need the recorded execution hash.
            receipt.write_bytes(original+b'\n')
            before = self.tree()
            with self.subTest(key=key), self.assertRaises(codex.InstallConflict) as raised:
                codex.install(**self.options, apply=True)
            self.assertEqual(raised.exception.details['reason'], 'baseline_invalid')
            self.assertEqual(self.tree(), before)
            receipt.write_bytes(original)

    def test_incomplete_snapshot_target_set_cannot_authenticate_managed_receipts(self):
        installed = codex.install(**self.options, apply=True)
        manifest = Path(installed['snapshot'])/'manifest.json'
        record = json.loads(manifest.read_bytes())
        record['files'].pop(0)
        manifest.write_text(json.dumps(record))
        before = self.tree()
        with self.assertRaises(codex.InstallConflict) as raised:
            codex.install(**self.options, apply=True)
        self.assertEqual(raised.exception.details['reason'], 'baseline_invalid')
        self.assertEqual(self.tree(), before)

    def test_foreign_engine_target_with_seven_unique_rows_blocks_noop_setup(self):
        installed = codex.install(**self.options, apply=True)
        manifest = Path(installed['snapshot'])/'manifest.json'
        record = json.loads(manifest.read_bytes())
        engine = next(row for row in record['files'] if row['path'] == str(self.options['engine_config']))
        engine['path'] = str(self.root/'foreign-config.json')
        self.assertEqual(len({row['path'] for row in record['files']}), 7)
        manifest.write_text(json.dumps(record))
        before = self.tree()
        for apply in (False, True):
            with self.subTest(apply=apply), self.assertRaises(codex.InstallConflict) as raised:
                codex.install(**self.options, apply=apply)
            self.assertEqual(raised.exception.details['reason'], 'baseline_invalid')
            self.assertEqual(raised.exception.details['target'], 'installation')
            self.assertEqual(self.tree(), before)

    def test_legacy_script_entrypoints_keep_explicit_source_mode(self):
        checkout = Path(__file__).resolve().parents[1]
        repo = self.root/'synthetic-source'
        (repo/'.venv/bin').mkdir(parents=True)
        (repo/'.venv/bin/python').write_text('synthetic interpreter')
        (repo/'adapters/codex').mkdir(parents=True)
        for name in ('SKILL.md','AGENTS.md'):
            (repo/'adapters/codex'/name).write_bytes(
                codex.resources.files('retulo').joinpath('resources','codex',name).read_bytes())
        command = [sys.executable, str(checkout/'scripts/install_codex.py'), '--apply', '--repo', str(repo)]
        for key in ('codex_home','engine_config','skills_home','workspace_home','snapshot_home'):
            command += ['--'+key.replace('_','-'), str(self.options[key])]
        applied = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stdout+applied.stderr)
        record = json.loads(applied.stdout)
        server = tomllib.loads((self.options['codex_home']/'config.toml').read_text())['mcp_servers']['retulo']
        self.assertEqual(server['command'], str(repo/'.venv/bin/python'))
        command = [sys.executable, str(checkout/'scripts/restore_codex.py'), '--apply', '--snapshot', record['snapshot']]
        for key, value in self.restore_options().items():
            command += ['--'+key.replace('_','-'), str(value)]
        restored = subprocess.run(command, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(restored.returncode, 0, restored.stdout+restored.stderr)
        self.assertEqual(json.loads(restored.stdout)['status'], 'restored')
        self.assertFalse(self.options['engine_config'].exists())

    def test_source_extension_appends_only_requested_roots_and_keeps_custom_state(self):
        config = self.options['engine_config']
        config.parent.mkdir()
        original = dict(schema_version=1, workspace_home=str(self.options['workspace_home']),
                        state_home=str(self.root/'custom-state'),
                        allowed_workspace_roots=[str(self.root/'custom-workspaces')],
                        allowed_source_roots=[str(self.root/'existing')])
        config.write_text(json.dumps(original, indent='\t'))
        requested = [self.root/'existing', self.root/'new', self.root/'new']
        opts = {**self.options, 'source_roots':requested, 'extend_source_roots':True}
        before = self.tree()
        codex.install(**opts)
        self.assertEqual(self.tree(), before)
        codex.install(**opts, apply=True)
        expected = {**original, 'allowed_source_roots':[str(self.root/'existing'), str(self.root/'new')]}
        self.assertEqual(json.loads(config.read_bytes()), expected)
        exact = b'\n'+json.dumps(expected, separators=(',', ':')).encode()+b'\n\n'
        config.write_bytes(exact)
        self.assertIsNone(codex.install(**opts, apply=True)['snapshot'])
        self.assertEqual(config.read_bytes(), exact)
        codex.install(**self.options, extend_source_roots=True, apply=True)
        self.assertEqual(config.read_bytes(), exact)
        with self.assertRaisesRegex(ValueError, 'allowed_source_roots'):
            codex.install(**self.options)

    def test_managed_runtime_upgrade_and_legacy_harness_provenance(self):
        runtime = self.root/'old-python'
        runtime.write_text('synthetic prior interpreter')
        home = self.options['codex_home']
        home.mkdir()
        prefix = b'# Keep exact comment\nmodel="same"\n'
        suffix = b'\n# Keep unrelated table\n[mcp_servers.other]\ncommand="other"\n'
        (home/'config.toml').write_bytes(prefix)
        first = codex.install(**self.options, runtime_python=runtime, apply=True)
        (home/'config.toml').write_bytes((home/'config.toml').read_bytes()+suffix)
        # Earlier releases archived the harness, but did not record MCP metadata.
        managed = Path(first['managed_harness_baseline'])
        record = json.loads(managed.read_bytes())
        record.pop('mcp')
        self.write_historical_harness_receipt(managed, record)
        preview = codex.install(**self.options)
        self.assertEqual(preview['harness']['hooks']['status'], 'managed_upgrade')
        codex.install(**self.options, apply=True)
        raw = (home/'config.toml').read_bytes()
        self.assertTrue(raw.startswith(prefix))
        self.assertTrue(raw.endswith(suffix))
        self.assertEqual(tomllib.loads(raw.decode())['mcp_servers']['retulo']['command'], str(Path(sys.executable).absolute()))

    def test_foreign_or_modified_mcp_entries_cannot_be_claimed(self):
        runtime = self.root/'prior-python'
        runtime.write_text('synthetic')
        home = self.options['codex_home']
        home.mkdir()
        (home/'config.toml').write_text('[mcp_servers.retulo]\ncommand="unowned"\n')
        before = self.tree()
        with self.assertRaisesRegex(ValueError, 'Existing retulo MCP differs'):
            codex.install(**self.options, apply=True)
        self.assertEqual(self.tree(), before)
        (home/'config.toml').unlink()
        codex.install(**self.options, runtime_python=runtime, apply=True)
        config = home/'config.toml'
        config.write_bytes(config.read_bytes().replace(b'tool_timeout_sec = 120', b'tool_timeout_sec = 121'))
        before = self.tree()
        with self.assertRaisesRegex(ValueError, 'Existing retulo MCP differs'):
            codex.install(**self.options, apply=True)
        self.assertEqual(self.tree(), before)

    def test_tampered_managed_mcp_archive_blocks_upgrade(self):
        runtime = self.root/'prior-python'
        runtime.write_text('synthetic')
        installed = codex.install(**self.options, runtime_python=runtime, apply=True)
        baseline = json.loads(Path(installed['managed_harness_baseline']).read_bytes())
        Path(baseline['mcp']['base_copy']).write_text('{}')
        before = self.tree()
        with self.assertRaisesRegex(ValueError, 'Existing retulo MCP differs'):
            codex.install(**self.options, apply=True)
        self.assertEqual(self.tree(), before)

    def test_disconnect_preserves_shared_config_after_another_client_extends_it(self):
        installed = codex.install(**self.options, apply=True)
        config = self.options['engine_config']
        shared = json.loads(config.read_bytes())
        shared['allowed_source_roots'].append(str(self.root/'other-client-project'))
        shared['state_home'] = str(self.root/'shared-state')
        raw = json.dumps(shared, indent=4).encode()
        config.write_bytes(raw)
        artifact = self.options['workspace_home']/'user-result.txt'
        artifact.write_text('retained task output')
        opts = dict(snapshot=installed['snapshot'], **self.restore_options())
        before = self.tree()
        with self.assertRaisesRegex(ValueError, 'changed'):
            codex_restore.restore(**opts, apply=True)
        self.assertEqual(self.tree(), before)
        preview = codex_restore.restore(**opts, preserve_engine_config=True)
        self.assertEqual(preview['status'], 'ready')
        self.assertEqual(self.tree(), before)
        codex_restore.restore(**opts, apply=True, preserve_engine_config=True)
        self.assertEqual(config.read_bytes(), raw)
        self.assertEqual(artifact.read_text(), 'retained task output')
        self.assertFalse((self.options['codex_home']/'config.toml').exists())

    def _two_step_chain(self):
        home = self.options['codex_home']
        home.mkdir()
        (home/'config.toml').write_text('model="original"\n')
        (home/'AGENTS.md').write_text('Original guidance.\n')
        (home/'hooks.json').write_text('{"hooks":{"Stop":[]}}\n')
        runtime = self.root/'old-python'
        runtime.write_text('synthetic')
        first = codex.install(**self.options, runtime_python=runtime, apply=True)
        second = codex.install(**{**self.options, 'source_roots':[self.root/'new-project']},
                               extend_source_roots=True, apply=True)
        return [second['snapshot'], first['snapshot']]

    def test_chain_preview_and_apply_unwind_all_versions_preserving_core(self):
        snapshots = self._two_step_chain()
        before = self.tree()
        config = self.options['engine_config'].read_bytes()
        opts = dict(snapshots=snapshots, **self.restore_options(), preserve_engine_config=True)
        preview = codex_restore.restore_chain(**opts)
        self.assertEqual(preview['status'], 'ready')
        self.assertEqual(self.tree(), before)
        result = codex_restore.restore_chain(**opts, apply=True)
        self.assertEqual(result['status'], 'restored')
        self.assertEqual(self.options['engine_config'].read_bytes(), config)
        self.assertEqual((self.options['codex_home']/'config.toml').read_text(), 'model="original"\n')
        self.assertEqual((self.options['codex_home']/'AGENTS.md').read_text(), 'Original guidance.\n')
        self.assertEqual(codex_restore.restore_chain(**opts)['status'], 'already_at_before_state')

    def test_chain_failure_rolls_back_to_latest_installed_state(self):
        snapshots = self._two_step_chain()
        before = self.tree()
        original = codex.atomic
        calls = 0
        def failing(path, raw, mode=0o600, expected=codex._UNSET):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('injected chain failure')
            return original(path, raw, mode, expected)
        with mock.patch.object(codex, 'atomic', side_effect=failing), self.assertRaisesRegex(OSError, 'chain failure'):
            codex_restore.restore_chain(snapshots, **self.restore_options(), apply=True, preserve_engine_config=True)
        self.assertEqual(self.tree(), before)

    def test_chain_invalid_older_snapshot_is_rejected_before_any_write(self):
        snapshots = self._two_step_chain()
        manifest = Path(snapshots[-1])/'manifest.json'
        record = json.loads(manifest.read_bytes())
        record['files'][0]['after_sha256'] = '0'*64
        manifest.write_text(json.dumps(record))
        before = self.tree()
        with self.assertRaisesRegex(ValueError, 'latest'):
            codex_restore.restore_chain(snapshots, **self.restore_options(), apply=True, preserve_engine_config=True)
        self.assertEqual(self.tree(), before)


class WheelCodexTests(unittest.TestCase):
    def test_noneditable_wheel_setup_works_outside_the_checkout(self):
        # Build from an isolated copy so the check never modifies release outputs.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = Path(__file__).resolve().parents[1]
            build = root/'source-copy'
            build.mkdir()
            shutil.copytree(source/'src', build/'src', ignore=shutil.ignore_patterns('__pycache__','*.egg-info'))
            for name in ('pyproject.toml','README.md','LICENSE','THIRD_PARTY_NOTICES.md'):
                shutil.copy2(source/name, build/name)
            built = subprocess.run([sys.executable,'-m','build','--wheel','--no-isolation','--outdir',str(root/'wheel')],
                                   cwd=build, capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stdout+built.stderr)
            runtime = root/'installed-runtime'
            created = subprocess.run([sys.executable,'-m','venv','--without-pip','--system-site-packages',str(runtime)],
                                     capture_output=True, text=True)
            self.assertEqual(created.returncode, 0, created.stdout+created.stderr)
            python = runtime/'bin/python'
            wheel = next((root/'wheel').glob('retulo-*.whl'))
            installed = subprocess.run([sys.executable,'-m','pip','--python',str(python),'install','--no-index','--no-deps',str(wheel)],
                                       capture_output=True, text=True)
            self.assertEqual(installed.returncode, 0, installed.stdout+installed.stderr)
            shutil.rmtree(build)
            empty = root/'unrelated-cwd'
            empty.mkdir()
            program = '''
import json,sys,tomllib
from pathlib import Path
import retulo
from retulo.integrations import codex,codex_restore
root=Path(sys.argv[1]); runtime=Path(sys.executable)
assert Path(retulo.__file__).is_relative_to(runtime.parent.parent), retulo.__file__
opts=dict(codex_home=root/'client',engine_config=root/'core/config.json',skills_home=root/'skills',workspace_home=root/'workspaces',source_roots=[],snapshot_home=root/'snapshots')
preview=codex.install(**opts)
assert not opts['codex_home'].exists()
installed=codex.install(**opts,apply=True)
server=tomllib.loads((opts['codex_home']/'config.toml').read_text())['mcp_servers']['retulo']
assert server['command']==str(runtime), server
assert (opts['skills_home']/'retulo/SKILL.md').is_file()
assert '.venv' not in server['command']
restore={key:opts[key] for key in ('codex_home','engine_config','skills_home','snapshot_home')}
assert codex_restore.restore(installed['snapshot'],**restore,apply=True,preserve_engine_config=True)['status']=='restored'
assert opts['engine_config'].is_file()
print(json.dumps({'module':retulo.__file__,'python':str(runtime),'ok':True}))
'''
            checked = subprocess.run([str(python),'-I','-c',program,str(root/'fixture')],
                                     cwd=empty, capture_output=True, text=True)
            self.assertEqual(checked.returncode, 0, checked.stdout+checked.stderr)
            self.assertTrue(json.loads(checked.stdout)['ok'])


if __name__ == '__main__':
    unittest.main()
