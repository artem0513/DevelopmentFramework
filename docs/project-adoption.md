# Explicit project adoption

DevelopmentFramework is a reusable engineering template. Its presence on disk, shared runtime and MCP tools do not adopt other projects. A user can ask: "Copy and adapt DevelopmentFramework for this repository." Until then, use the target project's existing instructions and relevant domain tools.

Financial report interpretation, portfolio data-quality warnings, document preparation and general questions do not activate the engineering process. An actual requested software code/config change is handled by the engineering workflow only in an opted-in target repository. Using a chat opened on this template does not opt a different repository in.

## Prepare the selected project

The assistant executes the following only after explicit adoption is requested; the user need not launch PowerShell manually:

```powershell
python scripts/install.py --project E:/path/to/project
```

The target must be an existing directory. The installer copies the nine owned skills and their resources to `.agents/skills`, three native roles to `.codex/agents`, and adds a managed routing block to the active project AGENTS file. A nonempty AGENTS.override.md takes precedence and receives the block. Existing instructions and configuration are preserved; changed owned files are backed up under the chosen Codex home. Linked targets escaping the selected project, hardlinked managed files and invalid project TOML are rejected before activation.

The installer also appends exact project SKILL.md enable entries to the project's `.codex/config.toml`, preserving unrelated local settings and previously managed selections. Disable global source copies by their exact global SKILL.md paths so their names do not suppress project copies. This host's live discovery showed that a user-wide name disable can still suppress a project path opt-in; do not assume cross-layer override order. The host must trust local configuration. Reload after discovery/config changes; existing chat instructions are not retroactively removed. Generated absolute-path selection files are machine-local; regenerate them for a new checkout.

Next, adapt the project profile, actual build/test commands, coding standards, source inventory and account boundaries. Select optional upstream/vendor skills for this project's stack; do not copy every globally installed skill or reuse another project's sources. The core installer supplies its owned skills, not every optional library or provider adapter. No orders, external sends, Git publication or schedules follow from adoption.

## Shared tooling and verifier access

```powershell
python scripts/install.py --runtime-only
# When project adoption authorizes verification for this selected root:
python scripts/install.py --register-verifier --root E:/path/to/project
```

Runtime-only setup installs shared CLI tooling without discoverable engineering skills, roles or routing. Verifier-only registration requires the runtime to exist and does not run the skill installer. Registration retains the scoped verification tools and sandbox boundary. A request to adopt a named project with verification can authorize that root's registration; do not register unrelated roots or ask again for an action already authorized. Copying skills alone does not grant the MCP host access to a new repository; unavailable verification remains BLOCKED.

`autonomous_se` provides independent engineering verification. `node_repl` is a general calculation/file-processing tool, and `codex_apps` provides connected application tools. Their availability is not a command to use engineering methods for non-code tasks. Project scoping does not require disabling finance or document integrations.

## Optional vendor skills

Inspect the source, choose the applicable stack and pin the reviewed full commit:

```powershell
python scripts/vendor_skills.py --stack angular --inspect
python scripts/vendor_skills.py --stack angular --ref <reviewed-full-SHA> --project E:/path/to/project
```

An explicit destination is required for installation. `--dest` supports a deliberate custom destination; `--global` is an explicit legacy option. Inspection does not install. Available optional upstream skill libraries are not required dependencies for every project.

## Removing a previous global activation

Back up the user config, global instructions, owned native roles and selected skill inventory before changes. Remove only the known autonomous-se managed global instruction block. Keep any other personal instructions. Disable the selected global engineering copies using exact user-level `skills.config` path entries; retain the source files for later selective copying. Review new plugin versions or new skill installations so they do not silently reintroduce user-wide engineering. Keep non-engineering skills and unrelated plugin/MCP/model/sandbox settings intact. Move only owned ASE native roles out of global discovery after the selected project has its local copies.

A neutral user preference may explain that adoption is per project and ordinary domain tasks use domain skills. It must not point every chat to the engineering coordinator. After updating settings, restart the host so already-loaded global instructions/skills are refreshed. The template's own local adoption remains active for work on its software.

The optional `scripts/audit_project_scope.py` performs a read-only migration audit against a private pre-change backup and inventory. It emits outcomes without configuration contents. Keep that backup outside Git because it may contain private host settings. File audits and fixture tests do not alone establish every host's discovery behavior or enforce model compliance.

## Reapplication and rollback

Reapplying to the same project updates owned files and managed blocks with backups. Review the changed-file manifest. To roll back, restore only those owned files from their corresponding backups, remove only the project's managed routing/skill-enable blocks and remove newly created owned files that had no prior version. Preserve unrelated `.agents` and `.codex` contents. Shared tooling can remain inactive. Global activation is available only through an explicit `--global` invocation and is not the default restoration path.

Current scope follows [ADR 003](ADR-003.md). Historical validation of the old global installer remains historical evidence, not current default behavior.
