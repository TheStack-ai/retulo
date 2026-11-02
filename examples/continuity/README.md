# One task, two file versions

These two Markdown files were produced by a real run of
`scripts/demo_continuity.py` using synthetic visitor notes. A fresh coordinator
identified the same task through separate CLI processes and added a section.
The source notes and the first output remained intact. `result.json` records the
observed checks and exact output hashes without machine-specific paths.

- [First guide](visitor-guide-v001.md)
- [Continued guide](visitor-guide-v002.md)
- [Observed result](result.json)

Run the example from the source checkout with a new or empty destination:

```sh
.venv/bin/python scripts/demo_continuity.py --output-dir /tmp/retulo-continuity-demo
```

The demo writes the expected guide text itself; it makes no model calls and
does not change global settings. It checks CLI task continuity and preservation.
It does not establish adoption by a newly connected Codex client, interpret a
natural-language request, or claim human approval of the guide.
