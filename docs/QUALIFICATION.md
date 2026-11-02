# Qualification — Retulo

## GitHub-hosted validation — 2026-11-02

The first public source commit, `85da50712bc9b208ffec68d6d6c808e38004bcb9`,
passed [Release checks](https://github.com/TheStack-ai/retulo/actions/runs/41441041094)
on the macOS runners for Python 3.11 and 3.14. The workflow runs the full
regression suite, isolated install/lifecycle/restore smoke, source inspection,
and wheel/sdist build and inspection. [Documentation deployment](https://github.com/TheStack-ai/retulo/actions/runs/41441041053)
also passed. The current default-branch status is available from the README badge.

## 0.4.0rc1 — 2026-11-02

The workflow/recipe candidate was checked on macOS arm64. Its original 0.3.1rc1
source snapshot was retained before implementation; that baseline passed 195
tests. The following are observed local checks, not a hosted CI result.

| Check | Result |
|---|---|
| Python 3.14.4 source regression suite | 379 tests passed |
| Python 3.11.15 fresh virtualenv, built wheel installed normally | 379 tests passed; imports resolved to the installed wheel |
| MCP descriptions after the final budget adjustment | 7 focused SDK checks passed on each tested Python environment |
| Isolated install/reinstall/hooks/restore | Passed; original settings restored and artifacts preserved |
| Actual MCP stdio | 29 tools; workflow execution, restart, scoped targets, paging and recipe creation exercised |
| Actual report production | Separate CLI and producer processes generated HTML/SVG from real repository inventories |
| Input update | Five affected stages regenerated; the independent profile stage reused |
| Recipe reuse | A different real test/document inventory produced a new completed report task |
| Preservation | Input files, previous final report and completed recipe-origin task retained |
| Creation retry | Same recipe request returned the same task; new work inherited no old approvals or verdicts |
| Independent implementation review | Reproduced findings were fixed and independently rechecked |

The suite covers partial output, publication/recovery failure, input changes
between planning and completion, lost responses, late attempts, manual adoption,
missing/tampered execution snapshots, output-contract changes, target-specific
evidence, recipe pinning and v1 receipt compatibility. Large default context,
plan, recipe and execution-start responses have explicit size and recovery tests.

An early Python 3.11 target-overlay attempt was not accepted as qualification:
its MCP subprocess loaded the previous installed package instead of the overlay,
and tool definitions exceeded the budget in that environment. The final check
used a fresh virtualenv with locked dependencies and the actual built wheel;
tool prose was shortened without increasing the budget.

The current Codex coordinator exercised the candidate CLI directly and inspected
the report in a browser. This does not establish automatic adoption by an
already-connected installed MCP session, or compatibility with another provider.
The operating installation was not replaced. Example content checks and browser
layout inspection are separate from user approval.

Reproduce the relevant checks from the source checkout:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/smoke_local.py
.venv/bin/python scripts/demo_incremental.py --output-dir /tmp/retulo-report-demo
.venv/bin/python -m build --no-isolation
.venv/bin/python scripts/check_release.py --artifact dist/retulo-0.4.0rc1-py3-none-any.whl --artifact dist/retulo-0.4.0rc1.tar.gz
```

Use a new empty example directory. The engine core makes no model calls and
requires neither Main Brain nor another agent orchestrator. No new runtime
dependency was added for workflows or recipes.

## Public presentation refresh — 2026-11-02

The current source passed the 379-test suite again after the multilingual README,
community forms, site, and brand assets were added. The runtime and test files
were unchanged during that run. The [continuity example](../examples/continuity/README.md)
also ran through 16 separate CLI calls: it reopened the same task, preserved its
source and first output, and recorded fresh checks for the second output.
This synthetic example does not establish adoption by a new Codex conversation.

The three language pages and local README layouts were inspected at desktop and
phone widths. The hosted-metadata build was checked separately in a temporary
directory; it was not deployed. See [Site](SITE.md) and [Publication](PUBLICATION.md)
for the distinction between prepared metadata and observed hosted results.

## Historical qualification — 0.3.1rc1

The public candidate is evaluated separately from its development prototype. Checks use synthetic data and explicit temporary settings roots. The operating installation is not replaced or restarted.

## Candidate checks

Local runs on macOS arm64 with Python 3.14.4 (source environment) and Python 3.11.15 (clean wheel installation), 2026-11-02:

| Check | Result |
|---|---|
| Synthetic regression suite | 195 tests passed on each Python version |
| Source install preview | No settings writes |
| Apply and repeat apply | Installation succeeds; repeat is unchanged |
| Three lifecycle hook commands | Bounded context, no task creation |
| Separate CLI invocations | Same task completed at v1 and v2; v1 preserved |
| Real MCP stdio | 22 tools; registered files verified |
| Optional memory receiver | Unconfigured explicitly; core works without it |
| Restore preview and apply | Original settings restored, artifacts retained |
| Wheel and sdist | Built and inspected separately from source |

Reproduce the suite and isolated lifecycle with:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/smoke_local.py
.venv/bin/python scripts/check_release.py --artifacts-dir dist
```

The smoke script makes temporary settings and synthetic files, invokes real installed CLI/hook/MCP processes, then restores the installation and checks preserved artifacts. It makes no model calls.

## Prior real-client evidence

The preceding 0.3.0 prototype was exercised in actual Codex sessions with ordinary question, document, development and independent-session continuation requests. Those runs established the prototype's default workflow and preserved earlier files. Private prompts, event logs and source documents are not distributed.

This candidate's name, package and integration paths changed. Its deterministic installation and protocol checks do not prove that a newly connected Codex client has trusted and adopted the renamed hooks. A real client must review those exact definitions. No repeated model trial or trust bypass is used to imply this approval, and no existing user installation is changed for the release check.

## Limits

Qualified target: macOS and a local filesystem. Python 3.11 is the declared minimum. Both 3.11.15 and 3.14.4 passed locally; the prepared CI matrix targets 3.11 and 3.14. The linked GitHub Actions run records the hosted result for its exact source commit. Other providers, Linux, Windows, NAS and multi-host concurrency remain unqualified.

Integrity checks do not establish content quality or human approval. The optional memory receiver and external orchestrators are not required. No general token-cost saving or cache-hit rate is claimed. Snapshot restoration protects against detected changes but does not provide a power-loss recovery journal.
