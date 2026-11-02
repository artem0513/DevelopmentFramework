# Autonomous SE

A local Codex workflow that records actual execution evidence, uses separate acceptance/review sessions, and refuses unsupported completion or publishing. Python 3.11+ standard library, SQLite, Git and an authenticated Codex CLI. Windows/PowerShell is the primary tested environment.

```powershell
python scripts/install.py
& "$HOME/.codex/bin/ase.ps1" --repo E:/path/to/repo init
```

The installer backs up changed owned files, installs eight discoverable skills under `~/.agents/skills`, three native agent definitions under `~/.codex/agents`, a user runtime/launcher and a short managed global AGENTS pointer. Existing `config.toml` is preserved; `autonomous.config.toml` is an optional separate profile. New skills become available on the next Codex turn/session. Superpowers and .NET test are installed with the current native plugin mechanism; see [installation evidence](docs/validation.md).

Open an existing repository in Codex and provide a normal development request. The global router instructs Codex to inspect the project, establish a task contract, implement, delegate verification/review, execute checks and report the gate. This is model-driven routing: the CLI's explicit input is a confirmed JSON contract, not a deterministic natural-language requirement compiler. See [workflow](docs/workflow.md) and [framework routing](docs/frameworks.md).

From this checkout, the same commands work with `python -m ase`:

```powershell
python -m ase --repo E:/path/to/repo init
# Copy templates/task.json and replace its ACs/argv with this project's real requirements.
python -m ase --repo E:/path/to/repo start E:/path/to/task.json
python -m ase --repo E:/path/to/repo run task-id --parallel
python -m ase --repo E:/path/to/repo resume task-id
python -m ase --repo E:/path/to/repo status task-id
python -m ase --repo E:/path/to/repo check task-id
python -m ase --repo E:/path/to/repo verify task-id --parallel
python -m ase --repo E:/path/to/repo gate task-id
python -m ase --repo E:/path/to/repo report task-id
python -m ase --repo E:/path/to/repo context
python -m ase --repo E:/path/to/repo daily --date 2026-11-01
python -m ase --repo E:/path/to/repo weekly --date 2026-11-01
python -m ase --repo E:/path/to/repo backup
python -m ase --repo E:/path/to/repo publish task-id
```

`--repo` and optional `--home` precede the subcommand. `run` retries at most the contract's `max_attempts` (1–5). `resume --recover` only clears a lock whose owner stopped; investigate source/status before retrying. LOW tasks use fewer relevant checks; every approved task still needs independent AC/review. HIGH requires explicit `approve-high-risk task-id` for the tested fingerprint, plus appropriate broader checks in its reviewed contract.

`gate` exit codes: **0 APPROVED**, **2 REJECTED**, **3 BLOCKED**. `verify` exit zero means verifier processes ran, and may still contain a rejected quality verdict; use `gate` as the publication/CI predicate. Missing tools, zero/skipped required tests, failed builds, stale evidence and P0/P1/P2 reviews cannot approve; P3 remains advisory.

**Automatic verification from a chat:** the installed `autonomous_se` MCP service accepts only `verification_start` and `verification_status` for configured repository roots. The chat implements and executes checks in its workspace sandbox, then the host service launches independent reviewers with `read-only` permissions and records genuine receipts. This avoids the nested CLI startup denial without granting write access to the real Codex home. A fresh ordinary-language chat completed this entire path with APPROVED. See [the fix and evidence](docs/nested-workflow.md).

The service is currently registered for `E:/Tools/DevelopmentFramework` and its disposable child repositories. An authorized bootstrap for another project registers that root with `python scripts/install.py --register-verifier --root <path>`. The installer backs up config, pins the executable/state home and approves only the two verification tools; future tools retain a prompting default. New chats load the service automatically. If a current chat lacks the MCP catalog or a repository is outside the allowlist, the gate remains BLOCKED; sandbox permissions are preserved.

Push is dry-run unless `--execute` is supplied and a human has authorized that repository's exact single remote destination:

```powershell
# Only after explicit user authorization for this repository/remote:
python -m ase --repo E:/path/to/repo authorize --remote origin
python -m ase --repo E:/path/to/repo publish task-id --execute --commit "Implement verified feature"
python -m ase --repo E:/path/to/repo authorize --remote origin --disable
```

Only `feature/*` and `bugfix/*` branches are allowed. Publication rejects pre-existing dirty task baselines, suspicious secrets, changed destinations/source and protected paths; hooks are preserved. It never force-pushes or merges. Optional [pre-push hook](scripts/install_hook.py) provides local assistance. Automatic PR creation is intentionally not enabled; drafts can be prepared by communication-assistant and published through an authorized GitHub workflow.

State defaults to `%LOCALAPPDATA%/AutonomousSE/projects/<canonical-path-hash>` on Windows, `~/.local/share/AutonomousSE/...` elsewhere. Each project has a separate SQLite database, migrations/backups, task contracts, execution logs, receipts and reports. Concise session summaries/decisions and unresolved questions are supported. SQLite does not store raw conversation transcripts. Time distinguishes agent runtime, draft human time and confirmed human time; it never infers billable hours from model elapsed time. `time`, `correct-time` and `session-summary` expose their fields through `--help`.

The SQLite memory stores concise records. Agent execution JSONL logs can include source and tool output needed for troubleshooting: known logs and handoffs expire after 30 days, lazily on the next project open. Set `ASE_LOG_RETENTION_DAYS` to 1–365 to change that bound. Locked tasks are preserved until recovered; expired evidence requires fresh verification. Review artifacts before sharing. The installed launcher captures stable home/state paths and supplies missing home environment variables for nested Windows execution; explicit `--home` takes precedence.

This is an evidence-based reliability layer, **not a security sandbox against another process with your Windows identity**. A same-user agent can edit local code/state/hooks. Actual enforceable publishing requires a pinned trusted CI runner, separate credentials and server-side required checks: [deployment policy](templates/trusted-ci-policy.md). The included GitHub workflow is a cross-platform test template, not proof that a remote CI ran. Git-ignored dependencies/infrastructure are environment inputs outside the source fingerprint; use lockfiles and isolated CI for reproducibility.

Run custom component tests with `python -m unittest discover -s tests -v`. The test suite labels synthetic model adapters as protocol tests. Real model validation and deliberate-defect evidence are separately recorded in [validation](docs/validation.md); [acceptance audit](docs/acceptance-audit.md) records remaining limitations. [ADR](docs/ADR-001.md) explains why the implementation reuses native Codex and selected existing skills. [Research](docs/research.md) covers all requested candidates and official source links.


## Reuse in future projects

[Universal adoption and continuity](docs/universal-template.md) describes task restoration, guarded checkpoints, optional source coverage and skill routing. Repositories and sources start empty; supply them when adopting a project. Templates are worksheets, not permissions or automatic integrations.

[Optional CI diagnosis and historical knowledge](docs/ci-and-knowledge.md) adds scoped gh-fix-ci and dated, source-backed knowledge records with supersession history. Installation chooses no accounts, sources or schedules.

After an in-scope fix, the bounded workflow automatically reruns tests and fresh independent AC/review before checking the gate. P2 now rejects approval. Retry counts persist across resume; completed cycle evidence is retained under task cycles archives. See [workflow](docs/workflow.md).
