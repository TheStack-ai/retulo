import importlib.util
import io
import json
from pathlib import Path
import subprocess
import shlex
import sys
import tempfile
import time
import tomllib
import unittest
from unittest import mock

spec=importlib.util.spec_from_file_location('installer',Path(__file__).resolve().parents[1]/'scripts/install_codex.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

PROCESS_INSTALL = '''
import importlib.util,json,sys,time
from pathlib import Path
spec=importlib.util.spec_from_file_location("installer",sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
options=json.loads(Path(sys.argv[2]).read_text())
marker,release,mode=Path(sys.argv[3]),Path(sys.argv[4]),sys.argv[5]
if mode != "normal":
    original=m.atomic
    def pause(path,raw,mode_bits=0o600,expected=m._UNSET):
        if path==Path(options["snapshot_home"])/"managed-skill.json" and expected is not m._UNSET:
            marker.write_text("inside transaction")
            deadline=time.monotonic()+10
            while not release.exists():
                if time.monotonic()>deadline:raise RuntimeError("test release timeout")
                time.sleep(0.01)
            if mode=="fail":raise OSError("injected managed receipt failure")
        return original(path,raw,mode_bits,expected)
    m.atomic=pause
try:
    print(json.dumps(m.install(**options,apply=True)))
except Exception as exc:
    print(type(exc).__name__+": "+str(exc),file=sys.stderr)
    sys.exit(1)
'''


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        repo=self.root/'repo';(repo/'.venv/bin').mkdir(parents=True)
        (repo/'.venv/bin/python').write_text('test placeholder')
        (repo/'adapters/codex').mkdir(parents=True)
        (repo/'adapters/codex/SKILL.md').write_text('synthetic skill')
        (repo/'adapters/codex/AGENTS.md').write_bytes(
            installer.AGENTS_START+b'\nSynthetic automatic file workflow v1.\n'+installer.AGENTS_END+b'\n')
        self.kw=dict(repo=repo,codex_home=self.root/'codex',engine_config=self.root/'config.json',
            skills_home=self.root/'skills',workspace_home=self.root/'workspaces',source_roots=[],snapshot_home=self.root/'snapshots')

    def seed_legacy(self, snapshot_home=None):
        skill=self.kw['skills_home']/'retulo/SKILL.md';skill.parent.mkdir(parents=True,exist_ok=True)
        skill.write_text('previous shipped default')
        legacy=(snapshot_home or self.kw['snapshot_home'])/'20261101T000000Z';legacy.mkdir(parents=True)
        (legacy/'manifest.json').write_text(json.dumps([{'path':str(skill),'before_sha256':None,
            'after_sha256':installer.digest(skill.read_bytes()),'backup':None}]))
        return skill

    def launch_install(self, name, mode='normal', **overrides):
        options={k:str(v) if isinstance(v,Path) else v for k,v in {**self.kw,**overrides}.items()}
        config=self.root/(name+'-options.json');config.write_text(json.dumps(options))
        marker=self.root/(name+'-inside');release=self.root/(name+'-release')
        process=subprocess.Popen([sys.executable,'-c',PROCESS_INSTALL,str(Path(installer.__file__)),
                                  str(config),str(marker),str(release),mode],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def cleanup():
            if process.poll() is None:
                process.kill();process.communicate(timeout=5)
        self.addCleanup(cleanup)
        return process,marker,release

    def wait_for_marker(self, marker, process):
        deadline=time.monotonic()+5
        while not marker.exists():
            if process.poll() is not None:
                self.fail(f'Installer ended before checkpoint: {process.communicate()}')
            if time.monotonic()>deadline:
                self.fail('Installer did not reach its controlled checkpoint')
            time.sleep(0.01)

    def test_preview_is_readonly_and_apply_preserves_other_settings(self):
        codex=self.kw['codex_home'];codex.mkdir()
        original='model = "unchanged-model"\n[mcp_servers.other]\ncommand="other-command"\n'
        (codex/'config.toml').write_text(original)
        installer.install(**self.kw)
        self.assertFalse(self.kw['engine_config'].exists())
        self.assertEqual((codex/'config.toml').read_text(),original)
        installer.install(**self.kw,apply=True)
        after=tomllib.loads((codex/'config.toml').read_text())
        self.assertEqual(after['model'],'unchanged-model')
        self.assertEqual(after['mcp_servers']['other']['command'],'other-command')
        self.assertIn('retulo',after['mcp_servers'])
        self.assertEqual(json.loads(self.kw['engine_config'].read_text())['allowed_source_roots'],[])
        repeated=installer.install(**self.kw,apply=True)
        self.assertFalse(any(row['changed'] for row in repeated['files']))
        self.assertIsNone(repeated['snapshot'])

    def test_managed_default_upgrades_and_retains_base_bytes(self):
        first=installer.install(**self.kw,apply=True)
        skill=self.kw['skills_home']/'retulo/SKILL.md'
        source=self.kw['repo']/'adapters/codex/SKILL.md'
        settings=self.kw['engine_config']; settings.write_text(json.dumps(json.loads(settings.read_text()),separators=(',',':')))
        settings_before=settings.read_bytes()
        before=set(self.kw['snapshot_home'].rglob('*'))
        source.write_text('new default with compact context')
        preview=installer.install(**self.kw)
        self.assertEqual(preview['skill']['status'],'managed_upgrade')
        self.assertEqual(set(self.kw['snapshot_home'].rglob('*')),before)
        self.assertEqual(skill.read_text(),'synthetic skill')
        upgraded=installer.install(**self.kw,apply=True)
        self.assertEqual(skill.read_bytes(),source.read_bytes())
        self.assertEqual(settings.read_bytes(),settings_before)
        baseline=json.loads(Path(upgraded['managed_baseline']).read_text())
        self.assertEqual(Path(baseline['base_copy']).read_bytes(),source.read_bytes())
        self.assertEqual((Path(first['snapshot'])/'skill-default.md').read_text(),'synthetic skill')
        files=json.loads((Path(upgraded['snapshot'])/'manifest.json').read_text())
        self.assertEqual(files['status'],'complete')
        installed=next(row for row in files['files'] if row['path']==str(skill))
        self.assertEqual(Path(installed['backup']).read_text(),'synthetic skill')
        history=set(self.kw['snapshot_home'].rglob('*'))
        managed_before=Path(upgraded['managed_baseline']).read_bytes()
        self.assertIsNone(installer.install(**self.kw,apply=True)['snapshot'])
        self.assertEqual(set(self.kw['snapshot_home'].rglob('*')),history)
        self.assertEqual(Path(upgraded['managed_baseline']).read_bytes(),managed_before)

    def test_reinstall_preserves_valid_custom_configuration_bytes(self):
        installer.install(**self.kw,apply=True)
        settings=self.kw['engine_config']
        existing=json.loads(settings.read_text())
        existing.update(schema_version=1,state_home=str(self.root/'custom-state'),
                        allowed_workspace_roots=[str(self.kw['workspace_home']),str(self.root/'another-workspace')],
                        allowed_source_roots=[str(self.root/'source-a'),str(self.root/'source-b')],
                        main_brain={'command':'synthetic-brain','args':['--scope','fixture']})
        original=('\n'+json.dumps(existing,indent='\t')+'\n\n').encode()
        settings.write_bytes(original)
        options={**self.kw,'source_roots':existing['allowed_source_roots'],'main_brain':existing['main_brain']}
        (self.kw['repo']/'adapters/codex/SKILL.md').write_text('updated skill')
        for apply in (False,True):
            with self.subTest(apply=apply):
                result=installer.install(**options,apply=apply)
                row=next(row for row in result['files'] if row['path']==str(settings))
                self.assertFalse(row['changed'])
                self.assertEqual(settings.read_bytes(),original)
        self.assertEqual((self.kw['skills_home']/'retulo/SKILL.md').read_text(),'updated skill')

    def test_reinstall_keeps_omitted_optional_config_defaults_omitted(self):
        existing={'workspace_home':str(self.kw['workspace_home']),'allowed_source_roots':[],'main_brain':{}}
        original=json.dumps(existing,separators=(',',':')).encode()
        self.kw['engine_config'].write_bytes(original)
        result=installer.install(**self.kw,main_brain={},apply=True)
        self.assertEqual(self.kw['engine_config'].read_bytes(),original)
        self.assertFalse(next(row for row in result['files'] if row['path']==str(self.kw['engine_config']))['changed'])

    def test_reinstall_conflicting_requested_settings_are_rejected_without_writes(self):
        installer.install(**self.kw,apply=True)
        settings=self.kw['engine_config'];existing=json.loads(settings.read_text())
        existing.update(state_home=str(self.root/'custom-state'),
                        allowed_workspace_roots=[str(self.root/'other-workspace')],
                        allowed_source_roots=[str(self.root/'source')],
                        main_brain={'command':'synthetic-brain'})
        settings.write_text(json.dumps(existing,indent=4))
        options={**self.kw,'source_roots':existing['allowed_source_roots'],'main_brain':existing['main_brain']}
        cases=[({'workspace_home':self.root/'different-home'},'workspace_home'),
               ({'source_roots':[]},'allowed_source_roots'),
               ({'source_roots':existing['allowed_source_roots']+[str(self.root/'extra-source')]},'allowed_source_roots'),
               ({'main_brain':None},'main_brain'),
               ({'main_brain':{'command':'different-brain'}},'main_brain')]
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        for override,field in cases:
            for apply in (False,True):
                with self.subTest(field=field,apply=apply),self.assertRaisesRegex(ValueError,field):
                    installer.install(**{**options,**override},apply=apply)
                self.assertEqual({p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()},before)

    def test_reinstall_rejects_invalid_custom_configuration_before_writes(self):
        original={'workspace_home':str(self.kw['workspace_home']),'allowed_source_roots':[]}
        invalid=[{'schema_version':2},{'state_home':'relative'}, {'allowed_workspace_roots':['relative']},
                 {'unexpected':'value'},{'main_brain':'invalid'}]
        for fields in invalid:
            self.kw['engine_config'].write_text(json.dumps({**original,**fields}))
            before=self.kw['engine_config'].read_bytes()
            for apply in (False,True):
                with self.subTest(fields=fields,apply=apply),self.assertRaises(ValueError):
                    installer.install(**self.kw,apply=apply)
                self.assertEqual(self.kw['engine_config'].read_bytes(),before)
                self.assertFalse(self.kw['codex_home'].exists())
                self.assertFalse(self.kw['snapshot_home'].exists())

    def test_harness_preserves_global_guidance_hooks_order_and_feature_settings(self):
        codex=self.kw['codex_home'];codex.mkdir()
        # Preserve the existing bytes, including non-UTF8 comments and CRLF.
        guidance=b'# Global\r\n<!-- omo-native-workflow:start -->\r\nkeep \xff exact\r\n<!-- omo-native-workflow:end -->'
        (codex/'AGENTS.md').write_bytes(guidance)
        command='printf "%s" "$(never-run) `never-run`" && other-hook --literal "a b" && echo retulo codex-hook'
        existing={'hooks':{event:[{'matcher':'*','hooks':[{'type':'command','command':command}]}]
                           for event in (*installer.HOOK_EVENTS,'Stop','Interrupt')},
                  'custom':{'unrelated':[True,False,{'label':'unchanged'}]}}
        (codex/'hooks.json').write_text(json.dumps(existing))
        config='model="unchanged"\n[features]\nhooks=true\nplugin_hooks=false\ncustom=false\n'
        (codex/'config.toml').write_text(config)
        initial={p:p.read_bytes() for p in codex.iterdir()}
        preview=installer.install(**self.kw)
        self.assertEqual({p:p.read_bytes() for p in codex.iterdir()},initial)
        self.assertFalse(self.kw['snapshot_home'].exists())
        self.assertEqual(preview['harness']['events'],list(installer.HOOK_EVENTS))
        self.assertTrue(preview['harness']['hooks_feature'])
        result=installer.install(**self.kw,apply=True)
        self.assertEqual((codex/'AGENTS.md').read_bytes().split(installer.AGENTS_START)[0],guidance+b'\n')
        updated=json.loads((codex/'hooks.json').read_text())
        self.assertEqual(updated['custom'],existing['custom'])
        for event,groups in existing['hooks'].items():
            self.assertEqual(updated['hooks'][event][:len(groups)],groups)
            self.assertEqual(len(updated['hooks'][event]),len(groups)+(event in installer.HOOK_EVENTS))
        self.assertEqual(tomllib.loads((codex/'config.toml').read_text())['features'],
                         tomllib.loads(config)['features'])
        self.assertEqual(result['harness']['native_hook_trust'],'verify_in_codex')
        after={p:p.read_bytes() for p in (codex/'AGENTS.md',codex/'hooks.json')}
        repeated=installer.install(**self.kw,apply=True)
        self.assertIsNone(repeated['snapshot'])
        self.assertEqual({p:p.read_bytes() for p in after},after)

    def test_harness_upgrade_preserves_prefix_suffix_and_unrelated_hook_edits(self):
        first=installer.install(**self.kw,apply=True)
        codex=self.kw['codex_home'];agents=codex/'AGENTS.md';hooks=codex/'hooks.json'
        prefix=b'custom before\r\n';suffix=b'custom after\r\n'
        agents.write_bytes(prefix+agents.read_bytes()+suffix)
        value=json.loads(hooks.read_text())
        value['hooks']['SessionStart'].insert(0,{'hooks':[{'type':'command','command':'extra-before'}]})
        value['hooks']['SessionStart'].append({'hooks':[{'type':'command','command':'extra-after'}]})
        hooks.write_text(json.dumps(value,separators=(',',':')))
        source=self.kw['repo']/'adapters/codex/AGENTS.md'
        source.write_bytes(source.read_bytes().replace(b'v1',b'v2'))
        upgraded=installer.install(**self.kw,apply=True)
        self.assertEqual(upgraded['harness']['agents']['status'],'managed_upgrade')
        self.assertEqual(upgraded['harness']['hooks']['status'],'unchanged')
        self.assertEqual(agents.read_bytes(),prefix+source.read_bytes()+suffix)
        self.assertEqual(hooks.read_text(),json.dumps(value,separators=(',',':')))
        baseline=json.loads(Path(upgraded['managed_harness_baseline']).read_text())
        self.assertEqual(Path(baseline['components']['agents']['base_copy']).read_bytes(),source.read_bytes().rstrip(b'\n'))
        self.assertEqual((Path(first['snapshot'])/'agents-default.md').read_bytes(),
                         source.read_bytes().replace(b'v2',b'v1').rstrip(b'\n'))

    def test_harness_user_edits_and_deletions_conflict_without_writes(self):
        installer.install(**self.kw,apply=True)
        codex=self.kw['codex_home'];agents=codex/'AGENTS.md';hooks=codex/'hooks.json'
        original_agents=agents.read_bytes();original_hooks=hooks.read_bytes()
        cases=[('agents',original_agents.replace(b'v1',b'custom'),original_hooks),
               ('agents',b'only user guidance',original_hooks),
               ('hooks',original_agents,original_hooks.replace(b'"timeout": 2',b'"timeout": 5')),
               ('hooks',original_agents,b'{"hooks":{}}')]
        for target,agent_bytes,hook_bytes in cases:
            agents.write_bytes(agent_bytes);hooks.write_bytes(hook_bytes)
            before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
            for apply in (False,True):
                with self.subTest(target=target,apply=apply),self.assertRaises(installer.InstallConflict) as caught:
                    installer.install(**self.kw,apply=apply)
                self.assertEqual(caught.exception.details['target'],target)
                self.assertEqual(caught.exception.details['reason'],'user_modified')
            self.assertEqual({p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()},before)

    def test_duplicate_or_malformed_harness_never_replaces_user_content(self):
        installer.install(**self.kw,apply=True)
        codex=self.kw['codex_home'];agents=codex/'AGENTS.md';hooks=codex/'hooks.json'
        original_agents=agents.read_bytes();original_hooks=hooks.read_bytes()
        cases=[(original_agents+original_agents,original_hooks,'invalid_markers'),
               (original_agents.replace(installer.AGENTS_END,b''),original_hooks,'invalid_markers')]
        duplicate=json.loads(original_hooks);duplicate['hooks']['SessionStart']*=2
        cases.append((original_agents,json.dumps(duplicate).encode(),'duplicate_handlers'))
        for agent_bytes,hook_bytes,reason in cases:
            agents.write_bytes(agent_bytes);hooks.write_bytes(hook_bytes)
            with self.subTest(reason=reason),self.assertRaises(installer.InstallConflict) as caught:
                installer.install(**self.kw,apply=True)
            self.assertEqual(caught.exception.details['reason'],reason)
            self.assertEqual(agents.read_bytes(),agent_bytes)
            self.assertEqual(hooks.read_bytes(),hook_bytes)

    def test_harness_baseline_corruption_cannot_bless_current_bytes(self):
        result=installer.install(**self.kw,apply=True)
        receipt=Path(result['managed_harness_baseline']);baseline=json.loads(receipt.read_text())
        Path(baseline['components']['hooks']['base_copy']).write_text('altered')
        for apply in (False,True):
            with self.subTest(apply=apply),self.assertRaises(installer.InstallConflict) as caught:
                installer.install(**self.kw,apply=apply)
            self.assertEqual(caught.exception.details['reason'],'baseline_invalid')

    def test_explicitly_disabled_hooks_are_reported_without_toggling_any_setting(self):
        codex=self.kw['codex_home'];codex.mkdir()
        original=b'[features]\nhooks=false\nplugin_hooks=true\n'
        (codex/'config.toml').write_bytes(original)
        for apply in (False,True):
            with self.subTest(apply=apply),self.assertRaises(installer.InstallConflict) as caught:
                installer.install(**self.kw,apply=apply)
            self.assertEqual(caught.exception.details['reason'],'feature_disabled')
            self.assertEqual((codex/'config.toml').read_bytes(),original)
            self.assertEqual(list(codex.iterdir()),[codex/'config.toml'])
        self.assertFalse(self.kw['snapshot_home'].exists())

    def test_hook_command_quotes_paths_without_changing_arguments(self):
        other_repo=self.root/'repo with spaces $(literal)';self.kw['repo'].rename(other_repo)
        self.kw['repo']=other_repo
        self.kw['engine_config']=self.root/'config with spaces `literal`.json'
        result=installer.install(**self.kw,apply=True)
        self.assertEqual(shlex.split(result['harness']['command']),[
            str(other_repo/'.venv/bin/python'),'-m','retulo','--config',str(self.kw['engine_config']),'codex-hook'])
        for event,groups in json.loads((self.kw['codex_home']/'hooks.json').read_text())['hooks'].items():
            self.assertIn(event,installer.HOOK_EVENTS)
            self.assertEqual(groups[0]['hooks'][0]['command'],result['harness']['command'])

    def test_failure_at_harness_receipt_restores_every_existing_target_and_baseline(self):
        first=installer.install(**self.kw,apply=True)
        paths=[Path(row['path']) for row in first['files']]
        paths.extend([Path(first['managed_baseline']),Path(first['managed_harness_baseline'])])
        before={p:p.read_bytes() for p in paths}
        (self.kw['repo']/'adapters/codex/AGENTS.md').write_bytes(
            installer.AGENTS_START+b'\nnew harness\n'+installer.AGENTS_END+b'\n')
        (self.kw['repo']/'adapters/codex/SKILL.md').write_text('new skill')
        atomic=installer.atomic
        def fail(path,raw,mode=0o600,expected=installer._UNSET):
            if path==Path(first['managed_harness_baseline']) and expected is not installer._UNSET:
                raise OSError('injected harness receipt failure')
            return atomic(path,raw,mode,expected)
        with mock.patch.object(installer,'atomic',side_effect=fail),self.assertRaisesRegex(OSError,'harness receipt'):
            installer.install(**self.kw,apply=True)
        self.assertEqual({p:p.read_bytes() for p in paths},before)
        latest=sorted(self.kw['snapshot_home'].glob('*/manifest.json'))[-1]
        receipt=json.loads(latest.read_text())
        self.assertEqual(receipt['status'],'rolled_back')
        self.assertTrue(installer._verified_rollback(receipt))
        self.assertEqual(set(item['path'] for item in receipt['files']),set(str(p) for p in paths))

    def test_first_harness_install_failure_removes_new_targets_and_restores_originals(self):
        codex=self.kw['codex_home'];codex.mkdir()
        (codex/'AGENTS.md').write_bytes(b'original rules\r\n')
        (codex/'hooks.json').write_bytes(b'{"hooks":{"Stop":[{"hooks":[{"command":"keep-stop"}]}]}}')
        before={p:p.read_bytes() for p in codex.iterdir()}
        atomic=installer.atomic
        def fail(path,raw,mode=0o600,expected=installer._UNSET):
            if path==self.kw['snapshot_home']/'managed-harness.json' and expected is not installer._UNSET:
                raise OSError('new harness failure')
            return atomic(path,raw,mode,expected)
        with mock.patch.object(installer,'atomic',side_effect=fail),self.assertRaisesRegex(OSError,'new harness'):
            installer.install(**self.kw,apply=True)
        self.assertEqual({p:p.read_bytes() for p in before},before)
        for path in (codex/'config.toml',self.kw['engine_config'],
                     self.kw['skills_home']/'retulo/SKILL.md',
                     self.kw['snapshot_home']/'managed-skill.json',self.kw['snapshot_home']/'managed-harness.json'):
            self.assertFalse(path.exists(),str(path))
        receipt=json.loads(next(self.kw['snapshot_home'].glob('*/manifest.json')).read_text())
        self.assertTrue(installer._verified_rollback(receipt))

    def test_each_harness_target_participates_in_cross_process_locks(self):
        lock_script='''
import importlib.util,sys,time
from pathlib import Path
spec=importlib.util.spec_from_file_location("installer",sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
with m.installation_locks([Path(sys.argv[2])]):
    Path(sys.argv[3]).write_text("locked")
    deadline=time.monotonic()+10
    while not Path(sys.argv[4]).exists():
        if time.monotonic()>deadline:raise RuntimeError("release timeout")
        time.sleep(0.01)
'''
        for target in (self.kw['codex_home']/'AGENTS.md',self.kw['codex_home']/'hooks.json',
                       self.kw['snapshot_home']/'managed-harness.json'):
            with self.subTest(target=target.name):
                marker=self.root/(target.name+'-locked');release=self.root/(target.name+'-unlock')
                holder=subprocess.Popen([sys.executable,'-c',lock_script,str(Path(installer.__file__)),
                                         str(target),str(marker),str(release)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                try:
                    self.wait_for_marker(marker,holder)
                    process,_,_=self.launch_install('blocked-'+target.name)
                    time.sleep(0.15)
                    self.assertIsNone(process.poll(),'Installer must wait for the shared harness target')
                    release.write_text('release')
                    _,lock_error=holder.communicate(timeout=5)
                    _,install_error=process.communicate(timeout=5)
                    self.assertEqual(holder.returncode,0,lock_error)
                    self.assertEqual(process.returncode,0,install_error)
                finally:
                    if holder.poll() is None:
                        holder.kill();holder.communicate(timeout=5)

    def test_harness_symlink_destinations_fail_before_mutation(self):
        codex=self.kw['codex_home'];codex.mkdir()
        outside=self.root/'outside-harness';outside.write_text('untouched')
        for filename in ('AGENTS.md','hooks.json'):
            target=codex/filename;target.symlink_to(outside)
            for apply in (False,True):
                with self.subTest(filename=filename,apply=apply),self.assertRaisesRegex(ValueError,'symlink'):
                    installer.install(**self.kw,apply=apply)
            self.assertEqual(outside.read_text(),'untouched')
            self.assertFalse((codex/'config.toml').exists())
            self.assertFalse(self.kw['snapshot_home'].exists())
            target.unlink()

    def test_legacy_receipt_can_upgrade_only_its_latest_known_default(self):
        skill=self.kw['skills_home']/'retulo/SKILL.md';skill.parent.mkdir(parents=True)
        skill.write_text('previous shipped default')
        legacy=self.kw['snapshot_home']/'20261101T000000Z';legacy.mkdir(parents=True)
        (legacy/'manifest.json').write_text(json.dumps([{'path':str(skill),'before_sha256':None,
            'after_sha256':installer.digest(skill.read_bytes()),'backup':None}]))
        result=installer.install(**self.kw,apply=True)
        self.assertEqual(result['skill']['status'],'managed_upgrade')
        self.assertEqual(result['skill']['baseline_provenance'],'legacy_snapshot')
        self.assertEqual(skill.read_text(),'synthetic skill')
        # A user's downgrade to an older known default is not the current managed base.
        skill.write_text('previous shipped default')
        (self.kw['repo']/'adapters/codex/SKILL.md').write_text('next default')
        with self.assertRaises(installer.InstallConflict) as caught:
            installer.install(**self.kw,apply=True)
        self.assertEqual(caught.exception.details['reason'],'user_modified')
        self.assertEqual(skill.read_text(),'previous shipped default')

    def test_user_edits_conflict_with_three_hashes_and_manual_comparison_paths(self):
        installer.install(**self.kw,apply=True)
        skill=self.kw['skills_home']/'retulo/SKILL.md'
        source=self.kw['repo']/'adapters/codex/SKILL.md'
        skill.write_text('user-edited default');source.write_text('next shipped default')
        before={p:p.read_bytes() for p in self.kw['snapshot_home'].rglob('*') if p.is_file()}
        for apply in (False,True):
            with self.subTest(apply=apply),self.assertRaises(installer.InstallConflict) as caught:
                installer.install(**self.kw,apply=apply)
            info=caught.exception.details
            self.assertEqual(info['base_sha256'],installer.digest(b'synthetic skill'))
            self.assertEqual(info['current_sha256'],installer.digest(b'user-edited default'))
            self.assertEqual(info['candidate_sha256'],installer.digest(b'next shipped default'))
            self.assertEqual(info['comparison']['current'],str(skill))
            self.assertEqual(info['comparison']['candidate'],str(source))
            self.assertEqual(Path(info['comparison']['base_copy']).read_text(),'synthetic skill')
        self.assertEqual(skill.read_text(),'user-edited default')
        self.assertEqual({p:p.read_bytes() for p in self.kw['snapshot_home'].rglob('*') if p.is_file()},before)

    def test_corrupt_managed_receipt_does_not_fall_back_to_old_snapshots(self):
        result=installer.install(**self.kw,apply=True)
        managed=Path(result['managed_baseline']);managed.write_text('{broken')
        (self.kw['repo']/'adapters/codex/SKILL.md').write_text('next default')
        with self.assertRaises(installer.InstallConflict) as caught:
            installer.install(**self.kw,apply=True)
        self.assertEqual(caught.exception.details['reason'],'baseline_invalid')
        self.assertIn('baseline_error',caught.exception.details)
        self.assertEqual((self.kw['skills_home']/'retulo/SKILL.md').read_text(),'synthetic skill')
        self.assertEqual(managed.read_text(),'{broken')

    def test_missing_or_corrupt_legacy_receipt_cannot_authorize_upgrade(self):
        skill=self.kw['skills_home']/'retulo/SKILL.md';skill.parent.mkdir(parents=True)
        skill.write_text('old unverified default')
        with self.assertRaises(installer.InstallConflict) as missing:
            installer.install(**self.kw,apply=True)
        self.assertEqual(missing.exception.details['reason'],'baseline_missing')
        legacy=self.kw['snapshot_home']/'20261101T000000Z';legacy.mkdir(parents=True)
        (legacy/'manifest.json').write_text(json.dumps([{'path':str(skill),'after_sha256':'not-a-hash'}]))
        with self.assertRaises(installer.InstallConflict) as corrupt:
            installer.install(**self.kw,apply=True)
        self.assertEqual(corrupt.exception.details['reason'],'baseline_invalid')
        self.assertEqual(skill.read_text(),'old unverified default')
        self.assertFalse(self.kw['engine_config'].exists())

    def test_changed_archived_baseline_is_not_trusted(self):
        result=installer.install(**self.kw,apply=True)
        managed=json.loads(Path(result['managed_baseline']).read_text())
        Path(managed['base_copy']).write_text('altered baseline copy')
        (self.kw['repo']/'adapters/codex/SKILL.md').write_text('next default')
        with self.assertRaises(installer.InstallConflict) as caught:
            installer.install(**self.kw,apply=True)
        self.assertEqual(caught.exception.details['reason'],'baseline_invalid')
        self.assertEqual((self.kw['skills_home']/'retulo/SKILL.md').read_text(),'synthetic skill')

    def test_concurrent_setting_edit_is_preserved_and_prior_write_rolled_back(self):
        codex=self.kw['codex_home'];codex.mkdir()
        original='model="unchanged"\n';(codex/'config.toml').write_text(original)
        real_atomic=installer.atomic
        def racing(path,raw,mode=0o600,expected=installer._UNSET):
            if path==self.kw['engine_config'] and expected is not installer._UNSET:
                path.write_text('concurrent user edit')
            return real_atomic(path,raw,mode,expected)
        with mock.patch.object(installer,'atomic',side_effect=racing),self.assertRaisesRegex(ValueError,'Concurrent'):
            installer.install(**self.kw,apply=True)
        self.assertEqual((codex/'config.toml').read_text(),original)
        self.assertEqual(self.kw['engine_config'].read_text(),'concurrent user edit')
        self.assertFalse((self.kw['skills_home']/'retulo/SKILL.md').exists())
        self.assertFalse((self.kw['snapshot_home']/'managed-skill.json').exists())
        receipt=json.loads(next(self.kw['snapshot_home'].glob('*/manifest.json')).read_text())
        self.assertEqual(receipt['status'],'prepared')
        self.assertNotIn('rollback_verified',receipt)

    def test_legacy_upgrade_failure_records_verified_rollback_and_retry_succeeds(self):
        skill=self.seed_legacy()
        original=installer.atomic
        def failing(path,raw,mode=0o600,expected=installer._UNSET):
            if path==self.kw['snapshot_home']/'managed-skill.json' and expected is not installer._UNSET:
                raise OSError('injected receipt failure')
            return original(path,raw,mode,expected)
        with mock.patch.object(installer,'atomic',side_effect=failing),self.assertRaisesRegex(OSError,'injected'):
            installer.install(**self.kw,apply=True)
        self.assertEqual(skill.read_text(),'previous shipped default')
        failed=[json.loads(p.read_text()) for p in self.kw['snapshot_home'].glob('*/manifest.json')
                if isinstance(json.loads(p.read_text()),dict)]
        self.assertEqual(len(failed),1)
        self.assertEqual(failed[0]['status'],'rolled_back')
        self.assertTrue(installer._verified_rollback(failed[0]))
        self.assertEqual(installer.install(**self.kw,apply=True)['skill']['status'],'managed_upgrade')
        self.assertEqual(skill.read_text(),'synthetic skill')

    def test_prepared_or_unverified_rollback_still_blocks_legacy_upgrade(self):
        skill=self.seed_legacy()
        folder=self.kw['snapshot_home']/'20261102T000000Z';folder.mkdir()
        receipt=folder/'manifest.json'
        for status in ('prepared','rolled_back'):
            receipt.write_text(json.dumps({'schema_version':1,'status':status,'rollback_verified':True,'files':[]}))
            with self.subTest(status=status),self.assertRaises(installer.InstallConflict) as caught:
                installer.install(**self.kw,apply=True)
            self.assertEqual(caught.exception.details['reason'],'baseline_invalid')
        self.assertEqual(skill.read_text(),'previous shipped default')

    def test_two_process_installs_serialize_and_leave_consistent_baseline(self):
        skill=self.seed_legacy()
        first,marker,release=self.launch_install('first',mode='pause')
        self.wait_for_marker(marker,first)
        second,_,_=self.launch_install('second')
        time.sleep(0.15)
        self.assertIsNone(second.poll(),'Second installer must wait while first owns target locks')
        release.write_text('continue')
        out1,err1=first.communicate(timeout=8);out2,err2=second.communicate(timeout=8)
        self.assertEqual((first.returncode,second.returncode),(0,0),(err1,err2))
        self.assertEqual(json.loads(out1)['skill']['status'],'managed_upgrade')
        self.assertEqual(json.loads(out2)['skill']['status'],'unchanged')
        managed=json.loads((self.kw['snapshot_home']/'managed-skill.json').read_text())
        self.assertEqual(managed['sha256'],installer.digest(skill.read_bytes()))
        self.assertEqual(Path(managed['base_copy']).read_bytes(),skill.read_bytes())

    def test_overlapping_targets_lock_even_with_different_snapshot_homes(self):
        skill=self.seed_legacy()
        other=self.root/'other-snapshots';self.seed_legacy(other)
        first,marker,release=self.launch_install('failed',mode='fail')
        self.wait_for_marker(marker,first)
        second,_,_=self.launch_install('other',snapshot_home=other)
        time.sleep(0.15)
        self.assertIsNone(second.poll(),'Different snapshot root must not bypass shared target locks')
        release.write_text('continue')
        _,err1=first.communicate(timeout=8);out2,err2=second.communicate(timeout=8)
        self.assertEqual(first.returncode,1,err1)
        self.assertEqual(second.returncode,0,err2)
        self.assertEqual(json.loads(out2)['skill']['status'],'managed_upgrade')
        managed=json.loads((other/'managed-skill.json').read_text())
        self.assertEqual(managed['sha256'],installer.digest(skill.read_bytes()))
        self.assertEqual(skill.read_text(),'synthetic skill')

    def test_atomic_detects_change_while_preparing_temporary_file(self):
        target=self.root/'race.txt';target.write_bytes(b'before')
        fsync=installer.os.fsync
        def racing(fd):
            target.write_bytes(b'concurrent edit')
            return fsync(fd)
        with mock.patch.object(installer.os,'fsync',side_effect=racing),self.assertRaisesRegex(ValueError,'Concurrent'):
            installer.atomic(target,b'candidate',expected=b'before')
        self.assertEqual(target.read_bytes(),b'concurrent edit')

    def test_symlink_archived_baseline_does_not_read_or_modify_outside(self):
        result=installer.install(**self.kw,apply=True)
        baseline=Path(json.loads(Path(result['managed_baseline']).read_text())['base_copy'])
        outside=self.root/'outside';outside.write_text('untouched')
        baseline.unlink();baseline.symlink_to(outside)
        (self.kw['repo']/'adapters/codex/SKILL.md').write_text('next default')
        with self.assertRaises(installer.InstallConflict) as caught:
            installer.install(**self.kw,apply=True)
        self.assertEqual(caught.exception.details['reason'],'baseline_invalid')
        self.assertEqual(outside.read_text(),'untouched')

    def test_cli_conflict_is_structured_and_does_not_initialize_settings(self):
        skill=self.kw['skills_home']/'retulo/SKILL.md';skill.parent.mkdir(parents=True)
        skill.write_text('unmanaged user skill')
        argv=['install_codex.py']
        for key in ('repo','codex_home','engine_config','skills_home','workspace_home','snapshot_home'):
            argv.extend(['--'+key.replace('_','-'),str(self.kw[key])])
        output=io.StringIO()
        with mock.patch.object(installer.sys,'argv',argv),mock.patch('sys.stdout',output):
            result=installer.main()
        self.assertEqual(result,2)
        response=json.loads(output.getvalue())
        self.assertEqual(response['error']['code'],'skill_conflict')
        self.assertEqual(response['error']['details']['current_sha256'],installer.digest(skill.read_bytes()))
        self.assertFalse(self.kw['engine_config'].exists())

    def test_existing_skill_changes_are_preserved(self):
        skill=self.kw['skills_home']/'retulo/SKILL.md';skill.parent.mkdir(parents=True)
        skill.write_text('user customization')
        with self.assertRaisesRegex(ValueError,'skill differs'):
            installer.install(**self.kw,apply=True)
        self.assertEqual(skill.read_text(),'user customization')
        self.assertFalse(self.kw['engine_config'].exists())

    def test_config_symlink_is_rejected(self):
        outside=self.root/'outside';outside.write_text('{}')
        self.kw['engine_config'].symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'symlink'):
            installer.install(**self.kw,apply=True)
        self.assertEqual(outside.read_text(),'{}')

    def test_parent_settings_symlinks_fail_before_any_existing_target_changes(self):
        codex=self.kw['codex_home'];codex.mkdir()
        original='model = "preserved-model"\n[mcp_servers.existing]\ncommand="preserved"\n'
        original_path=codex/'config.toml';original_path.write_text(original)
        for field in ('engine_config','codex_home','skills_home'):
            with self.subTest(field=field):
                target=self.root/('outside-'+field);target.mkdir()
                sentinel=target/'original.txt';sentinel.write_text('untouched original')
                alias=self.root/('link-'+field);alias.symlink_to(target,target_is_directory=True)
                options=dict(self.kw)
                options[field]=alias/'engine.json' if field=='engine_config' else alias
                for apply in (False,True):
                    with self.subTest(apply=apply),self.assertRaisesRegex(ValueError,'symlink'):
                        installer.install(**options,apply=apply)
                self.assertEqual(original_path.read_text(),original)
                self.assertEqual(list(target.iterdir()),[sentinel])
                self.assertEqual(sentinel.read_text(),'untouched original')
                self.assertFalse(self.kw['engine_config'].exists())
                self.assertFalse((self.kw['skills_home']/'retulo/SKILL.md').exists())
                self.assertFalse(self.kw['snapshot_home'].exists())
                self.assertFalse(self.kw['workspace_home'].exists())

    def test_workspace_and_snapshot_parent_symlinks_fail_before_settings_writes(self):
        for field in ('workspace_home','snapshot_home'):
            with self.subTest(field=field):
                target=self.root/('outside-'+field);target.mkdir()
                alias=self.root/('link-'+field);alias.symlink_to(target,target_is_directory=True)
                options=dict(self.kw);options[field]=alias/'child'
                with self.assertRaisesRegex(ValueError,'symlink'):
                    installer.install(**options,apply=True)
                self.assertFalse(self.kw['engine_config'].exists())
                self.assertFalse(self.kw['codex_home'].exists())
                self.assertFalse(self.kw['skills_home'].exists())
                self.assertFalse(self.kw['snapshot_home'].exists())
                self.assertEqual(list(target.iterdir()),[])


if __name__=='__main__':unittest.main()
