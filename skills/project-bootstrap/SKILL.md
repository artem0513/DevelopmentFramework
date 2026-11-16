---
name: project-bootstrap
description: "Use when the user explicitly requests adoption of DevelopmentFramework in a selected project or local workspace, or backend preflight for that adoption."
---

# Explicit workspace adoption

A template, installed runtime or readable workspace does not adopt other projects. Prepare tooling separately from project routing. Activate only the explicitly selected existing project/workspace and backend targets; no Git repository is required. Preserve applicable local instructions and existing user authorization.

For the user's requested all-in-project installation add `--project-local`: preview `python scripts/install.py --project PATH --project-local --target codex --preview` from the framework source, adding `--target claude` or `--target copilot` only when selected. Review exact owned files, hashes, scope and private backup paths before applying. Runtime/settings live in `.devframework`, private memory/journal in its ignored `local` child, and commands use `.devframework/bin/ase.ps1` or `.devframework/bin/ase`. Without that flag existing shared adoption remains available; `--runtime-only --target BACKEND` prepares shared tooling without routing. Never change authentication, unrelated integrations or provider permission settings as a setup shortcut.

Register project identity with the local `ase` CLI; new projects use `local` reporting time from the executing computer, so a region is not a setup question. Respect an explicit existing timezone selection; inspect actual source inventory, domain constraints, checks and capability versions. The bundled project profile is a worksheet, not an access grant. Use task-discovery to record tool presence separately from permission, resource access, structured output and independent execution.

Use software checks for code/config implementation, domain evidence for documents/research, and real resource observations for infrastructure. Native role pointers and protocol tests do not prove live model support. Missing subscribed backends remain explicitly unavailable; do not install them merely to complete a support matrix.

Read [skill routing](references/skill-routing.md) when selecting optional methods. Inspect and pin a vendor package before explicitly scoped copying; downloaded scripts are not executed. A shared runtime never activates project routing globally.
