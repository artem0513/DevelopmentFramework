"""Install owned runtime, skills and agent definitions, backing up changed files."""
from pathlib import Path
import argparse
import datetime
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
import tomllib
import stat
import base64
import hashlib
import copy

ROOT = Path(__file__).resolve().parents[1]
BEGIN = "<!-- autonomous-se managed begin -->"
END = "<!-- autonomous-se managed end -->"
SKILLS_BEGIN = "# autonomous-se project skill selection begin"
SKILLS_END = "# autonomous-se project skill selection end"


def managed_text(current, begin, end, block):
    """Replace one owned block, preserving all surrounding project/user text."""
    if current.count(begin) != current.count(end) or current.count(begin) > 1:
        raise ValueError('Malformed managed block; inspect existing instructions before installation')
    if begin in current:
        before, rest = current.split(begin, 1)
        _, after = rest.split(end, 1)
        return before + block + after
    return current.rstrip() + '\n\n' + block + '\n'


def _is_link(path):
    if path.is_symlink():
        return True
    try:
        # Path.is_junction is unavailable on supported Python 3.11. Windows
        # lstat has exposed the reparse tag since Python 3.8.
        return getattr(path.lstat(), 'st_reparse_tag', None) == getattr(stat, 'IO_REPARSE_TAG_MOUNT_POINT', object())
    except (FileNotFoundError, NotADirectoryError):
        return False


def project_target(project, target):
    """Reject linked targets that would write outside the selected project."""
    target = Path(target)
    if not target.resolve().is_relative_to(project):
        raise ValueError('Project installation path escapes selected project: ' + str(target))
    if _is_link(target):
        raise ValueError('Project installation refuses a linked managed target: ' + str(target))
    for parent in target.parents:
        if parent == project:
            break
        if _is_link(parent):
            raise ValueError('Project installation refuses a linked managed directory: ' + str(parent))
        if parent.exists() and not parent.is_dir():
            raise ValueError('Project installation parent is not a directory: ' + str(parent))
    if target.is_file() and target.stat().st_nlink > 1:
        raise ValueError('Project installation refuses a hardlinked managed file: ' + str(target))
    return target


def _target_type(target, directory):
    target = Path(target)
    if _is_link(target) and (not directory or not target.exists()):
        raise ValueError('Installation refuses a linked managed file or dangling directory: ' + str(target))
    if target.exists() and not (target.is_dir() if directory else target.is_file()):
        raise ValueError(('Expected a directory destination: ' if directory else 'Expected a regular file destination: ') + str(target))
    if not directory and target.is_file() and target.stat().st_nlink > 1:
        raise ValueError('Installation refuses a hardlinked managed file: ' + str(target))
    for parent in target.parents:
        if parent.is_symlink() and not parent.exists():
            raise ValueError('Installation parent is a dangling link: ' + str(parent))
        if parent.exists() and not parent.is_dir():
            raise ValueError('Installation parent is not a directory: ' + str(parent))
    return target


def file_target(target):
    return _target_type(target, directory=False)


def directory_target(target):
    return _target_type(target, directory=True)


def _toml_tokens(text):
    """Lex enough TOML to locate a value without treating strings as syntax."""
    tokens, index = [], 0
    while index < len(text):
        start, char = index, text[index]
        if char in ' \t\r':
            index += 1
            continue
        if char == '#':
            index = text.find('\n', index)
            if index < 0:
                break
            continue
        if char in '\"\'':
            triple = text.startswith(char * 3, index)
            width = 3 if triple else 1
            index += width
            while index < len(text):
                if char == '"' and text[index] == '\\':
                    index += 2
                elif text.startswith(char * width, index):
                    index += width
                    if triple:
                        for _ in range(2):
                            if index < len(text) and text[index] == char:
                                index += 1
                            else:
                                break
                    break
                else:
                    index += 1
            tokens.append(('string', start, index))
        elif char in '\n[]=.,{}':
            tokens.append((char, start, start + 1))
            index += 1
        else:
            while index < len(text) and text[index] not in ' \t\r\n[]=.,{}#\"\'':
                index += 1
            tokens.append(('bare', start, index))
    return tokens


def _toml_key(raw):
    value, keys = tomllib.loads(raw + ' = 0'), []
    while isinstance(value, dict):
        key, value = next(iter(value.items()))
        keys.append(key)
    return tuple(keys)


def _toml_value_end(tokens, start, inline=False):
    first = tokens[start][0]
    if first == 'string':
        return start + 1
    if first in ('[', '{'):
        depth = 0
        for index in range(start, len(tokens)):
            kind = tokens[index][0]
            depth += int(kind in ('[', '{')) - int(kind in (']', '}'))
            if not depth:
                return index + 1
    index = start
    while index < len(tokens) and tokens[index][0] != '\n':
        if inline and tokens[index][0] in (',', '}'):
            break
        index += 1
    return index


def _skill_array_span(text):
    """Find an assigned skills.config array, including inline table ancestry."""
    tokens = _toml_tokens(text)
    wanted = ('skills', 'config')

    def inspect_value(start, end, path):
        if path == wanted and tokens[start][0] == '[':
            return tokens[start][1], tokens[end - 1][1]
        if path != wanted[:len(path)] or tokens[start][0] != '{':
            return None
        index = start + 1
        while index < end - 1:
            if tokens[index][0] in ('\n', ','):
                index += 1
                continue
            equal = index
            while tokens[equal][0] != '=':
                equal += 1
            key = _toml_key(text[tokens[index][1]:tokens[equal][1]].strip())
            stop = _toml_value_end(tokens, equal + 1, inline=True)
            found = inspect_value(equal + 1, stop, path + key)
            if found:
                return found
            index = stop
        return None

    index, table = 0, ()
    while index < len(tokens):
        if tokens[index][0] == '\n':
            index += 1
            continue
        if tokens[index][0] == '[':
            width = 2 if tokens[index + 1][0] == '[' else 1
            close = index + width
            while tokens[close][0] != ']':
                close += 1
            table = _toml_key(text[tokens[index + width][1]:tokens[close][1]].strip())
            index = close + width
            continue
        equal = index
        while tokens[equal][0] != '=':
            equal += 1
        key = _toml_key(text[tokens[index][1]:tokens[equal][1]].strip())
        stop = _toml_value_end(tokens, equal + 1)
        found = inspect_value(equal + 1, stop, table + key)
        if found:
            return found
        index = stop
    return None


def project_skill_config(project, skill_entries):
    """Build validated exact-path opt-ins without replacing custom TOML values."""
    config = file_target(project_target(project, project / '.codex/config.toml'))
    existing = config.read_text(encoding='utf-8') if config.exists() else ''
    try:
        tomllib.loads(existing)
        inline_array = _skill_array_span(existing)
        # Retain only selections previously owned by this installer. Discovering
        # other project folders must not enable a user's intentionally disabled
        # domain skills when adding an engineering/vendor pack.
        previous = []
        if SKILLS_BEGIN in existing and SKILLS_END in existing:
            owned = existing.split(SKILLS_BEGIN, 1)[1].split(SKILLS_END, 1)[0]
            owned_entries = (tomllib.loads('entries = [\n' + owned + '\n]')['entries'] if inline_array
                             else tomllib.loads(owned).get('skills', {}).get('config', []))
            for entry in owned_entries:
                if entry.get('enabled') is True and isinstance(entry.get('path'), str):
                    selected = Path(entry['path'])
                    # Managed absolute selectors are machine-local. A moved
                    # checkout rebuilds them without reading/writing the old
                    # location; only local existing selections carry forward.
                    if selected.is_absolute() and selected.is_relative_to(project):
                        previous.append(project_target(project, selected))
        entries = [SKILLS_BEGIN]
        for path in sorted(set(previous + list(skill_entries))):
            file_target(project_target(project, path))
            encoded = json.dumps(str(path), ensure_ascii=False)
            entries += (['{path = ' + encoded + ', enabled = true},'] if inline_array else
                        ['[[skills.config]]', 'path = ' + encoded, 'enabled = true', ''])
        entries.append(SKILLS_END)
        if inline_array:
            # Replace only our array members. Keep the user's original array,
            # inline parent tables, comments and unrelated TOML source intact.
            base = existing
            if SKILLS_BEGIN in existing:
                managed_text(existing, SKILLS_BEGIN, SKILLS_END, '')  # Validate delimiter counts.
                begin = existing.index(SKILLS_BEGIN)
                end = existing.index(SKILLS_END) + len(SKILLS_END)
                if begin and existing[begin - 1] == '\n':
                    begin -= 1
                if end < len(existing) and existing[end] == '\n':
                    end += 1
                base = existing[:begin] + existing[end:]
            elif SKILLS_END in existing:
                raise ValueError('Malformed managed project skill selection')
            opening, closing = _skill_array_span(base)
            significant = [token for token in _toml_tokens(base)
                           if token[1] > opening and token[2] <= closing and token[0] != '\n']
            if significant and significant[-1][0] != ',':
                offset = significant[-1][2]
                base = base[:offset] + ',' + base[offset:]
                closing += 1
            updated = base[:closing] + '\n' + '\n'.join(entries) + '\n' + base[closing:]
        else:
            updated = managed_text(existing, SKILLS_BEGIN, SKILLS_END, '\n'.join(entries))
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as ex:
        raise ValueError('Project config must be valid TOML before activation: ' + str(ex)) from ex
    return config, updated.encode('utf-8')


TARGETS = {
    'codex': ('.agents/skills', '.codex/agents', 'AGENTS.md'),
    'claude': ('.claude/skills', '.claude/agents', 'CLAUDE.md'),
    'copilot': ('.github/skills', '.github/agents', '.github/copilot-instructions.md'),
}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _stamp():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def _manifest_hash(manifest):
    return _sha(json.dumps({k: v for k, v in manifest.items() if k != 'manifest_sha256'},
                           sort_keys=True, separators=(',', ':')).encode('utf-8'))


def _safe_destination(scope, target):
    """Scope checks include directory aliases, even when they remain in-scope."""
    return file_target(project_target(scope, target))


def _payload(target, data, *, scope, backup_root, index):
    target = _safe_destination(scope, target)
    before = target.read_bytes() if target.exists() else None
    changed = before != data
    backup = backup_root / str(index) / target.name if changed and before is not None else None
    return {'path': str(target), 'scope_root': str(scope), 'sha256': _sha(data),
            'before_sha256': _sha(before) if before is not None else None,
            'content_base64': base64.b64encode(data).decode('ascii'),
            'changed': changed, 'backup': str(backup) if backup else None}


def _routing(skill_root):
    return (BEGIN + '\nThis workspace explicitly adopts DevelopmentFramework for managed tasks. '
            'Use ' + skill_root + '/task-orchestrator/SKILL.md and the appropriate domain skills. '
            'Choose software only for actual code or configuration implementation; documents, financial analysis, '
            'research and infrastructure use their own acceptance profiles. Preserve project rules and user authorization. '
            'Restore local context, propose the outcome concisely, and reuse existing scope approval. '
            'Independent acceptance and review plus the evidence gate are required for verified completion.\n' + END)


def _launcher_bytes(runtime):
    py = str(Path(sys.executable)).replace("'", "''")
    location = str(runtime).replace("'", "''")
    powershell = (f"$env:PYTHONPATH = '{location}' + [IO.Path]::PathSeparator + $env:PYTHONPATH\n"
                  "$aseForward = @()\n"
                  "for ($aseIndex = 0; $aseIndex -lt $args.Count; $aseIndex++) {\n"
                  "  $aseArg = [string]$args[$aseIndex]\n"
                  "  if ($aseArg -match '^--home=[A-Za-z]$' -and $aseIndex + 1 -lt $args.Count -and (([string]$args[$aseIndex + 1]).StartsWith('\\') -or ([string]$args[$aseIndex + 1]).StartsWith('/'))) {\n"
                  "    $aseIndex++; $aseArg = $aseArg + ':' + [string]$args[$aseIndex]\n"
                  "  }\n  $aseForward += $aseArg\n}\n"
                  f"& '{py}' -m ase @aseForward\nexit $LASTEXITCODE\n")
    shell = ('#!/bin/sh\nPYTHONPATH=' + shlex.quote(str(runtime)) + ':"${PYTHONPATH:-}" exec '
             + shlex.quote(sys.executable) + ' -m ase "$@"\n')
    return powershell.encode('utf-8'), shell.encode('utf-8')


def plan_install(targets: list[str], project, *, runtime_home: Path) -> dict:
    """Build a complete read-only preview for explicit portable backend targets.

    ``project`` accepts a registered ProjectContext, an explicit existing path,
    or None for runtime-only preparation. Registration itself is not performed.
    """
    return _plan_install(targets, project, runtime_home=runtime_home)


def _plan_install(targets, project, *, runtime_home, legacy_global=False, skill_home=None):
    if not targets or any(target not in TARGETS for target in targets):
        raise ValueError('Select one or more explicit targets: codex, claude, copilot')
    targets = list(dict.fromkeys(targets))
    home = Path(runtime_home).expanduser().absolute()
    # Explicit root aliases are canonicalized; aliases beneath them are rejected.
    home = home.resolve()
    directory_target(home)
    context = project if project is not None and hasattr(project, 'project_id') and hasattr(project, 'root') else None
    if project is not None:
        project = Path(context.root if context else project).expanduser().resolve(strict=True)
        if not project.is_dir() or any(c in str(project) for c in '\r\n\0'):
            raise ValueError('Supply an existing project directory')
    if project is not None and (home == project or home.is_relative_to(project)):
        raise ValueError('Shared tooling home must be outside the selected project')
    if legacy_global and (project is not None or targets != ['codex']):
        raise ValueError('Legacy global activation is an explicit Codex-only mode')
    runtime = home / 'autonomous-se/runtime'
    stamp = _stamp()
    backup_root = home / 'backups/autonomous-se' / stamp
    planned, seen = [], set()

    def add(target, data, scope=home):
        item = _payload(target, data, scope=scope, backup_root=backup_root, index=len(planned))
        if item['path'] in seen:
            raise ValueError('Duplicate managed installation destination: ' + item['path'])
        if item['backup']:
            _safe_destination(home, Path(item['backup']))
        seen.add(item['path'])
        planned.append(item)

    for directory in ('ase', 'skills', 'agents', 'docs', 'templates', 'scripts'):
        for source in sorted((ROOT / directory).rglob('*')):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                if _is_link(source) or any(_is_link(p) for p in source.parents if p.is_relative_to(ROOT)):
                    raise ValueError('Canonical package contains linked source files')
                add(runtime / source.relative_to(ROOT), source.read_bytes())
    instruction_files, destinations = {}, {}
    for target in targets if project is not None else []:
        skill_relative, role_relative, pointer = TARGETS[target]
        skill_directory = directory_target(project_target(project, project / skill_relative))
        directory_target(project_target(project, project / role_relative))
        destinations[target] = str(skill_directory)
        instruction = project / pointer
        if target == 'codex':
            override = _safe_destination(project, project / 'AGENTS.override.md')
            if override.is_file() and override.read_text(encoding='utf-8').strip():
                instruction = override
        instruction = _safe_destination(project, instruction)
        current = instruction.read_text(encoding='utf-8') if instruction.exists() else ''
        add(instruction, managed_text(current, BEGIN, END, _routing(skill_relative)).encode('utf-8'), project)
        instruction_files[target] = str(instruction)
        entries = []
        for source in sorted((ROOT / 'skills').rglob('*')):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                destination = skill_directory / source.relative_to(ROOT / 'skills')
                add(destination, source.read_bytes(), project)
                if source.name == 'SKILL.md':
                    entries.append(destination)
        roles = ROOT / ('agents' if target == 'codex' else 'templates/agents/' + target)
        for source in sorted(roles.glob('*.toml' if target == 'codex' else '*.md')):
            add(project / role_relative / source.name, source.read_bytes(), project)
        if target == 'codex':
            config, data = project_skill_config(project, entries)
            add(config, data, project)
    if legacy_global:
        destination = Path(skill_home).expanduser().resolve() if skill_home else (Path.home() / '.agents/skills').resolve()
        directory_target(destination)
        destinations['codex'] = str(destination)
        for source in sorted((ROOT / 'skills').rglob('*')):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                add(destination / source.relative_to(ROOT / 'skills'), source.read_bytes(), destination)
        for source in sorted((ROOT / 'agents').glob('*.toml')):
            role = tomllib.loads(source.read_text(encoding='utf-8'))
            if 'developer_instructions' in role:
                role['developer_instructions'] = role['developer_instructions'].replace(
                    '.agents/skills/', destination.as_posix().rstrip('/') + '/')
            content = '\n'.join(key + ' = ' + json.dumps(value) for key, value in role.items()) + '\n'
            add(home / 'agents' / source.name, content.encode('utf-8'))
        instruction = _safe_destination(home, home / 'AGENTS.md')
        current = instruction.read_text(encoding='utf-8') if instruction.exists() else ''
        add(instruction, managed_text(current, BEGIN, END, _routing(str(destination))).encode('utf-8'))
        instruction_files['codex'] = str(instruction)
    ps, shell = _launcher_bytes(runtime)
    add(home / 'bin/ase.ps1', ps)
    add(home / 'bin/ase', shell)
    summary = {'runtime': str(runtime), 'targets': targets,
               'scope': 'global' if legacy_global else 'project' if project is not None else 'runtime',
               'project': str(project) if project is not None else None,
               'project_id': getattr(context, 'project_id', None),
               'skills': destinations.get('codex'), 'skill_destinations': destinations,
               'instruction_file': instruction_files.get('codex'), 'instruction_files': instruction_files,
               'launcher': str(home / 'bin/ase.ps1'), 'remote_push_enabled': False}
    # Stable ownership inventory permits a byte-identical reapplication. Per-run
    # timestamps and backups belong to the preview/result, not this installed file.
    ownership = {**summary, 'owned_files': [{'path': item['path'], 'sha256': item['sha256']} for item in planned]}
    add(home / 'autonomous-se/installation.json', json.dumps(ownership, indent=2).encode('utf-8'))
    manifest = {**summary, 'manifest_version': 1, 'kind': 'install', 'runtime_home': str(home),
                'planned_at': stamp, 'backup': str(backup_root), 'files': planned,
                'changed': [item['path'] for item in planned if item['changed']]}
    manifest['manifest_sha256'] = _manifest_hash(manifest)
    return manifest


def apply_install(manifest: dict) -> dict:
    """Apply exactly a preview, after preflighting every output and backup."""
    if manifest.get('kind') != 'install' or manifest.get('manifest_version') != 1:
        raise ValueError('Unsupported install manifest')
    if manifest.get('manifest_sha256') != _manifest_hash(manifest):
        raise ValueError('Manifest digest mismatch')
    home = Path(manifest['runtime_home'])
    project = Path(manifest['project']) if manifest.get('project') else None
    global_root = Path(manifest['skills']) if manifest.get('scope') == 'global' else None
    allowed_roots = {home, *([project] if project else []), *([global_root] if global_root else [])}
    changes, seen = [], set()
    for item in manifest['files']:
        target, scope = Path(item['path']), Path(item['scope_root'])
        if scope not in allowed_roots or str(target) in seen:
            raise ValueError('Invalid manifest destination scope')
        seen.add(str(target))
        _safe_destination(scope, target)
        data = base64.b64decode(item['content_base64'], validate=True)
        if _sha(data) != item['sha256']:
            raise ValueError('Manifest content hash mismatch')
        before = target.read_bytes() if target.exists() else None
        if (_sha(before) if before is not None else None) != item['before_sha256']:
            raise ValueError('Stale install manifest; destination changed: ' + str(target))
        if item['changed'] != (before != data):
            raise ValueError('Manifest changed-file mismatch')
        backup = Path(item['backup']) if item.get('backup') else None
        if backup:
            _safe_destination(home, backup)
            if not backup.is_relative_to(Path(manifest['backup'])) or backup.exists():
                raise ValueError('Backup destination is already occupied or outside the planned backup')
        if item['changed']:
            changes.append((target, data, backup, scope))
    # Local accident barrier, not a transaction against IO errors or a hostile
    # process changing path objects between checks and writes.
    for target, data, backup, scope in changes:
        _safe_destination(scope, target)
        if backup:
            _safe_destination(home, backup)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if sys.platform != 'win32':
        (home / 'bin/ase').chmod(0o755)
    return {key: value for key, value in manifest.items() if key != 'files'}


def install(home: Path, skill_home: Path = None, *, project: Path = None, global_install=False,
            targets=None):
    """Compatibility entrypoint; new callers can review plan/apply separately."""
    if skill_home is not None and not global_install:
        raise ValueError('A custom skill destination requires explicit global installation')
    manifest = _plan_install(targets or ['codex'], project, runtime_home=home,
                             legacy_global=global_install, skill_home=skill_home)
    result = apply_install(manifest)
    print(json.dumps(result, indent=2))
    return result


def register_verifier(*args, **kwargs):
    raise SystemExit('--register-verifier is deprecated: ASE uses local CLI adapters. '
                     'No configuration was changed. Review an explicit MCP cleanup preview separately.')


def _table_spans(text):
    """Find real TOML table headers; quoted/multiline string contents are ignored."""
    tokens, headers = _toml_tokens(text), []
    index = 0
    while index < len(tokens):
        kind, start, end = tokens[index]
        if kind == '\n':
            index += 1
            continue
        if kind == '[':
            width = 2 if tokens[index + 1][0] == '[' else 1
            close = index + width
            while close < len(tokens) and tokens[close][0] != ']':
                close += 1
            key = _toml_key(text[tokens[index + width][1]:tokens[close][1]].strip())
            headers.append((key, text.rfind('\n', 0, start) + 1))
            index = close + width
        else:
            equal = index
            while tokens[equal][0] != '=':
                equal += 1
            index = _toml_value_end(tokens, equal + 1)
    return [(key, start, headers[index + 1][1] if index + 1 < len(headers) else len(text))
            for index, (key, start) in enumerate(headers)]


def plan_mcp_cleanup(config_path: Path) -> dict:
    """Preview only the historical ASE registration, preserving unrelated bytes."""
    path = Path(config_path).expanduser().absolute()
    root = path.parent.resolve()
    path = _safe_destination(root, root / path.name)
    before = path.read_bytes() if path.exists() else b''
    text = before.decode('utf-8')
    parsed = tomllib.loads(text)
    entry = parsed.get('mcp_servers', {}).get('autonomous_se')
    owned = []
    if entry is not None:
        arguments = entry.get('args', []) if isinstance(entry, dict) else []
        runtime = str(entry.get('env', {}).get('PYTHONPATH', '')).replace('\\', '/').rstrip('/') if isinstance(entry, dict) else ''
        module = any(arguments[index:index + 2] == ['-m', 'ase.host'] for index in range(len(arguments)))
        if not module or not runtime.endswith('/autonomous-se/runtime'):
            raise ValueError('Cannot establish ownership of autonomous_se MCP registration')
        for key, start, end in _table_spans(text):
            if key[:2] != ('mcp_servers', 'autonomous_se'):
                continue
            # Comments between tables are user text, not registration syntax.
            lines = text[start:end].splitlines(keepends=True)
            while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith('#')):
                end -= len(lines.pop())
            owned.append((start, end))
        if not owned:
            raise ValueError('Owned registration uses an unsupported inline shape; no changes made')
    updated = text
    for start, end in reversed(owned):
        updated = updated[:start] + updated[end:]
    expected = copy.deepcopy(parsed)
    if entry is not None:
        del expected['mcp_servers']['autonomous_se']
        if not expected['mcp_servers']:
            del expected['mcp_servers']
    actual = tomllib.loads(updated)
    if actual.get('mcp_servers') == {}:
        actual.pop('mcp_servers')
    if actual != expected:
        raise ValueError('MCP cleanup would modify unrelated settings')
    stamp = _stamp()
    backup = root / 'backups/autonomous-se' / stamp / 'config.before-mcp-cleanup.toml'
    if owned:
        _safe_destination(root, backup)
    manifest = {'kind': 'mcp-cleanup', 'manifest_version': 1, 'config_path': str(path),
                'before_sha256': _sha(before) if path.exists() else None,
                'owned_entry': 'autonomous_se' if owned else None,
                'owned_blocks': [text[start:end] for start, end in owned],
                'changed': bool(owned), 'backup': str(backup) if owned else None,
                'content_base64': base64.b64encode(updated.encode('utf-8')).decode('ascii'),
                'sha256': _sha(updated.encode('utf-8'))}
    manifest['manifest_sha256'] = _manifest_hash(manifest)
    return manifest


def apply_mcp_cleanup(manifest: dict, *, authorization_ref: str) -> dict:
    if not isinstance(authorization_ref, str) or not authorization_ref.strip():
        raise ValueError('Explicit migration authorization reference is required')
    if manifest.get('kind') != 'mcp-cleanup' or manifest.get('manifest_version') != 1:
        raise ValueError('Unsupported cleanup manifest')
    if manifest.get('manifest_sha256') != _manifest_hash(manifest):
        raise ValueError('Manifest digest mismatch')
    path = Path(manifest['config_path'])
    _safe_destination(path.parent, path)
    current = plan_mcp_cleanup(path)
    for key in ('before_sha256', 'sha256', 'owned_blocks', 'owned_entry', 'changed', 'content_base64'):
        if current[key] != manifest[key]:
            raise ValueError('Stale cleanup preview; config changed')
    if manifest['changed']:
        backup = _safe_destination(path.parent, Path(manifest['backup']))
        if not backup.is_relative_to(path.parent / 'backups/autonomous-se') or backup.exists():
            raise ValueError('Invalid or occupied cleanup backup destination')
        data = base64.b64decode(manifest['content_base64'], validate=True)
        if _sha(data) != manifest['sha256']:
            raise ValueError('Cleanup content hash mismatch')
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup)
        path.write_bytes(data)
    return {'changed': manifest['changed'], 'config_path': str(path), 'backup': manifest['backup'],
            'authorization_ref': authorization_ref.strip()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--project', type=Path, help='Explicitly activate one project/workspace')
    mode.add_argument('--runtime-only', action='store_true', help='Prepare shared tooling without routing')
    mode.add_argument('--global', dest='global_install', action='store_true', help='Explicit legacy Codex global activation')
    mode.add_argument('--cleanup-mcp', type=Path, metavar='CONFIG', help='Preview only the owned historical MCP entry')
    mode.add_argument('--apply-manifest', type=Path, help='Apply a previously reviewed install/cleanup JSON manifest')
    parser.add_argument('--target', action='append', choices=TARGETS, help='Backend target; repeat for multiple backends')
    state = Path(os.environ.get('ASE_HOME', Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'AutonomousSE'))
    parser.add_argument('--home', type=Path, default=state / 'tooling', help='Shared tooling destination; independent of provider homes')
    parser.add_argument('--skills', type=Path, help='Legacy global destination; requires --global')
    parser.add_argument('--preview', action='store_true', help='Print manifest without changing files')
    parser.add_argument('--authorization-ref', help='Explicit user authorization source for MCP cleanup')
    parser.add_argument('--register-verifier', action='store_true', help='Deprecated; always fails without writing')
    parser.add_argument('--root', action='append', type=Path, default=[], help=argparse.SUPPRESS)
    parser.add_argument('--state-home', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.register_verifier:
        parser.error('--register-verifier is deprecated; no files or configuration were changed. Use local CLI adapters.')
    if args.apply_manifest:
        manifest = json.loads(args.apply_manifest.read_text(encoding='utf-8'))
        result = (apply_mcp_cleanup(manifest, authorization_ref=args.authorization_ref)
                  if manifest.get('kind') == 'mcp-cleanup' else apply_install(manifest))
    elif args.cleanup_mcp:
        result = plan_mcp_cleanup(args.cleanup_mcp)
    else:
        if not (args.project or args.runtime_only or args.global_install):
            parser.error('Select --project, --runtime-only or explicit legacy --global')
        if args.skills and not args.global_install:
            parser.error('--skills requires --global; use --project for project activation')
        if not args.target and not args.global_install:
            parser.error('Select an explicit --target codex, claude or copilot')
        manifest = _plan_install(args.target or ['codex'], args.project, runtime_home=args.home,
                                 legacy_global=args.global_install, skill_home=args.skills)
        result = manifest if args.preview else apply_install(manifest)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
