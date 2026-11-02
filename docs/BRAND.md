# Retulo identity

Retulo's identity is built around a stable task: files, versions, decisions, and
remaining work stay connected as the work continues. Updating an affected step
is a second expression of the same idea.

## Symbol and wordmark

The primary symbol is an original geometric R. Its uninterrupted vertical stem
supports a rounded upper bowl. A narrow open seam separates the diagonal leg.
The stem represents continuity; the leg gives the work a clear next step.

The selected version is `assets/brand/symbol.svg`. The angular alternative is
retained in `symbol-alt.svg` and on `identity-board.svg` for design comparison.
The selected direction remains a proposal until the user reviews the rendered
assets. File creation and automated checks do not establish visual approval.

Use **Retulo** in prose, **retulo** in the wordmark, and `retulo` for the command.
Use the full wordmark when space allows. At 16–24 pixels use the symbol alone.
Do not add the wordmark to a tiny repository icon or crop away the diagonal leg.
GitHub repositories display their owner's avatar; these assets do not replace
that avatar automatically.

## Color and type

| Role | Value |
|---|---|
| Light canvas / dark-theme lettering | `#ffffff` |
| Lettering / dark canvas | `#000000` |
| Small accent | `#1f4dff` |

Cobalt should occupy no more than 5% of an image. It identifies the symbol's
separate leg and affected workflow steps. Color is paired with words such as
“Rebuilt” and “Reused”; it never carries status by itself.

The visual system follows Signal Monochrome: black and white fields, thin rules,
compact hierarchy, one accent, and no gradients or shadows. The symbol is pure
path geometry. The wordmark and diagram lettering use the CSS font stack
`Inter, Arial, Helvetica, sans-serif`; no font binaries are bundled. The primary
SVGs therefore remain editable text and can use installed fallback fonts.
A rendered PNG may be used when identical text metrics are needed across hosts.

Keep clear space of at least one stem width around the symbol. Preserve its
aspect ratio and the seam between the bowl and leg. Use the monochrome asset
when only one ink is available. Use the dark-theme asset on a dark background.

## Assets

| Asset | Purpose | Intrinsic size |
|---|---|---|
| `symbol.svg` / `symbol-mono.svg` / `symbol-dark.svg` | Primary icon | 64 × 64 |
| `symbol-alt.svg` | Alternate design study | 64 × 64 |
| `wordmark-light.svg` / `wordmark-dark.svg` | Navigation and identity lockup | 360 × 88 |
| `readme-header-light.svg` / `readme-header-dark.svg` | README header | 1600 × 360 |
| `social-preview.svg` | Source for GitHub's sharing image | 1280 × 640 |
| `identity-board.svg` | Direction, alternate, size, and theme comparison | 1280 × 992 |
| `../demo/continuity.svg` | Task continuity explanation | 960 × 632 |
| `../demo/workflow.svg` | Declared-dependency example | 960 × 736 |

For GitHub's Social preview setting, render `social-preview.svg` to a 1280 × 640
PNG below 1 MB. The SVG is the editable source; it is not itself an upload format
for that setting. Header assets are intended to display at 800 pixels wide and
scale down in README layouts. Their essential product line remains large enough
to read at a narrow mobile width.

The two diagrams are **workflow illustrations, not screenshots of a Retulo UI**.
The workflow figure describes the measured repository-inventory example: five
steps rebuilt and one independent profile step reused. This is not a general
cache-hit or token-saving claim. The producer is the host agent.

## Static badges

The four local badge images show the candidate version, local-check scope,
required Python version, and MIT license. They make no claim of hosted CI,
publication, download count, coverage, or production stability.

- `preview.svg`: `preview | 0.4.0rc1`
- `checks.svg`: `checks | local only`
- `python.svg`: `python | >=3.11`
- `license.svg`: `license | MIT`

Keep the badge links next to their scope: release notes, qualification details,
Python requirements, and LICENSE. Replace the checks badge with GitHub Actions'
actual status only after a real hosted run exists. Keep success and failure
colors meaningful when dynamic badges are introduced.

## Suggested alternative text

- Header: “Retulo — Local workspaces for AI agents.”
- Symbol: “Retulo R symbol.”
- Continuity figure: “The host agent uses Retulo to manage a task workspace. In
  another conversation, identify the same task to read its recorded state and
  continue while retaining earlier results.”
- Workflow figure: “In the verified example, updated input rebuilds aggregate,
  chart, summary, report, and validation. The independent profile is reused and
  the previous final report remains available.”

## Rebuild and review

From the source checkout:

```sh
python3 scripts/build_brand.py
```

The builder uses only Python's standard library. It writes the named assets and
makes no network calls. Run it after changing geometry or copy. Review the actual
rendered output at 16, 24, and 32 pixels, in light and dark themes, at a 330-pixel
README width, and as a small social preview. Confirm that lettering fits, the R
still reads in monochrome, and every diagram arrow matches its explanation.

These original assets are distributed with the repository under its MIT license.

## Ready-to-upload social image

`assets/brand/social-preview.png` is the inspected 1280 × 640 PNG export of the
SVG source. It is below GitHub's 1 MB social-preview limit. The export was
rendered by the local browser at 2× and downsampled with Lanczos; the SVG remains
the editable source. Regenerating the SVG does not automatically refresh this
PNG. Export and inspect it again after changing the artwork or copy.

The published README uses the actual GitHub Actions `ci.yml` status badge.
The bundled `checks.svg` remains an explicitly local-only fallback.
