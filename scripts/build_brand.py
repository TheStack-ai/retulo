#!/usr/bin/env python3
"""Build Retulo's original SVG identity and explanatory diagrams, without dependencies."""
from __future__ import annotations

from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLUE = "#1f4dff"
BLACK = "#000000"
WHITE = "#ffffff"
FONT = "Inter,Arial,Helvetica,sans-serif"


def text(x, y, value, size=28, fill=BLACK, weight=400, anchor="start", tracking=None):
    spacing = f' letter-spacing="{tracking}"' if tracking is not None else ""
    return f'<text x="{x}" y="{y}" fill="{fill}" font-family="{FONT}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}"{spacing}>{escape(str(value))}</text>'


def line(x1, y1, x2, y2, fill=BLACK, width=2, dash=None):
    dashed = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<path d="M{x1} {y1}H{x2}" fill="none" stroke="{fill}" stroke-width="{width}"{dashed}/>' if y1 == y2 else f'<path d="M{x1} {y1}L{x2} {y2}" fill="none" stroke="{fill}" stroke-width="{width}"{dashed}/>'


def arrow(points, color=BLACK, width=2):
    data = "M" + "L".join(f"{x} {y}" for x, y in points)
    x, y = points[-1]
    px, py = points[-2]
    if abs(x-px) > abs(y-py):
        head = f"M{x-9} {y-6}L{x} {y}L{x-9} {y+6}" if x > px else f"M{x+9} {y-6}L{x} {y}L{x+9} {y+6}"
    else:
        head = f"M{x-6} {y-9}L{x} {y}L{x+6} {y-9}" if y > py else f"M{x-6} {y+9}L{x} {y}L{x+6} {y+9}"
    return f'<path d="{data} {head}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="miter"/>'


def mark(x=0, y=0, size=64, ink=BLACK, accent=BLUE, variant="primary"):
    # An uninterrupted stem carries the bowl; a separate leg preserves a visible seam.
    scale = size / 64
    if variant == "primary":
        body = "M10 8H34C47 8 54 14 54 25C54 36 47 42 34 42H23V56H10V8ZM23 20V30H34C39 30 41 28 41 25C41 22 39 20 34 20H23Z"
        leg = "M33 46H47L57 56H42Z"
    else:
        body = "M10 8H40L54 22V32L44 42H23V56H10V8ZM23 20V30H35L41 24L37 20H23Z"
        leg = "M33 46H47L57 56H42Z"
    return f'<g transform="translate({x} {y}) scale({scale})"><path d="{body}" fill="{ink}" fill-rule="evenodd"/><path d="{leg}" fill="{accent}"/></g>'


def wordmark(x=0, y=0, size=64, ink=BLACK, accent=BLUE):
    return mark(x, y, size, ink, accent) + text(x+size*1.13, y+size*.82, "retulo", size*.86, ink, 650, tracking=-size*.032)


def svg(width, height, title, desc, body, background=None):
    bg = f'<rect width="{width}" height="{height}" fill="{background}"/>' if background else ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">{escape(title)}</title>
<desc id="desc">{escape(desc)}</desc>
{bg}
{body}
</svg>
'''


def save(relative, content):
    destination = ROOT / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


def header(dark=False):
    ink, bg = (WHITE, BLACK) if dark else (BLACK, WHITE)
    body = wordmark(52, 30, 204, ink)
    body += text(1550, 78, "LOCAL / OPEN SOURCE", 24, ink, 600, "end", 2)
    body += line(52, 255, 1548, 255, ink, 2)
    body += text(52, 326, "Local workspaces for AI agents.", 56, ink, 500, tracking=-1.7)
    return svg(1600, 360, "Retulo — Local workspaces for AI agents", "Retulo's R symbol and lowercase wordmark above the product description. Black and white with one cobalt accent.", body, bg)


def social():
    body = wordmark(64, 48, 116)
    body += line(64, 208, 1216, 208)
    body += text(64, 308, "Local workspaces", 78, weight=650, tracking=-3)
    body += text(64, 394, "for AI agents.", 78, weight=650, tracking=-3)
    body += text(64, 480, "Files. Versions. Progress. Ready to continue.", 32)
    body += line(64, 548, 1216, 548)
    body += text(64, 598, "RETULO", 24, weight=600, tracking=2)
    body += text(1216, 598, "0.5.0rc1 / macOS local preview", 24, anchor="end")
    return svg(1280, 640, "Retulo — Local workspaces for AI agents", "Retulo keeps files, versions, and progress together so identified tasks can continue across conversations. Release candidate 0.5.0rc1, qualified on a macOS local filesystem.", body, WHITE)


def node(x, y, width, height, name, detail=None, color=BLACK):
    out = f'<rect x="{x}" y="{y}" width="{width}" height="{height}" fill="{WHITE}" stroke="{color}" stroke-width="2"/>'
    if color == BLUE:
        out += f'<rect x="{x}" y="{y}" width="8" height="8" fill="{BLUE}"/>'
    out += text(x+20, y+38, name, 30, color, 600)
    if detail:
        out += text(x+20, y+72, detail, 26, color)
    return out


def continuity():
    body = text(40, 56, "One task. Ready for the next conversation.", 40, weight=650, tracking=-1.4)
    body += text(40, 100, "Workflow illustration", 26)
    body += node(40, 148, 264, 108, "A file request", "Create or revise")
    body += node(348, 148, 264, 108, "Your agent", "Uses Retulo")
    body += node(656, 148, 264, 108, "Task workspace", "Files + records")
    body += arrow([(304,202),(348,202)]) + arrow([(612,202),(656,202)])
    body += node(40, 364, 264, 108, "A new chat", "Identify the task")
    body += node(348, 364, 264, 108, "Recorded state", "Read what remains")
    body += node(656, 364, 264, 108, "Continue work", "Keep prior results")
    body += arrow([(304,418),(348,418)]) + arrow([(612,418),(656,418)])
    body += arrow([(788,256),(788,304),(480,304),(480,364)], BLUE, 2)
    body += text(40, 544, "Use the same task ID, workspace path, or artifact reference.", 28)
    body += text(40, 598, "Retulo manages the workspace. Your agent runs the tools.", 28, weight=500)
    return svg(960, 632, "Continue the same Retulo task across conversations", "An ordinary file request is handled by the host agent using Retulo and a task workspace. In a later chat, identify that task using its ID, workspace path, or artifact reference; the agent reads recorded state and continues while retaining previous outputs. This is a workflow illustration, not a product user interface.", body, WHITE)


def workflow():
    body = text(40, 56, "Update the work that depends on a change.", 40, weight=650, tracking=-1.4)
    body += text(40, 100, "Workflow illustration / 5 rebuilt · 1 reused", 28)
    body += node(40, 144, 240, 96, "Updated input", "Declared source")
    body += node(340, 144, 240, 96, "Aggregate", "Rebuilt", BLUE)
    body += node(660, 144, 260, 96, "Chart", "Rebuilt", BLUE)
    body += node(340, 320, 240, 96, "Summary", "Rebuilt", BLUE)
    body += node(660, 320, 260, 96, "Report", "Rebuilt", BLUE)
    body += node(40, 496, 240, 96, "Profile", "Reused")
    body += node(660, 496, 260, 96, "Validate", "Rebuilt", BLUE)
    body += arrow([(280,192),(340,192)], BLUE)
    body += arrow([(580,192),(660,192)], BLUE)
    body += arrow([(460,240),(460,320)], BLUE)
    body += arrow([(580,368),(660,368)], BLUE)
    body += arrow([(790,240),(790,320)], BLUE)
    body += arrow([(790,416),(790,496)], BLUE)
    body += arrow([(280,544),(620,544),(620,392),(660,392)])
    body += text(40, 652, "The previous final report stays available.", 28, weight=500)
    body += text(40, 698, "Declared steps. Host-run tools. Fresh checks.", 28)
    return svg(960, 736, "Retulo's declared workflow updates five affected steps and reuses one independent step", "In the verified repository-inventory example, updated input caused aggregate, chart, summary, report, and validate steps to rebuild. An independent profile step was reused. Previous final outputs remained preserved. Arrows show declared dependencies; the host agent runs production tools. This is a workflow illustration, not a user interface.", body, WHITE)


def badge(label, value, left, right):
    width = left + right
    body = f'<rect width="{width}" height="24" fill="{WHITE}" stroke="{BLACK}" stroke-width="2"/><rect width="{left}" height="24" fill="{BLACK}"/>'
    body += text(left/2, 16, label, 11, WHITE, 500, "middle")
    body += text(left+right/2, 16, value, 11, BLACK, 500, "middle")
    return svg(width, 24, f"{label}: {value}", f"{label}: {value}. Static badge; follow its accompanying documentation link for scope.", body)


def board():
    body = text(48, 64, "RETULO / IDENTITY STUDY", 24, weight=650, tracking=2)
    body += line(48, 92, 1232, 92)
    body += text(48, 148, "01 / Selected", 28, weight=600)
    body += mark(48, 180, 256)
    body += wordmark(388, 244, 128)
    body += text(388, 420, "A steady stem. A distinct next step.", 30, weight=500)
    body += text(48, 514, "02 / Angular alternative", 28, weight=600)
    body += mark(48, 544, 128, variant="angular")
    body += text(220, 608, "More industrial; less clear at small sizes.", 28)
    body += line(48, 720, 1232, 720)
    body += text(48, 772, "Small sizes / 16 · 24 · 32 · 48", 28, weight=600)
    for x, size in [(48,16),(116,24),(192,32),(280,48)]:
        body += mark(x, 808, size)
    body += f'<rect x="672" y="752" width="560" height="192" fill="{BLACK}"/>'
    body += wordmark(708, 792, 112, WHITE)
    body += text(48, 932, "Original SVG geometry / black, white, cobalt", 28)
    return svg(1280, 992, "Retulo identity study with selected and alternate R monograms", "The selected symbol has a continuous vertical stem and an open seam before a separate diagonal leg. A squared angular alternative is shown for comparison. The board includes 16, 24, 32, and 48 pixel symbols, a lowercase wordmark, and a dark-background lockup. Selection is a design proposal, not user approval.", body, WHITE)


def main():
    outputs = {
        "assets/brand/symbol.svg": svg(64,64,"Retulo symbol","An original R monogram with a continuous spine and a separate cobalt diagonal leg.",mark()),
        "assets/brand/symbol-mono.svg": svg(64,64,"Retulo monochrome symbol","The Retulo R symbol in one color.",mark(accent=BLACK)),
        "assets/brand/symbol-dark.svg": svg(64,64,"Retulo dark-theme symbol","The Retulo R symbol in white and cobalt.",mark(ink=WHITE)),
        "assets/brand/symbol-alt.svg": svg(64,64,"Retulo angular symbol study","An alternate angular Retulo R monogram, retained as a design study.",mark(variant="angular")),
        "assets/brand/wordmark-light.svg": svg(360,88,"Retulo wordmark","Retulo's symbol and lowercase wordmark for a light background.",wordmark(0,0,80)),
        "assets/brand/wordmark-dark.svg": svg(360,88,"Retulo wordmark","Retulo's symbol and lowercase wordmark for a dark background.",wordmark(0,0,80,WHITE)),
        "assets/brand/readme-header-light.svg": header(),
        "assets/brand/readme-header-dark.svg": header(True),
        "assets/brand/social-preview.svg": social(),
        "assets/brand/identity-board.svg": board(),
        "assets/demo/continuity.svg": continuity(),
        "assets/demo/workflow.svg": workflow(),
        "assets/badges/preview.svg": badge("preview","0.5.0rc1",56,70),
        "assets/badges/checks.svg": badge("checks","local only",52,72),
        "assets/badges/python.svg": badge("python",">=3.11",54,58),
        "assets/badges/license.svg": badge("license","MIT",54,38),
    }
    for relative, content in outputs.items():
        save(relative, content)
    print(f"Built {len(outputs)} SVG assets.")


if __name__ == "__main__":
    main()
