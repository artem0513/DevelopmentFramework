# Autonomous SE

A local Codex workflow that records actual execution evidence, uses separate acceptance/review sessions, and refuses unsupported completion or publishing. Python 3.11+ standard library, SQLite, Git and an authenticated Codex CLI. Windows/PowerShell is the primary tested environment.

```powershell
python scripts/install.py --project E:/path/to/repo
& "$HOME/.codex/bin/ase.ps1" --repo E:/path/to/repo init
```

This checkout is a passive template. Installation requires an explicit mode. `--project <repo>` installs nine owned skills in that repository's `.agents/skills`, three native roles in `.codex/agents`, exact local skill-enable entries in `.codex/config.toml`, and a managed project instruction block. Existing project content is preserved and changed owned files are backed up. Shared runtime/launcher preparation does not activate engineering in other projects. `--runtime-only` prepares tooling without routing; legacy global activation requires explicit `--global`. See [project adoption](docs/project-adoption.md).

Ask the assistant to copy/adapt this template for a named software project when needed. The opted-in project's local routing covers actual code/config changes and their verification. Financial analysis, interpreting data-quality warnings and ordinary document work follow their domain instructions. Merely opening another project does not adopt it. Routing remains model-driven; the CLI takes a confirmed JSON contract. See [workflow](docs/workflow.md) and [framework routing](docs/frameworks.md).

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

Verifier roots are an explicit host allowlist, separate from project adoption. After shared runtime preparation, an authorized bootstrap can register a root with `python scripts/install.py --register-verifier --root <path>`. Registration alone does not copy skills or create routing. It backs up config and approves only the two verification tools; future tools retain a prompting default. If the host tools or root are unavailable, the gate remains BLOCKED; sandbox permissions are preserved. Existing local allowlists are not documented as universal template defaults.

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
