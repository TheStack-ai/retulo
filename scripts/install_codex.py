#!/usr/bin/env python3
"""Guarded local installation of the Codex MCP, skill and automatic workspace harness."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import tempfile
import time
import tomllib


_UNSET = object()
AGENTS_START = b'<!-- retulo:start -->'
AGENTS_END = b'<!-- retulo:end -->'
HOOK_EVENTS = ('SessionStart', 'UserPromptSubmit', 'SubagentStart')
HOOK_STATUS = 'Retulo'
HARNESS_VERSION = 1


class InstallConflict(ValueError):
    """A reviewable managed-file conflict, reported without a traceback."""
    def __init__(self, message, details):
        self.details = details
        super().__init__(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def reject_symlink_path(path):
    """Match runtime config loading: the destination and every parent must be real paths."""
    for node in (path, *path.parents):
        if node.is_symlink():
            raise ValueError(f'Refusing symlink settings path: {node.name}')


def installation_lock_path(target):
    """The lock identity follows the target, even when installers use different snapshot roots."""
    target = Path(os.path.abspath(Path(target).expanduser()))
    key = digest(str(target).encode())[:24]
    return target.parent/f'.retulo-install-{key}.lock'


@contextmanager
def installation_locks(targets, create=True):
    """Serialize the whole read/commit/rollback transaction for overlapping targets."""
    if os.name != 'posix':
        raise ValueError('Installer process locking is only qualified on local POSIX filesystems')
    import fcntl
    locks = sorted({installation_lock_path(target) for target in targets})
    descriptors = []
    deadline = time.monotonic()+30
    try:
        for path in locks:
            reject_symlink_path(path)
            if create:
                path.parent.mkdir(parents=True, exist_ok=True)
                reject_symlink_path(path)
                fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            else:
                try:
                    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                except FileNotFoundError:
                    continue
            descriptors.append(fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Installer lock is not a regular file')
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError('Another installer is still using these targets; retry after it finishes')
                    time.sleep(0.025)
        yield
    finally:
        for fd in reversed(descriptors):
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        # Never unlink lock files: waiting processes must keep sharing the same inode.


def atomic(path, raw, mode=0o600, expected=_UNSET):
    reject_symlink_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    reject_symlink_path(path)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        reject_symlink_path(path)
        if expected is not _UNSET:
            current = path.read_bytes() if path.exists() else None
            if current != expected:
                raise ValueError('Concurrent settings change; installation cancelled')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


def _json_record(path):
    reject_symlink_path(path)
    if not path.is_file() or path.stat().st_size > 1048576:
        raise ValueError('Invalid or oversized installer receipt')
    return json.loads(path.read_text())


def _verified_rollback(record):
    files, checks = record.get('files'), record.get('rollback_checks')
    if (record.get('rollback_verified') is not True or not isinstance(files,list) or not files or
            not isinstance(checks,list) or len(files) != len(checks)):
        return False
    expected = {}
    for item in files:
        if (not isinstance(item,dict) or not isinstance(item.get('path'),str) or item['path'] in expected or
                ('before_sha256' not in item) or
                (item['before_sha256'] is not None and not _sha(item['before_sha256']))):
            return False
        expected[item['path']] = item['before_sha256']
    observed = {}
    for item in checks:
        if not isinstance(item,dict) or item.get('path') not in expected or item['path'] in observed or 'sha256' not in item:
            return False
        observed[item['path']] = item['sha256']
    return observed == expected


def skill_baseline(snapshot_home, skill_path):
    """Read our newest installed default. Never bless a hash merely found in old history."""
    managed = snapshot_home/'managed-skill.json'
    reject_symlink_path(managed)
    if managed.exists():
        try:
            record = _json_record(managed)
            if (not isinstance(record, dict) or type(record.get('schema_version')) is not int or record['schema_version'] != 1 or
                    record.get('path') != str(skill_path) or not _sha(record.get('sha256'))):
                raise ValueError('Invalid managed skill identity')
            archived = Path(record['base_copy'])
            if not archived.is_absolute() or '..' in archived.parts or not archived.is_relative_to(snapshot_home):
                raise ValueError('Managed skill baseline is outside installer snapshots')
            reject_symlink_path(archived)
            if not archived.is_file() or archived.stat().st_size > 1048576 or digest(archived.read_bytes()) != record['sha256']:
                raise ValueError('Managed skill baseline bytes do not match receipt')
            return {'sha256':record['sha256'], 'receipt':str(managed), 'base_copy':str(archived),
                    'provenance':'managed_baseline'}, None
        except (ValueError, KeyError, TypeError, OSError) as exc:
            return None, f'Invalid managed skill receipt: {exc}'
    if not snapshot_home.exists():
        return None, None
    # v0.1 snapshots were lists written before install. Matching their planned
    # installed hash proves exact default bytes, not an authenticated execution.
    for directory in sorted(snapshot_home.iterdir(), key=lambda p:p.name, reverse=True):
        if directory.name == 'managed-skill.json' or not directory.is_dir():
            continue
        receipt = directory/'manifest.json'
        if not receipt.exists():
            continue
        try:
            record = _json_record(receipt)
            if isinstance(record, dict):
                if type(record.get('schema_version')) is not int or record['schema_version'] != 1:
                    raise ValueError('Invalid installer snapshot schema')
                if record.get('status') == 'rolled_back':
                    if not _verified_rollback(record):
                        raise ValueError('Installer rollback is unverified')
                    continue
                if record.get('status') != 'complete':
                    raise ValueError('Installer snapshot is incomplete')
                record = record.get('files')
            if not isinstance(record, list):
                raise ValueError('Invalid installer snapshot shape')
            matches = [row for row in record if isinstance(row, dict) and row.get('path') == str(skill_path)]
            if not matches:
                continue
            if len(matches) != 1 or not _sha(matches[0].get('after_sha256')):
                raise ValueError('Invalid installed skill hash')
            return {'sha256':matches[0]['after_sha256'], 'receipt':str(receipt), 'base_copy':None,
                    'provenance':'legacy_snapshot'}, None
        except (ValueError, TypeError, OSError) as exc:
            return None, f'Invalid installer snapshot: {exc}'
    return None, None


def skill_update_plan(snapshot_home, skill_path, skill_source, current, candidate):
    baseline, problem = skill_baseline(snapshot_home, skill_path)
    current_sha = digest(current) if current is not None else None
    candidate_sha = digest(candidate)
    details = {'base_sha256':baseline['sha256'] if baseline else None,
               'current_sha256':current_sha, 'candidate_sha256':candidate_sha,
               'comparison':{'base_receipt':baseline['receipt'] if baseline else str(snapshot_home/'managed-skill.json'),
                             'base_copy':baseline['base_copy'] if baseline else None,
                             'current':str(skill_path), 'candidate':str(skill_source)},
               'baseline_provenance':baseline['provenance'] if baseline else None}
    if problem:
        details['baseline_error'] = problem
    if current is None:
        details['status'] = 'new'
    elif current == candidate:
        details['status'] = 'unchanged'
    elif baseline and current_sha == baseline['sha256']:
        details['status'] = 'managed_upgrade'
    else:
        details['status'] = 'conflict'
        details['reason'] = 'baseline_invalid' if problem else 'user_modified' if baseline else 'baseline_missing'
        raise InstallConflict('Installed skill differs; refusing to overwrite user edits or an unverified baseline', details)
    return details


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def harness_baseline(snapshot_home, paths):
    managed = snapshot_home/'managed-harness.json'
    reject_symlink_path(managed)
    if not managed.exists():
        return None, None
    try:
        record = _json_record(managed)
        if (not isinstance(record, dict) or type(record.get('schema_version')) is not int or
                record['schema_version'] != 1 or type(record.get('harness_version')) is not int or
                not 1 <= record['harness_version'] <= HARNESS_VERSION or set(record.get('components', {})) != set(paths)):
            raise ValueError('Invalid managed harness identity')
        for name, path in paths.items():
            item = record['components'][name]
            if not isinstance(item, dict) or item.get('path') != str(path) or not _sha(item.get('sha256')):
                raise ValueError('Invalid managed harness component')
            archived = Path(item['base_copy'])
            if not archived.is_absolute() or '..' in archived.parts or not archived.is_relative_to(snapshot_home):
                raise ValueError('Managed harness baseline is outside installer snapshots')
            reject_symlink_path(archived)
            if not archived.is_file() or archived.stat().st_size > 1048576 or digest(archived.read_bytes()) != item['sha256']:
                raise ValueError('Managed harness baseline bytes do not match receipt')
        return record, None
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return None, f'Invalid managed harness receipt: {exc}'


def harness_component_plan(name, path, candidate_source, current, candidate, baseline, problem, snapshot_home):
    base = baseline['components'][name] if baseline else None
    details = {'target':name, 'base_sha256':base['sha256'] if base else None,
               'current_sha256':digest(current) if current is not None else None,
               'candidate_sha256':digest(candidate),
               'comparison':{'base_receipt':str(snapshot_home/'managed-harness.json'),
                             'base_copy':base['base_copy'] if base else None,
                             'current':str(path), 'candidate':str(candidate_source)}}
    if problem:
        details.update(status='conflict', reason='baseline_invalid', baseline_error=problem)
    elif current == candidate:
        details['status'] = 'unchanged'
    elif current is None and base is None:
        details['status'] = 'new'
    elif base and current is not None and digest(current) == base['sha256']:
        details['status'] = 'managed_upgrade'
    else:
        details.update(status='conflict', reason='user_modified' if base else 'baseline_missing')
    if details['status'] == 'conflict':
        raise InstallConflict(f'Managed {name} differs; refusing to overwrite user edits or an unverified baseline', details)
    return details


def agents_component(raw):
    """Only bytes between our two exact markers belong to this installer."""
    raw = raw or b''
    starts, ends = raw.count(AGENTS_START), raw.count(AGENTS_END)
    if starts == ends == 0:
        return None, None
    start, end = raw.find(AGENTS_START), raw.find(AGENTS_END)
    if starts != 1 or ends != 1 or end < start:
        raise InstallConflict('Malformed or duplicate retulo AGENTS markers',
                              {'target':'agents', 'status':'conflict', 'reason':'invalid_markers'})
    end += len(AGENTS_END)
    return raw[start:end], (start, end)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key in hooks configuration')
        result[key] = value
    return result


def _managed_hook_group(group):
    if not isinstance(group, dict) or not isinstance(group.get('hooks'), list):
        return False
    for handler in group['hooks']:
        if not isinstance(handler, dict):
            continue
        if handler.get('statusMessage') == HOOK_STATUS:
            return True
        try:
            tokens = shlex.split(handler.get('command', ''))
        except (ValueError, TypeError):
            continue
        if (len(tokens) >= 6 and tokens[1:3] == ['-m', 'retulo'] and
                tokens[3] == '--config' and tokens[-1] == 'codex-hook'):
            return True
    return False


def hook_components(raw, python, engine_config):
    value = json.loads(raw, object_pairs_hook=_unique_object) if raw is not None else {}
    if not isinstance(value, dict) or not isinstance(value.get('hooks', {}), dict):
        raise ValueError('Hooks configuration must contain an object of event handlers')
    events = value.get('hooks', {})
    for name, groups in events.items():
        if not isinstance(groups, list):
            raise ValueError(f'Hook event {name} must contain a list')
    command = shlex.join([str(python), '-m', 'retulo', '--config', str(engine_config), 'codex-hook'])
    group = {'hooks':[{'type':'command', 'command':command, 'timeout':2,
                       'statusMessage':HOOK_STATUS, 'additionalContextLimit':1200}]}
    candidate = {name:[group] for name in HOOK_EVENTS}
    owned = {name:[item for item in groups if _managed_hook_group(item)] for name, groups in events.items()}
    owned = {name:groups for name, groups in owned.items() if groups}
    # Preserve every unrelated group and its index; a managed replacement stays
    # in place, while new handlers append after existing entries.
    updated = json.loads(json.dumps(value))
    updated_events = updated.setdefault('hooks', {})
    for name in dict.fromkeys((*owned, *HOOK_EVENTS)):
        groups = updated_events.setdefault(name, [])
        positions = [i for i, item in enumerate(groups) if _managed_hook_group(item)]
        if positions:
            if len(positions) != 1:
                raise InstallConflict('Duplicate retulo hook groups',
                                      {'target':'hooks', 'status':'conflict', 'reason':'duplicate_handlers'})
            if name in candidate:
                groups[positions[0]] = candidate[name][0]
            else:
                # No migration silently removes an old managed event.
                raise InstallConflict('Unexpected retulo hook event',
                                      {'target':'hooks', 'status':'conflict', 'reason':'unexpected_event'})
        elif name in candidate:
            groups.append(candidate[name][0])
    after = raw if value == updated else (json.dumps(updated, ensure_ascii=False, indent=2)+'\n').encode()
    return (_canonical(owned) if owned else None, _canonical(candidate), after, command)


def _install_unlocked(repo, codex_home, engine_config, skills_home, workspace_home, source_roots,
                      snapshot_home, main_brain=None, apply=False):
    repo, codex_home, engine_config, skills_home, workspace_home, snapshot_home = [
        Path(p).expanduser().absolute() for p in
        (repo, codex_home, engine_config, skills_home, workspace_home, snapshot_home)]
    python = repo/'.venv/bin/python'
    skill_source = repo/'adapters/codex/SKILL.md'
    agents_source = repo/'adapters/codex/AGENTS.md'
    if not python.is_file() or not skill_source.is_file() or not agents_source.is_file():
        raise ValueError('Install dependencies and build the Codex skill and harness templates first')
    codex_config = codex_home/'config.toml'
    skill_path = skills_home/'retulo/SKILL.md'
    agents_path, hooks_path = codex_home/'AGENTS.md', codex_home/'hooks.json'
    # Reject unsupported parent links before taking backups or touching any settings.
    # Otherwise installation can report success while runtime load_config rejects it.
    for p in (codex_config, engine_config, skill_path, skill_source, agents_path, hooks_path,
              agents_source, workspace_home, snapshot_home):
        reject_symlink_path(p)
    original = codex_config.read_bytes() if codex_config.exists() else None
    before = original or b''
    parsed = tomllib.loads(before.decode())
    hooks_enabled = parsed.get('features', {}).get('hooks')
    if hooks_enabled is False:
        raise InstallConflict('Codex hooks are explicitly disabled; enable them deliberately before installing the harness',
                              {'target':'hooks', 'status':'conflict', 'reason':'feature_disabled',
                               'path':str(codex_config), 'setting':'features.hooks', 'current':False})
    server = {'command': str(python), 'args': ['-m','retulo','--config',str(engine_config),'mcp'],
              'startup_timeout_sec': 20, 'tool_timeout_sec': 120}
    existing = parsed.get('mcp_servers',{}).get('retulo')
    if existing is not None and existing != server:
        raise ValueError('Existing retulo MCP differs; review it instead of overwriting')
    addition = '\n[mcp_servers.retulo]\n' + '\n'.join(
        key+' = '+json.dumps(value, ensure_ascii=False) for key,value in server.items())+'\n'
    after = before if existing else before + addition.encode()
    new = tomllib.loads(after.decode())
    restored = json.loads(json.dumps(new))
    restored.get('mcp_servers',{}).pop('retulo',None)
    expected = json.loads(json.dumps(parsed))
    expected.get('mcp_servers',{}).pop('retulo',None)
    if not expected.get('mcp_servers'):
        expected.pop('mcp_servers',None)
    if not restored.get('mcp_servers'):
        restored.pop('mcp_servers',None)
    if restored != expected:
        raise ValueError('Installation would alter unrelated Codex configuration')
    settings = {'workspace_home':str(workspace_home),
                'state_home':str(workspace_home/'.retulo'),
                'allowed_workspace_roots':[str(workspace_home)],
                'allowed_source_roots':[str(Path(p).expanduser().resolve()) for p in source_roots]}
    if main_brain is not None:
        settings['main_brain'] = main_brain
    settings_old = engine_config.read_bytes() if engine_config.exists() else None
    settings_bytes = (json.dumps(settings,ensure_ascii=False,indent=2)+'\n').encode()
    if settings_old is not None:
        from retulo.cli import validate_config
        if len(settings_old) > 65536:
            raise ValueError('Existing engine configuration exceeds 65536 bytes')
        current_settings = validate_config(json.loads(settings_old.decode('utf-8')))
        requested_settings = validate_config(settings)
        # Reinstall only compares settings the caller actually supplies. Valid
        # state storage, workspace allowlists and schema metadata belong to the
        # existing configuration, not to the installer's new-install defaults.
        for field in ('workspace_home', 'allowed_source_roots', 'main_brain'):
            if current_settings.get(field) != requested_settings.get(field):
                raise ValueError(f'Existing engine configuration differs for {field}; refusing to overwrite')
        # Preserve every existing byte, including formatting and optional fields.
        settings_bytes = settings_old
    if skill_source.stat().st_size > 1048576:
        raise ValueError('Codex skill exceeds 1048576 bytes')
    skill_bytes = skill_source.read_bytes()
    skill_old = skill_path.read_bytes() if skill_path.exists() else None
    skill_plan = skill_update_plan(snapshot_home, skill_path, skill_source, skill_old, skill_bytes)
    if agents_source.stat().st_size > 1048576:
        raise ValueError('Codex harness guidance exceeds 1048576 bytes')
    agents_bytes = agents_source.read_bytes().rstrip(b'\r\n')
    template_block, template_bounds = agents_component(agents_bytes)
    if template_block != agents_bytes or template_bounds != (0, len(agents_bytes)):
        raise ValueError('Codex harness template must consist of one managed block')
    agents_old = agents_path.read_bytes() if agents_path.exists() else None
    agents_current, bounds = agents_component(agents_old)
    if bounds:
        agents_new = agents_old[:bounds[0]] + agents_bytes + agents_old[bounds[1]:]
    else:
        prefix = agents_old or b''
        separator = b'' if not prefix or prefix.endswith(b'\n') else b'\n'
        agents_new = prefix + separator + agents_bytes + b'\n'
    hooks_old = hooks_path.read_bytes() if hooks_path.exists() else None
    hooks_current, hooks_bytes, hooks_new, hook_command = hook_components(hooks_old, python, engine_config)
    harness_paths = {'agents':agents_path, 'hooks':hooks_path}
    harness_base, harness_problem = harness_baseline(snapshot_home, harness_paths)
    harness_plans = {
        'agents':harness_component_plan('agents', agents_path, agents_source, agents_current,
                                       agents_bytes, harness_base, harness_problem, snapshot_home),
        'hooks':harness_component_plan('hooks', hooks_path, 'generated:codex-hook', hooks_current,
                                      hooks_bytes, harness_base, harness_problem, snapshot_home)}
    outputs = [(codex_config,original,after),(engine_config,settings_old,settings_bytes),
               (skill_path,skill_old,skill_bytes),(agents_path,agents_old,agents_new),
               (hooks_path,hooks_old,hooks_new)]
    result = {'apply':apply,'workspace_home':str(workspace_home),
              'files':[{'path':str(p),'changed':old!=fresh} for p,old,fresh in outputs],
              'skill':skill_plan, 'harness':{'version':HARNESS_VERSION, **harness_plans,
                  'events':list(HOOK_EVENTS), 'command':hook_command,
                  'hooks_feature':hooks_enabled if hooks_enabled is not None else 'unspecified',
                  'native_hook_trust':'verify_in_codex'},
              'models_preserved':True,'hooks_preserved':True,
              'unrelated_hooks_preserved':True,'github_publication':False}
    if not apply:
        return result
    if not any(old != fresh for _,old,fresh in outputs) and harness_base is not None:
        workspace_home.mkdir(parents=True, exist_ok=True)
        result['snapshot'] = None
        return result
    snapshot = snapshot_home/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    managed = snapshot_home/'managed-skill.json'
    reject_symlink_path(managed)
    managed_old = managed.read_bytes() if managed.exists() else None
    baseline_copy = snapshot/'skill-default.md'
    managed_new = (json.dumps({'schema_version':1,'path':str(skill_path),'sha256':digest(skill_bytes),
                              'base_copy':str(baseline_copy)},indent=2)+'\n').encode()
    outputs.append((managed,managed_old,managed_new))
    harness_managed = snapshot_home/'managed-harness.json'
    reject_symlink_path(harness_managed)
    harness_managed_old = harness_managed.read_bytes() if harness_managed.exists() else None
    harness_copies = {'agents':(snapshot/'agents-default.md',agents_bytes),
                      'hooks':(snapshot/'hooks-default.json',hooks_bytes)}
    harness_record = {'schema_version':1, 'harness_version':HARNESS_VERSION,
                      'components':{name:{'path':str(harness_paths[name]), 'sha256':digest(raw), 'base_copy':str(path)}
                                    for name, (path, raw) in harness_copies.items()}}
    harness_managed_new = (json.dumps(harness_record,indent=2)+'\n').encode()
    outputs.append((harness_managed,harness_managed_old,harness_managed_new))
    for p,old,_ in outputs:
        reject_symlink_path(p)
        current = p.read_bytes() if p.exists() else None
        if current != old:
            raise ValueError('Concurrent settings change; installation cancelled')
    snapshot.mkdir(parents=True, mode=0o700)
    manifest=[]
    for i,(p,old,fresh) in enumerate(outputs):
        backup=snapshot/f'{i:02d}.before'
        if old is not None:
            atomic(backup,old)
        manifest.append({'path':str(p),'before_sha256':digest(old) if old is not None else None,
            'after_sha256':digest(fresh),'backup':str(backup) if old is not None else None})
    atomic(baseline_copy,skill_bytes)
    for path, raw in harness_copies.values():
        atomic(path,raw)
    receipt={'schema_version':1,'status':'prepared','files':manifest}
    atomic(snapshot/'manifest.json',(json.dumps(receipt,indent=2)+'\n').encode())
    written=[]
    try:
        for p,old,fresh in outputs:
            reject_symlink_path(p)
            current=p.read_bytes() if p.exists() else None
            if current != old:
                raise ValueError('Concurrent settings change; installation cancelled')
            if old != fresh:
                mode=p.stat().st_mode & 0o777 if p.exists() else 0o600
                atomic(p,fresh,mode,expected=old); written.append((p,old,fresh,mode))
        reject_symlink_path(workspace_home)
        workspace_home.mkdir(parents=True,exist_ok=True)
        receipt['status']='complete'
        atomic(snapshot/'manifest.json',(json.dumps(receipt,indent=2)+'\n').encode())
    except Exception as exc:
        for p,old,fresh,mode in reversed(written):
            try:
                reject_symlink_path(p)
                if p.is_file() and p.read_bytes()==fresh:
                    if old is None: p.unlink()
                    else: atomic(p,old,mode,expected=fresh)
            except (ValueError, OSError) as rollback_error:
                # A late user edit must not stop rollback of earlier unchanged writes.
                exc.add_note(f'Rollback left {p} for review: {rollback_error}')
        try:
            fully_restored = True
            rollback_checks = []
            for p,old,_ in outputs:
                reject_symlink_path(p)
                current = p.read_bytes() if p.exists() else None
                if current != old:
                    fully_restored = False
                rollback_checks.append({'path':str(p),'sha256':digest(current) if current is not None else None})
            if fully_restored:
                receipt.update(status='rolled_back',rollback_verified=True,rollback_checks=rollback_checks)
                atomic(snapshot/'manifest.json',(json.dumps(receipt,indent=2)+'\n').encode())
        except (ValueError, OSError) as verification_error:
            exc.add_note(f'Rollback verification remains incomplete: {verification_error}')
        raise
    result['snapshot']=str(snapshot)
    result['managed_baseline']=str(managed)
    result['managed_harness_baseline']=str(harness_managed)
    return result


def install(repo, codex_home, engine_config, skills_home, workspace_home, source_roots,
            snapshot_home, main_brain=None, apply=False):
    options = dict(repo=repo,codex_home=codex_home,engine_config=engine_config,
                   skills_home=skills_home,workspace_home=workspace_home,source_roots=source_roots,
                   snapshot_home=snapshot_home,main_brain=main_brain)
    targets = [Path(codex_home).expanduser()/'config.toml',Path(engine_config).expanduser(),
               Path(skills_home).expanduser()/'retulo/SKILL.md',
               Path(codex_home).expanduser()/'AGENTS.md',Path(codex_home).expanduser()/'hooks.json',
               Path(snapshot_home).expanduser()/'managed-skill.json',
               Path(snapshot_home).expanduser()/'managed-harness.json']
    # Wait on already-existing writer locks without creating any file or folder.
    # Invalid paths/configuration and user-edit conflicts still fail before lock creation.
    with installation_locks(targets,create=False):
        preview = _install_unlocked(**options,apply=False)
    if not apply:
        return preview
    with installation_locks(targets):
        # Preflight may be stale after waiting. Baselines and every target are read
        # again under all target locks, held through any rollback verification.
        return _install_unlocked(**options,apply=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--apply',action='store_true')
    p.add_argument('--repo',default=str(Path(__file__).resolve().parents[1]))
    p.add_argument('--codex-home',default=str(Path.home()/'.codex'))
    p.add_argument('--engine-config',default=str(Path.home()/'.config/retulo/config.json'))
    p.add_argument('--skills-home',default=str(Path.home()/'.agents/skills'))
    p.add_argument('--workspace-home',default=str(Path.home()/'Documents/RetuloWorkspaces'))
    p.add_argument('--source-root',action='append',default=[])
    p.add_argument('--snapshot-home',default=str(Path.home()/'.local/state/retulo/install'))
    p.add_argument('--main-brain-config',help='JSON file with command/args/scope; never credentials')
    a=p.parse_args(); opts=vars(a)
    config=opts.pop('main_brain_config')
    opts['main_brain']=json.loads(Path(config).read_text()) if config else None
    opts['source_roots']=opts.pop('source_root')
    try:
        print(json.dumps(install(**opts),ensure_ascii=False,indent=2))
    except InstallConflict as exc:
        print(json.dumps({'ok':False,'error':{'code':'harness_conflict' if exc.details.get('target') else 'skill_conflict','message':str(exc),
                                           'details':exc.details}},ensure_ascii=False,indent=2))
        return 2
    return 0


if __name__=='__main__':
    sys.exit(main())
