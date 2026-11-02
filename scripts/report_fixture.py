#!/usr/bin/env python3
"""Small, deterministic host-side report producer used by the workflow demo.

Retulo never executes this script. The demo host invokes it with explicit input
and reserved output paths, just as an agent invokes an existing local tool.
The input inventory describes real files; it contains no invented business data.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
from pathlib import Path


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(value, encoding="utf-8")


def metrics_from_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["area", "files", "bytes", "lines"]:
            raise ValueError("Inventory columns must be area,files,bytes,lines")
        rows = []
        for source in reader:
            row = {"area": source["area"]}
            for key in ("files", "bytes", "lines"):
                row[key] = int(source[key])
                if row[key] < 0:
                    raise ValueError("Inventory counts cannot be negative")
            rows.append(row)
    if not rows or len({r["area"] for r in rows}) != len(rows):
        raise ValueError("Inventory must have nonempty, unique areas")
    rows.sort(key=lambda r: (-r["lines"], r["area"]))
    return {"areas": rows, **{key: sum(r[key] for r in rows) for key in ("files", "bytes", "lines")}}


def chart_svg(metrics):
    rows = metrics["areas"]
    height = 56 + len(rows) * 48
    ceiling = max(1, *(r["lines"] for r in rows))
    pieces = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 {height}" role="img" aria-label="Lines by source area">',
              '<style>text{font-family:ui-sans-serif,system-ui,sans-serif;fill:#253344;font-size:13px}.amount{font-variant-numeric:tabular-nums;fill:#536377}</style>']
    for index, row in enumerate(rows):
        y = 24 + index * 48
        width = round(490 * row["lines"] / ceiling, 2)
        label = html.escape(row["area"])
        pieces.extend([f'<text x="0" y="{y + 18}">{label}</text>',
                       f'<rect x="130" y="{y}" width="490" height="26" rx="3" fill="#edf1f4"/>',
                       f'<rect x="130" y="{y}" width="{width}" height="26" rx="3" fill="#177667"/>',
                       f'<text class="amount" x="650" y="{y + 18}">{row["lines"]:,}</text>'])
    pieces.append('</svg>')
    return "\n".join(pieces)


def report_html(metrics, profile, chart, summary, parameters):
    title = html.escape(str(parameters.get("title", "Source composition report")))
    as_of = html.escape(str(parameters.get("as_of", "")))
    rows = "\n".join(
        f'<tr><th scope="row">{html.escape(r["area"])}</th><td>{r["files"]:,}</td>'
        f'<td>{r["lines"]:,}</td><td>{r["bytes"] / 1024:,.1f} KB</td></tr>'
        for r in metrics["areas"])
    paragraphs = "\n".join(f'<p>{html.escape(p)}</p>' for p in summary.split("\n\n") if p.strip())
    embedded = json.dumps(metrics, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title><style>
*{{box-sizing:border-box}}body{{margin:0;background:#f2f4f5;color:#182b37;font:15px/1.65 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR",sans-serif}}
main{{max-width:1040px;margin:40px auto;padding:52px 64px 44px;background:#fff;border:1px solid #dfe5e8}}
.eyebrow{{font-size:11px;letter-spacing:.16em;color:#177667;font-weight:750}}h1{{font-size:34px;line-height:1.25;letter-spacing:-.04em;margin:12px 0 14px}}h2{{font-size:18px;letter-spacing:-.02em;margin:0 0 18px}}
.date,.note{{color:#667784;font-size:12px}}header{{padding-bottom:30px;border-bottom:2px solid #183a41}}
.profile{{max-width:700px;color:#526573;margin:16px 0 0}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);border-bottom:1px solid #dfe5e8;padding:28px 0;margin-bottom:34px}}
.stat+.stat{{border-left:1px solid #dfe5e8;padding-left:32px}}.stat-label{{font-size:12px;color:#667784}}.value{{font-size:36px;line-height:1.4;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.04em}}.unit{{font-size:13px;font-weight:400;color:#667784;margin-left:6px}}
section{{margin:32px 0}}svg{{display:block;width:100%;height:auto}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:12px 10px;border-bottom:1px solid #e4e9ec}}thead th{{background:#f3f6f7;color:#536773;font-size:11px}}th{{text-align:left;font-weight:550}}td,thead th:not(:first-child){{text-align:right;font-variant-numeric:tabular-nums}}
.summary{{padding:18px 24px;border-left:3px solid #177667;background:#f4f8f7}}.summary p{{margin:4px 0 10px}}footer{{border-top:1px solid #dfe5e8;padding-top:20px;margin-top:36px;color:#667784;font-size:11px}}
@media(max-width:700px){{main{{margin:0;padding:32px 22px;border:0}}h1{{font-size:27px}}.value{{font-size:24px;white-space:nowrap}}.unit{{display:block;font-size:11px;margin:2px 0 0}}.stat+.stat{{padding-left:12px}}table{{font-size:12px}}svg text{{font-size:24px}}svg text.amount{{font-size:22px}}}}
@media print{{body{{background:#fff}}main{{margin:0;border:0;max-width:none;padding:12mm}}section{{break-inside:avoid}}header{{break-after:avoid}}}}
</style></head><body><main data-total-files="{metrics['files']}" data-total-lines="{metrics['lines']}">
<header><div class="eyebrow">RETULO / ENGINEERING REPORT</div><h1>{title}</h1><div class="date">기준 시각 · {as_of}</div>{profile}</header>
<div class="stats"><div class="stat"><div class="stat-label">분석 파일</div><div class="value">{metrics['files']:,}<span class="unit">개</span></div></div><div class="stat"><div class="stat-label">텍스트 줄 수</div><div class="value">{metrics['lines']:,}<span class="unit">줄</span></div></div><div class="stat"><div class="stat-label">파일 크기</div><div class="value">{metrics['bytes']/1024:,.1f}<span class="unit">KB</span></div></div></div>
<section><h2>영역별 구성</h2>{chart}<p class="note">막대는 텍스트 줄 수를 나타냅니다. 코드 품질이나 개발 생산성을 평가하는 지표는 아닙니다.</p></section>
<section><h2>파일 집계</h2><table><thead><tr><th>영역</th><th>파일</th><th>줄 수</th><th>크기</th></tr></thead><tbody>{rows}</tbody></table></section>
<section><h2>관찰 내용</h2><div class="summary">{paragraphs}</div></section>
<footer>실제 파일 목록에서 집계한 보고서입니다. 입력 CSV, 집계 결과, 차트와 검사 기록은 각각 버전이 보존됩니다.</footer>
<script type="application/json" id="report-data">{embedded}</script></main></body></html>'''


def produce(stage, inputs, outputs, parameters):
    if stage == "profile":
        profile = load_json(inputs["profile"])
        write(outputs["profile"], '<p class="profile">' + html.escape(profile["description"]) + '</p>')
    elif stage == "aggregate":
        metrics = metrics_from_csv(inputs["inventory"])
        write(outputs["metrics"], json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
        with Path(outputs["table"]).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=["area", "files", "bytes", "lines"])
            writer.writeheader(); writer.writerows(metrics["areas"])
    elif stage == "chart":
        write(outputs["chart"], chart_svg(load_json(inputs["metrics"])))
    elif stage == "summary":
        metrics = load_json(inputs["metrics"])
        first = metrics["areas"][0]
        summary = (f"선택한 자료는 {len(metrics['areas'])}개 영역의 파일 {metrics['files']:,}개로 구성됩니다. "
                   f"전체 텍스트는 {metrics['lines']:,}줄이며 파일 크기는 {metrics['bytes'] / 1024:,.1f} KB입니다.\n\n"
                   f"텍스트 줄 수가 가장 많은 영역은 {first['area']}입니다. "
                   f"이 영역의 파일 {first['files']:,}개가 {first['lines']:,}줄을 차지합니다. "
                   "분포 변화는 입력 자료의 변화를 보여주며 품질 개선 여부를 단독으로 증명하지 않습니다.")
        write(outputs["summary"], summary + "\n")
    elif stage == "report":
        metrics = load_json(inputs["metrics"])
        read = lambda key: Path(inputs[key]).read_text(encoding="utf-8")
        write(outputs["report"], report_html(metrics, read("profile"), read("chart"), read("summary"), parameters))
    elif stage == "validate":
        import re
        from xml.etree import ElementTree
        metrics = load_json(inputs["metrics"])
        report = Path(inputs["report"]).read_text(encoding="utf-8")
        match = re.search(r'<script type="application/json" id="report-data">(.*?)</script>', report, re.S)
        if not match or json.loads(match.group(1)) != metrics:
            raise ValueError("Report data does not match its exact metrics input")
        for key in ("files", "bytes", "lines"):
            if metrics[key] != sum(r[key] for r in metrics["areas"]):
                raise ValueError(f"Incorrect {key} total")
        svg = re.search(r'<svg\b.*?</svg>', report, re.S)
        if not svg:
            raise ValueError("Missing embedded chart")
        ElementTree.fromstring(svg.group())
        if f'data-total-files="{metrics["files"]}"' not in report:
            raise ValueError("Displayed file total is stale")
        write(outputs["validation"], json.dumps({"status": "passed", "checks": ["inventory_totals", "exact_report_data", "embedded_svg", "displayed_total"], "files": metrics["files"], "lines": metrics["lines"], "visual_review": "separate"}, indent=2) + "\n")
    else:
        raise ValueError(f"Unsupported report stage: {stage}")
    for name, path in outputs.items():
        if not Path(path).is_file() or Path(path).stat().st_size == 0:
            raise ValueError(f"Missing output {name}")
    return {"stage": stage, "status": "produced", "outputs": sorted(outputs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--inputs", required=True, help="JSON mapping of explicit input paths")
    parser.add_argument("--outputs", required=True, help="JSON mapping of reserved output paths")
    parser.add_argument("--parameters", default="{}")
    args = parser.parse_args()
    print(json.dumps(produce(args.stage, json.loads(args.inputs), json.loads(args.outputs), json.loads(args.parameters)), ensure_ascii=False))


if __name__ == "__main__":
    main()
