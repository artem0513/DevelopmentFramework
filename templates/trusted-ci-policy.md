# Server-side enforcement checklist

The included CI workflow tests this tool; it is not a configured production gate for another repository.

For an actual repository, an administrator must:

1. Run a **pinned trusted copy** of this runtime and the required task policy from a separate protected repository or immutable image. Never load the gate implementation/policy from the untrusted feature branch.
2. Use separate jobs/identities for build evidence, independent AC/review and publication. Bind artifacts to repository, commit/tree digest, task contract and runner identity using the CI artifact/attestation service. Local SQLite receipts are not signed attestations.
3. Require the named gate status with rulesets/branch protection, forbid direct/force pushes and require CODEOWNERS review for CI, task policy and protected paths. Keep any publish credential outside the implementer sandbox; use short-lived least-privilege credentials only in the protected publish job.
4. Execute untrusted tests without production secrets or privileged Azure access. Review dependency restore and integration endpoints. Separate approval environments for destructive/data/security operations.
5. Exercise rejected AC, failed build, stale artifact and modified policy cases on that actual CI before describing remote enforcement as validated.

Optional local hook: `python scripts/install_hook.py --repo <path> --home <state-home> --task <id>`. It refuses to overwrite an existing hook and rejects non-HEAD/non-feature pushes. Hooks remain bypassable via `--no-verify`, Git configuration or filesystem access. Repository administrators, not this installer, control server-side protection.
