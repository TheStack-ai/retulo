# Retulo contributor instructions

- Work only in the requested checkout and assigned paths. Preserve other contributors' changes.
- Keep runtime workspaces, credentials, private documents and conversations outside this repository.
- Use synthetic temporary fixtures. Never apply installer or restore tests to real user settings.
- Keep the core deterministic: no model calls, background watcher, agent launcher or automatic publication.
- Preserve task UUIDs, originals, immutable output versions, revision checks and receipts.
- JSON state is authoritative; generated Markdown is a projection.
- File integrity, content validation, user approval and external delivery are distinct.
- Verify changed behavior and reuse valid checks. Simulations do not establish real-client compatibility.
- Uploading or publishing requires a separate explicit instruction.
