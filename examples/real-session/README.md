# One proposal, two real Codex conversations

[Watch the 60-second demo](https://thestack-ai.github.io/retulo/site/#demo).

On November 9, 2026, we created a fictional website proposal in Retulo, then opened a separate Codex conversation to shorten its schedule from four weeks to three. The new conversation read the saved task and files. It saved `proposal-v002.md` in the same workspace while `proposal-v001.md` stayed unchanged.

The video condenses the two-conversation workflow into 60 seconds using the actual documents and shortened requests. The terminal insert records a later, read-only Retulo command on the same isolated demo task. The filtered output shows the task ID, file integrity result and original filename. The second Codex conversation took about 3 minutes 36 seconds.

## Inspect the files

- [Source notes](notes.md): synthetic material, with no customer information.
- [First proposal](proposal-v001.md): four-week schedule.
- [Revised proposal](proposal-v002.md): three-week schedule; the other sections are unchanged.
- [Run result](result-v3.json) records the task identity, artifact revisions, checks and published video digest.

The original proposal's SHA-256 before **and** after the new conversation was:

```text
0020f9fcc39e8980632edf6afcdf21ca3ceac952d75267a031ad020e1f0700e5
```

The revised proposal has different bytes and a separate version:

```text
b0c9b5be7ee2c0275f2c2e182fc8aefa6c512a0db147c95e507ef248d58d505c
```

You can check the published files using only Python's standard library:

```bash
python3 examples/real-session/verify.py
```

This checks the published files, including the video, against their recorded hashes. Historical Codex conversations are documented in the run result.

## What actually ran

1. In the first coordinator conversation, `start.py` created an isolated configuration and synthetic notes, called `begin_task`, copied the source, reserved and committed the first proposal, checked its content, and completed the task. The initial proposal text is authored in the script.
2. We opened a real new Codex conversation with the exact task ID, workspace path and isolated CLI configuration. The request asked for a three-week schedule and preservation of the original version. It did not include the proposal text or the parent conversation history.
3. That conversation inspected and reopened the completed task, read the existing files, authored the revised schedule, reserved the same logical filename and committed version 2. It ran content and hash checks, then completed the task.
4. The first coordinator independently compared the files and ran Retulo's integrity check again. Both registered versions were intact, and the source notes were unchanged.

The public task ID is `51c9c1f8-a1e8-4c80-8a3f-05d26fd313fb`. Both output versions share artifact ID `7ffb910a-fb09-4401-a651-3f2696b87107`. Session identities are represented by hashes in the run result; personal paths, reservation tokens and conversation logs are omitted.

## Try the workflow yourself

From an installed source checkout, create the first draft in a new or empty directory:

```bash
.venv/bin/python examples/real-session/start.py --run-dir /tmp/retulo-real-session
```

The command prints the actual task ID, workspace path and config path. It does not start an agent or change your existing agent settings. Keep that output, open a new agent conversation, and replace the placeholders in this request:

> Continue Retulo task `<task_id>` in `<workspace_path>`. Read its saved state and source files. Change the four-week schedule to three weeks, keeping the scope and existing logo/contact-email constraint. Preserve the previous version. For this isolated demo, use `python -m retulo --config <config_path> call ...` from this source checkout. Reopen the completed task before editing. Reserve `proposal.md` again, write only to the returned staging path, commit the new version, verify both versions, and complete the task with fresh content checks.

The agent needs access to the checkout and demo directory. Use the exact returned task ID or workspace path to identify the task. The script refuses to reuse a nonempty run directory.

## Scope of the evidence

This is one real, CLI-assisted task continuation in Codex using Retulo `0.6.0rc1`. It demonstrates saved task state, a fresh conversation and managed file version preservation. It does not establish automatic task discovery, MCP installation success, another client's compatibility or external-user usability. File integrity and content checks do not constitute user visual approval.

The run used a separate configuration, workspace directory and state directory. It required no personal runtime migration, installer, hook change or additional model API. The requested new Codex conversation used the app's normal agent execution. Video rendering used local HyperFrames and FFmpeg; its simple soundtrack was synthesized locally, with no paid media generation.
