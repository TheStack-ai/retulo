# Security

Retulo's qualified preview target is one user's local macOS filesystem. It checks
the roots, paths, revisions, and receipts managed through its own operations.
Other host tools have their own access and behavior; Retulo does not sandbox them.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/TheStack-ai/retulo/security/advisories/new)
to share a potential vulnerability with the maintainer. The repository's private
reporting setting is enabled. Do not put exploit details, private documents,
credentials or task logs in a public issue.

If you cannot access that route, use the [documentation or usage form](https://github.com/TheStack-ai/retulo/issues/new?template=documentation.yml)
only to request a private contact method, without technical details.

For the private report, include:

- The affected Retulo version or source commit, platform, and client.
- The expected boundary and the observed result.
- A minimal reproduction using invented files and temporary folders.
- Relevant error codes or redacted lines, without private task contents.

The preview has no guaranteed response time or long-term-support commitment.
Confirmed affected versions and fixes will be documented in the changelog.

## Qualified boundary

- Explicitly configured source and workspace roots.
- Local process locks, revision checks, and atomic state writes.
- Bounded CLI and MCP operations.
- Installation previews, conflict checks, and preserved restore snapshots.

See [qualification](docs/QUALIFICATION.md) for the exact validation scope. Remote
hosting, multi-user isolation, Windows or NAS concurrency, and interception of
arbitrary host-tool writes have not been qualified. Keep credentials and task data
outside the source repository.
