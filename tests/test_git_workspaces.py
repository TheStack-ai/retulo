import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retulo.engine import Engine
from retulo.fs import EngineError
from retulo.git_workspaces import GitWorkspaces
from retulo import git_workspaces as module


class GitWorkspacesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.repo = self.base / 'source'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Fixture')
        (self.repo / 'file').write_text('one')
        self.git('add', 'file')
        self.git('commit', '-m', 'initial')
        self.managed = self.base / 'trees'
        self.managed.mkdir()
        self.engine = Engine(self.base / 'tasks', allowed_source_roots=[self.repo])
        self.engine.allowed_git_worktree_roots = [self.managed]
        self.task = self.engine.begin_task('start', 'Git task', 'Test', source_root=str(self.repo))
        self.id = self.task['task_id']
        self.api = GitWorkspaces(self.engine)
        self.tree = self.managed / '한글 work\ntree'

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args, path=None):
        return subprocess.run(['git', '-C', str(path or self.repo), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.decode().strip()

    def create(self):
        return self.api.create_worktree(self.id, 'create', 0, str(self.repo), str(self.tree), 'feature', 'HEAD')

    def error(self, code, function, *args):
        with self.assertRaises(EngineError) as caught:
            function(*args)
        self.assertEqual(code, caught.exception.code)

    def test_read_only_unbound_does_not_create_sidecar(self):
        self.assertEqual('unbound', self.api.status(self.id)['status'])
        self.assertFalse((Path(self.task['workspace_path']) / '.workspace/git.json').exists())

    def test_bind_keeps_core_source_and_revision(self):
        bound = self.api.bind(self.id, 'bind', 0, str(self.repo))
        self.assertEqual(1, bound['git_revision'])
        self.assertEqual('observed', self.api.status(self.id)['status'])
        core = self.engine.get_context(self.id, detail='full')['state']
        self.assertEqual(str(self.repo), core['source_root'])
        self.assertEqual(1, core['revision'])
        self.assertTrue(self.api.bind(self.id, 'bind', 0, str(self.repo))['replayed'])
        self.error('idempotency_conflict', self.api.bind, self.id, 'bind', 1, str(self.tree))

    def test_create_integrate_and_remove_preserves_branch(self):
        self.create()
        self.assertTrue(self.api.create_worktree(self.id, 'create', 0, str(self.repo), str(self.tree), 'feature', 'HEAD')['replayed'])
        (self.tree / 'file').write_text('two')
        self.git('add', 'file', path=self.tree)
        self.git('commit', '-m', 'feature', path=self.tree)
        removal = self.api.prepare_remove(self.id, str(self.repo))
        self.assertFalse(removal['eligible'])
        self.error('git_not_integrated', self.api.remove_worktree, self.id, 'remove-too-soon', 1, removal)
        plan = self.api.prepare_integration(self.id, str(self.repo))
        self.assertTrue(plan['eligible'])
        integrated = self.api.integrate(self.id, 'integrate', 1, plan)
        self.assertEqual(2, integrated['git_revision'])
        self.assertEqual('two', (self.repo / 'file').read_text())
        self.assertTrue(self.tree.exists())
        removal = self.api.prepare_remove(self.id, str(self.repo))
        self.api.remove_worktree(self.id, 'remove', 2, removal)
        self.assertFalse(self.tree.exists())
        self.assertEqual('removed', self.api.status(self.id)['status'])
        self.assertEqual(self.git('rev-parse', 'main'), self.git('rev-parse', 'feature'))
        self.assertTrue(self.api.remove_worktree(self.id, 'remove', 2, removal)['replayed'])

    def test_mutation_revisions_and_path_boundaries(self):
        self.error('outside_scope', self.api.create_worktree, self.id, 'bad-path', 0, str(self.repo), str(self.base / 'escape'), 'feature')
        self.create()
        self.error('git_revision_conflict', self.api.bind, self.id, 'another', 0, str(self.repo))
        self.error('git_already_bound', self.api.bind, self.id, 'another', 1, str(self.repo))

    def test_changed_plan_is_refused(self):
        self.create()
        plan = self.api.prepare_integration(self.id, str(self.repo))
        (self.tree / 'new').write_text('new')
        self.git('add', 'new', path=self.tree)
        self.git('commit', '-m', 'new', path=self.tree)
        self.error('git_plan_stale', self.api.integrate, self.id, 'apply', 1, plan)
        self.assertIsNone(self.api.status(self.id)['pending'])

    def test_ignored_untracked_locked_and_closed_tasks(self):
        self.create()
        (self.tree / '.gitignore').write_text('secret\n')
        self.git('add', '.gitignore', path=self.tree)
        self.git('commit', '-m', 'ignore', path=self.tree)
        (self.tree / 'secret').write_text('preserve')
        self.error('git_unsafe_state', self.api.prepare_integration, self.id, str(self.repo))
        (self.tree / 'secret').unlink()
        self.git('worktree', 'lock', str(self.tree))
        self.error('git_unsafe_state', self.api.prepare_integration, self.id, str(self.repo))
        self.git('worktree', 'unlock', str(self.tree))
        self.engine.checkpoint(self.id, 'pause', 1, {'status':'paused'})
        self.error('task_not_active', self.api.bind, self.id, 'bind2', 1, str(self.repo))

    def test_bind_does_not_allow_remove_unmanaged(self):
        self.git('worktree', 'add', '-b', 'other', str(self.tree))
        self.engine.allowed_source_roots.append(self.managed)
        self.api.bind(self.id, 'bind', 0, str(self.tree))
        self.error('git_not_managed', self.api.prepare_remove, self.id, str(self.repo))

    def test_missing_worktree_is_unknown(self):
        self.create()
        self.git('worktree', 'remove', str(self.tree))
        self.assertEqual('unknown', self.api.status(self.id)['status'])

    def test_create_recovers_after_git_succeeded_receipt_failed(self):
        real = module.atomic_json
        def fail_receipt(path, state):
            if state['git_revision'] == 1:
                raise OSError('simulated crash')
            return real(path, state)
        with patch.object(module, 'atomic_json', side_effect=fail_receipt):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual('recovery_needed', self.api.status(self.id)['status'])
        recovered = self.create()
        self.assertEqual(1, recovered['git_revision'])
        self.assertEqual(2, len(self.api.backend.list_worktrees(self.repo)))
        self.assertEqual('observed', self.api.status(self.id)['status'])

    def test_remove_recovers_after_git_succeeded_receipt_failed(self):
        self.create()
        plan = self.api.prepare_remove(self.id, str(self.repo))
        real = module.atomic_json
        def fail_receipt(path, state):
            if state['git_revision'] == 2:
                raise OSError('simulated crash')
            return real(path, state)
        with patch.object(module, 'atomic_json', side_effect=fail_receipt):
            with self.assertRaises(OSError):
                self.api.remove_worktree(self.id, 'remove', 1, plan)
        self.assertFalse(self.tree.exists())
        result = self.api.remove_worktree(self.id, 'remove', 1, plan)
        self.assertTrue(result['recovered'])
        self.assertEqual('removed', self.api.status(self.id)['status'])

    def test_pending_other_operation_is_refused_and_unknown_schema_fails(self):
        real = module.atomic_json
        def fail_receipt(path, state):
            if state['git_revision'] == 1:
                raise OSError('crash')
            return real(path, state)
        with patch.object(module, 'atomic_json', side_effect=fail_receipt):
            with self.assertRaises(OSError):
                self.create()
        self.error('git_recovery_needed', self.api.bind, self.id, 'unrelated', 0, str(self.repo))
        sidecar = Path(self.task['workspace_path']) / '.workspace/git.json'
        state = json.loads(sidecar.read_text())
        state['schema_version'] = 99
        sidecar.write_text(json.dumps(state))
        self.error('invalid_git_state', self.api.status, self.id)

    def test_invalid_or_existing_branch_fails_before_intent(self):
        self.error('git_branch_exists', self.api.create_worktree, self.id, 'existing', 0,
                   str(self.repo), str(self.tree), 'main', 'HEAD')
        self.assertIsNone(self.api.status(self.id)['pending'])
        self.create()

    def test_explicit_abandon_only_when_no_git_effect(self):
        with patch.object(self.api.backend, 'create_worktree', side_effect=EngineError('git_timeout', 'timeout before execution')):
            self.error('git_timeout', self.create)
        self.error('git_recovery_needed', self.api.assert_completable, self.id)
        result = self.api.abandon_pending(self.id, 'abandon', 0, 'create')
        self.assertTrue(result['abandoned'])
        self.assertIsNone(self.api.status(self.id)['pending'])
        self.api.assert_completable(self.id)
        self.assertTrue(self.create()['abandoned'])
        made = self.api.create_worktree(self.id, 'new-create', 1, str(self.repo), str(self.tree), 'feature', 'HEAD')
        self.assertEqual(2, made['git_revision'])

    def test_abandon_refuses_partial_creation(self):
        real = self.api.backend.create_worktree
        def partial(*args):
            real(*args)
            raise EngineError('git_timeout', 'after create')
        with patch.object(self.api.backend, 'create_worktree', side_effect=partial):
            self.error('git_timeout', self.create)
        with self.assertRaises(EngineError):
            self.api.abandon_pending(self.id, 'abandon', 0, 'create')
        self.assertTrue(self.tree.exists())
        self.assertEqual('recovery_needed', self.api.status(self.id)['status'])
        self.create()
        self.assertEqual('observed', self.api.status(self.id)['status'])


if __name__ == '__main__':
    unittest.main()
