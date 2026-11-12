# Everything in the project folder

Keep the framework checkout anywhere available, or place its files in a folder alongside your application sources. From the framework checkout initialize the application's directory once:

```powershell
python scripts/install.py --project D:/Work/MyProject --project-local --target codex --target claude --target copilot --timezone Europe/Bucharest
```

Python3.11+ and the selected bot are required. Remove unwanted targets; descriptors do not install a CLI or provide a subscription. Initialization is scoped to that directory. No global skills, provider permissions or account credentials are installed/migrated. For a read-only review first, add `--preview`, save the JSON, then apply with `python scripts/install.py --apply-manifest MANIFEST.json`. Existing instructions/settings/comments remain; changed owned files have private backups. Known conflicts and stale previews fail before writes. This is local reliability, not an IO transaction or isolation from another hostile process with the same OS identity.

## Layout

```text
MyProject/
  application sources...
  .devframework/
    config.toml                  project settings, no secrets
    .gitignore                   excludes /local/
    bin/ase.ps1                  PowerShell launcher
    bin/ase                      shell launcher
    autonomous-se/runtime/       self-contained framework runtime
    local/                       private, excluded from Git
      registry.sqlite3
      projects/<project-id>/     SQLite memory, journal, checks, receipts
      credentials/               encrypted vault, only if explicitly used
      backups/                   installation backups
  .agents/skills, .codex/agents   Codex-specific project files
  .claude/skills, .claude/agents  Claude-specific project files
  .github/skills, .github/agents  Copilot-specific project files
```

Native instruction files select project skills and the same local launcher. Nonsecret config/runtime/skills can be versioned; `.devframework/local` must remain ignored. Installer/runtime reject tracked private state, missing/ineffective ignore protection, linked managed paths and using one project's local home for another project. Generic discovery, source packets and fingerprints cannot treat private state as application source.

## Everyday commands

```powershell
.\.devframework\bin\ase.ps1 status
.\.devframework\bin\ase.ps1 project register --root . --kind software
.\.devframework\bin\ase.ps1 worklog --from 2026-11-12 --until 2026-11-13 --timezone Europe/Bucharest
.\.devframework\bin\ase.ps1 meeting-report --from 2026-11-12 --until 2026-11-13 --timezone Europe/Bucharest
```

Use the shell launcher analogously. Dates are examples; `--until` is exclusive. Journal entries reflect real task/check/session records; installation does not invent history. New registrations default to config timezone. Existing registered metadata/history remains unchanged; report commands can override reporting timezone.

Subdirectories find the nearest local configuration. An absolute local launcher path selects its own project even from another directory. Explicit `--repo`, `--project PATH` and `--home` remain available. `--project UUID` is a key within the selected registry, never a reason to fall back silently to global state. State selection is explicit `--home` > selected local config > `ASE_HOME` > platform default. Each project has independent registry/UUIDs, memory, journal and vault; its three bots share that state. Existing shared memory is not copied automatically.

The strict config has `schema_version=1`, `mode="project-local"`, unique selected `targets`, IANA `timezone`, and `log_retention_days` from1 to365. The private path is fixed. Local retention overrides the global retention environment default. Native provider and third-party session caches follow their own tools' policies. Optional credential import still requires a designated file outside source/Git; the protected local vault remains encrypted with Windows DPAPI, with unsupported OSes blocked rather than writing plaintext.

## Updates, clones and moves

Reapply the same local command from current framework sources to update. An installed runtime also contains its installer at `.devframework/autonomous-se/runtime/scripts/install.py`. Reinitialize after cloning to refresh machine-local absolute native skill selectors. Launchers find runtime relatively and use an available Python3.11+ interpreter.

Git clones exclude private history. For another computer use explicit reviewed export/import; secrets are excluded and DPAPI ciphertext remains tied to its user/machine. Moving a folder containing registered private state requires explicit reattachment/recovery and fresh affected evidence, not invented registry identity or replayed approvals. No MCP/daemon is needed. Independent sessions retain clean external OS temporary working directories; persistent captures/receipts remain inside the project's private state.
