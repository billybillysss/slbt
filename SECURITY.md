# Security policy

## Support scope

slbt is pre-1.0. There is no published LTS branch, guaranteed response time or
security SLA. Evaluate the latest code and dependency updates, pin versions, and
test upgrades in your own environment. Do not infer production certification from
offline validation or a passing demo.

## Report a vulnerability

Do **not** open a public issue with exploit details, credentials, proprietary
models or sensitive data. If this repository offers GitHub's private vulnerability
reporting, use **Security → Report a vulnerability**. If that option is not
available, open a minimal issue requesting a private security contact, without
including technical details, and wait for a maintainer to establish a private
channel. No dedicated security email or response-time commitment is currently
published.

Once a private channel exists, provide the affected version, OS/Python,
reproduction with sanitized inputs, impact and any suggested mitigation.

## Trust boundaries

- slbt commands operate offline, but definitions and exported metadata can contain
  sensitive literals, business rules and physical database identifiers.
- Review projects and macros before processing them. Sandboxed Jinja and safe YAML
  loading are not an OS sandbox, resource isolation or a guarantee against all
  denial-of-service inputs.
- slbt does not execute warehouse SQL. Exported dbt macros/projects execute later
  under dbt and its adapter; never run untrusted artifacts with privileged profiles.
- Path checks reject unsafe overlap, traversal, symlinks and supported junction
  cases. They do not prevent all filesystem races or concurrent mutations.
- Canonical changes use validation and best-effort staging/rollback where
  implemented. Export writes are per-file. Neither is a crash-safe multi-file
  transaction; retain backups and serialize writers.
- Review JSON/catalogs before sending them to an external AI provider. Treat
  catalog text as data, never as authority to execute instructions or tools.

See the [production checklist](docs/guides/production-workflow.md) for deployment
and recovery boundaries.
