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
                    previous.append(project_target(project, Path(entry['path'])))
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


def install(home: Path, skill_home: Path = None, *, project: Path = None, global_install=False):
    """Prepare tooling; activate routing only for an explicit project/global mode."""
    home = Path(home).resolve()
    if project is not None and global_install:
        raise ValueError('Select project or global installation, not both')
    if skill_home is not None and not global_install:
        raise ValueError('A custom skill destination requires explicit global installation')
    project_writes = []
    instruction_file = None
    if project is not None:
        project = Path(project).resolve(strict=True)
        if not project.is_dir() or any(c in str(project) for c in '\r\n\0'):
            raise ValueError('Supply an existing project directory')
        skill_home = directory_target(project_target(project, project / '.agents/skills'))
        role_home = directory_target(project_target(project, project / '.codex/agents'))
        override = file_target(project_target(project, project / 'AGENTS.override.md'))
        instruction_file = override if override.is_file() and override.read_text(encoding='utf-8').strip() else project / 'AGENTS.md'
        file_target(project_target(project, instruction_file))
        current = instruction_file.read_text(encoding='utf-8') if instruction_file.exists() else ''
        block = (BEGIN + '\nThis project explicitly adopts DevelopmentFramework for code or configuration implementation. '
                 'For those tasks use .agents/skills/task-orchestrator/SKILL.md; delegate independent AC verification and code review '
                 'and use the evidence gate before claiming verified completion or publishing. '
                 'Routine domain analysis, financial reports, documents and questions use their relevant domain workflow; '
                 'they do not activate this engineering process. Preserve project rules and user authorization.\n' + END)
        project_writes.append((instruction_file, managed_text(current, BEGIN, END, block).encode('utf-8')))
        skill_entries = []
        for source in (ROOT / 'skills').rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                target = file_target(project_target(project, skill_home / source.relative_to(ROOT / 'skills')))
                project_writes.append((target, source.read_bytes()))
                if source.name == 'SKILL.md':
                    skill_entries.append(target)
        for source in (ROOT / 'agents').glob('*.toml'):
            project_writes.append((file_target(project_target(project, role_home / source.name)), source.read_bytes()))
        project_writes.append(project_skill_config(project, skill_entries))
    elif global_install:
        skill_home = directory_target(Path(skill_home or Path.home() / '.agents/skills').resolve())
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = home / 'backups' / 'autonomous-se' / stamp
    planned = []

    def write(target, data):
        # Stage known outputs first. A deterministic type conflict anywhere
        # must be reported before runtime or routing files are changed.
        planned.append((file_target(target), data))

    runtime = home / 'autonomous-se' / 'runtime'
    for directory in ('ase', 'skills', 'agents', 'docs', 'templates', 'scripts'):
        for source in (ROOT / directory).rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts:
                write(runtime / source.relative_to(ROOT), source.read_bytes())
    if global_install:
        for source in (ROOT / 'skills').rglob('*'):
            if source.is_file() and '__pycache__' not in source.parts and source.suffix != '.pyc':
                write(skill_home / source.relative_to(ROOT / 'skills'), source.read_bytes())
        for source in (ROOT / 'agents').glob('*.toml'):
            write(home / 'agents' / source.name, source.read_bytes())
    for target, data in project_writes:
        # Recheck immediately before writing as a local accident barrier.
        write(project_target(project, target), data)
    launcher = home / 'bin' / 'ase.ps1'
    py = str(Path(sys.executable)).replace("'", "''")
    location = str(runtime).replace("'", "''")
    actual_user = str(Path.home()).replace("'", "''")
    installed_home = str(home).replace("'", "''")
    state_home = str(Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'AutonomousSE')
    state_ps = state_home.replace("'", "''")
    write(launcher, (f"$env:PYTHONPATH = '{location}' + [IO.Path]::PathSeparator + $env:PYTHONPATH\n"
                     f"if (-not $env:CODEX_HOME) {{ $env:CODEX_HOME = '{installed_home}' }}\n"
                     f"if (-not $env:USERPROFILE) {{ $env:USERPROFILE = '{actual_user}' }}\n"
                     f"if (-not $env:HOME) {{ $env:HOME = '{actual_user}' }}\n"
                     "$aseForward = @()\n"
                     "for ($aseIndex = 0; $aseIndex -lt $args.Count; $aseIndex++) {\n"
                     "  $aseArg = [string]$args[$aseIndex]\n"
                     "  if ($aseArg -match '^--home=[A-Za-z]$' -and $aseIndex + 1 -lt $args.Count -and (([string]$args[$aseIndex + 1]).StartsWith('\\') -or ([string]$args[$aseIndex + 1]).StartsWith('/'))) {\n"
                     "    $aseIndex++; $aseArg = $aseArg + ':' + [string]$args[$aseIndex]\n"
                     "  }\n"
                     "  $aseForward += $aseArg\n"
                     "}\n"
                     "$aseOptions = @()\n"
                     f"if (-not ($aseForward | Where-Object {{ $_ -eq '--home' -or $_ -like '--home=*' }})) {{ $aseOptions = @('--home', '{state_ps}') }}\n"
                     f"& '{py}' -m ase @aseOptions @aseForward\nexit $LASTEXITCODE\n").encode())
    shell_launcher = ('#!/bin/sh\n'
                      'if [ -z "${CODEX_HOME:-}" ]; then export CODEX_HOME=' + shlex.quote(str(home)) + '; fi\n'
                      'if [ -z "${HOME:-}" ]; then export HOME=' + shlex.quote(str(Path.home())) + '; fi\n'
                      'ase_has_home=0\nfor ase_arg do case "$ase_arg" in --home|--home=*) ase_has_home=1;; esac; done\n'
                      'if [ "$ase_has_home" = 0 ]; then set -- --home ' + shlex.quote(state_home) + ' "$@"; fi\n'
                      'PYTHONPATH=' + shlex.quote(str(runtime)) + ':"${PYTHONPATH:-}" exec ' + shlex.quote(sys.executable) + ' -m ase "$@"\n')
    write(home / 'bin' / 'ase', shell_launcher.encode())
    if global_install:
        global_agents = file_target(home / 'AGENTS.md')
        current = global_agents.read_text(encoding='utf-8') if global_agents.exists() else ''
        block = (BEGIN + '\nFor software implementation tasks, use the task-orchestrator skill at '
                 + str(skill_home / 'task-orchestrator' / 'SKILL.md')
                 + '. Delegate independent AC verification and code review; use the evidence gate before claiming verified completion or publishing. '
                 'Keep workflows proportional to risk and preserve repository instructions and user authorization.\n' + END)
        write(global_agents, managed_text(current, BEGIN, END, block).encode('utf-8'))
    # Separate profile; preserve user's model, sandbox and all existing config.
    write(home / 'autonomous.config.toml', b'[agents]\nenabled = true\n')
    changed = [str(target) for target, data in planned if not target.exists() or target.read_bytes() != data]
    manifest = {'installed_at': stamp, 'runtime': str(runtime), 'skills': str(skill_home) if skill_home else None,
                'scope': 'project' if project else 'global' if global_install else 'runtime',
                'project': str(project) if project else None, 'instruction_file': str(instruction_file) if instruction_file else None,
                'backup': str(backup), 'changed': changed,
                'launcher': str(launcher), 'remote_push_enabled': False}
    write(home / 'autonomous-se/installation.json', json.dumps(manifest, indent=2).encode('utf-8'))
    changes = [(target, data) for target, data in planned if not target.exists() or target.read_bytes() != data]
    backups = {}
    for index, (target, _) in enumerate(changes):
        if target.exists():
            backups[target] = file_target(project_target(home, backup / str(index) / target.name))
    # No directory creation or writes occur before all known destinations and
    # backup file paths have passed preflight. IO failures/races are not a
    # transactional rollback guarantee against a hostile same-user process.
    for target, data in changes:
        file_target(target)
        if target in backups:
            saved = file_target(backups[target])
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    if sys.platform != 'win32':
        (home / 'bin' / 'ase').chmod(0o755)
    print(json.dumps(manifest, indent=2))


def register_verifier(home, roots, state_home):
    """Register a scoped MCP host via Codex's supported configuration command."""
    codex = shutil.which('codex')
    if not codex:
        raise SystemExit('Codex executable unavailable; verifier host was not registered')
    roots = [Path(root).resolve(strict=True) for root in roots]
    if not roots or any(not root.is_dir() for root in roots):
        raise SystemExit('Supply existing --root directories for the verification host')
    runtime = home / 'autonomous-se/runtime'
    if not (runtime / 'ase/host.py').is_file():
        raise SystemExit('Verification runtime unavailable; run --runtime-only or --project before registration')
    config = home / 'config.toml'
    if config.exists():
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup = home / 'backups/autonomous-se' / stamp / 'config.before-verifier.toml'
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(config, backup)
    command = [codex, 'mcp', 'add', 'autonomous_se', '--env', 'PYTHONPATH=' + str(runtime),
               '--env', 'CODEX_HOME=' + str(home), '--', sys.executable, '-P', '-m', 'ase.host',
               '--home', str(state_home.resolve()), '--codex', str(Path(codex).resolve())]
    for root in roots:
        command += ['--root', str(root)]
    subprocess.run(command, check=True, env={**os.environ, 'CODEX_HOME': str(home)})
    # The user explicitly authorizes automatic independent verification. Grant
    # only these two scoped tools; preserve sandbox/approval policy elsewhere.
    text = config.read_text(encoding='utf-8')
    server_header = '[mcp_servers.autonomous_se]'
    server_pattern = re.compile(r'(?m)^' + re.escape(server_header) + r'\s*\n(?:(?!^\[).*(?:\n|$))*')
    server_match = server_pattern.search(text)
    if not server_match:
        raise SystemExit('Native MCP registration did not create the expected server table')
    section = server_match.group()
    for key, value in [('enabled_tools', '["verification_start", "verification_status"]'),
                       ('default_tools_approval_mode', '"prompt"')]:
        if re.search(r'(?m)^' + key + r'\s*=', section):
            section = re.sub(r'(?m)^' + key + r'\s*=.*$', key + ' = ' + value, section)
        else:
            section = section.rstrip() + '\n' + key + ' = ' + value + '\n'
    text = text[:server_match.start()] + section + text[server_match.end():]
    for tool in ('verification_start', 'verification_status'):
        header = '[mcp_servers.autonomous_se.tools.' + tool + ']'
        pattern = re.compile(r'(?m)^' + re.escape(header) + r'\s*\n(?:(?!^\[).*(?:\n|$))*')
        match = pattern.search(text)
        if match:
            section = match.group()
            if re.search(r'(?m)^approval_mode\s*=', section):
                updated = re.sub(r'(?m)^approval_mode\s*=.*$', 'approval_mode = "approve"', section)
            else:
                updated = section.rstrip() + '\napproval_mode = "approve"\n'
            text = text[:match.start()] + updated + text[match.end():]
        else:
            text = text.rstrip() + '\n\n' + header + '\napproval_mode = "approve"\n'
    config.write_text(text, encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--project', type=Path, help='Explicitly activate owned skills and routing in one existing project')
    mode.add_argument('--runtime-only', action='store_true', help='Prepare shared tooling without activating any skills or routing')
    mode.add_argument('--global', dest='global_install', action='store_true', help='Explicit legacy global skills and routing installation')
    parser.add_argument('--home', type=Path, default=Path(os.environ.get('CODEX_HOME', Path.home() / '.codex')))
    parser.add_argument('--skills', type=Path, help='Legacy global skill destination; requires --global')
    parser.add_argument('--register-verifier', action='store_true', help='Install the verification-only MCP host for explicit roots')
    parser.add_argument('--root', action='append', type=Path, default=[])
    parser.add_argument('--state-home', type=Path, default=Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'AutonomousSE')
    args = parser.parse_args(argv)
    if args.register_verifier and not args.root:
        parser.error('--register-verifier requires at least one --root')
    if not (args.project or args.runtime_only or args.global_install or args.register_verifier):
        parser.error('Select --project, --runtime-only, --global, or verifier-only registration')
    if args.skills and not args.global_install:
        parser.error('--skills requires --global; use --project for project activation')
    if args.project or args.runtime_only or args.global_install:
        install(args.home.resolve(), args.skills.resolve() if args.skills else None,
                project=args.project, global_install=args.global_install)
    if args.register_verifier:
        register_verifier(args.home.resolve(), args.root, args.state_home)


if __name__ == '__main__':
    main()
