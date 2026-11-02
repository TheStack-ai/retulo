# Contributing to Retulo

You can help by improving an example, clarifying documentation, reproducing a bug,
or proposing a focused code change. English, 简体中文, and 한국어 are welcome.
See [where to ask or report a problem](docs/COMMUNITY.md) before opening an issue.

For a large feature or a change to the task format, describe the intended workflow
and discuss the approach before writing a substantial implementation. A feature
proposal is useful when it explains the current workaround and a result we can check.

## Set up a development environment

From the source checkout, use Python 3.11 or later:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock -r requirements-build.lock
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/retulo --version
```

Use invented data and temporary directories for development and tests. Keep task
workspaces, private documents, conversations, credentials, and installer snapshots
outside this repository. Installer and restore checks must use temporary settings,
never your real home configuration.

## Make and check a change

Keep the change small enough to review. Run the checks relevant to the behavior
you changed. Add a regression test when it demonstrates a real failure or protects
an important contract. A wording or layout fix usually needs a link, command, or
rendering check rather than a new unit test.

Before submitting code that affects core behavior, run the full suite and the public
source check:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py
```

For installation or restoration changes, also run the isolated lifecycle check:

```sh
.venv/bin/python scripts/smoke_local.py
```

For a distribution change, build and inspect both formats:

```sh
.venv/bin/python -m build --no-isolation
.venv/bin/python scripts/check_release.py --artifacts-dir dist
```

See [qualification](docs/QUALIFICATION.md) for the environments and methods already
checked. A local test result does not establish compatibility with another client
or operating system.

## Preserve the core contracts

- Keep task IDs, source material, earlier output versions, and operation receipts intact.
- Keep state updates revision-checked and retries consistent.
- Treat JSON state as authoritative and generated Markdown as its current view.
- Record saved files, content checks, user approval, and external delivery separately.
- Keep model calls, agent launching, recipe command execution, and automatic publishing out of the core.
- Keep memory services and external orchestrators optional.

For a recipe example, include its input contract, required tools, expected outputs,
and checks. Use sample data another contributor can run locally. A new task must
perform its own checks; it does not inherit approval from the recipe's earlier use.

## Submit a pull request

Explain the problem, the resulting behavior, and the checks you actually ran. Note
effects on the task format, existing clients, installation, and documentation. If a
coding assistant helped, review and understand the change before submitting it.

Contributions use the repository's [MIT license](LICENSE). Identify copied or
adapted material and preserve its source and [third-party notices](THIRD_PARTY_NOTICES.md).
For a possible vulnerability, follow [the security reporting instructions](SECURITY.md).
