#!/usr/bin/env python3
"""Build Retulo's static multilingual documentation preview with the standard library.

Run from the source checkout: python3 scripts/build_site.py
The generated site uses repository-relative assets and Markdown documentation.
Serve the repository root, then open /site/. It does not start a listener or deploy.

For a real hosted destination, --base-url must be the absolute URL of site/index.html's
directory. Only that explicit mode emits canonical/hreflang, sitemap and JSON-LD.
Assets keep their repository-relative layout. With --repository-url, Markdown
and license links open the repository's readable main-branch GitHub pages.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
LANGUAGES = ("en", "zh-CN", "ko")
LOCALE_PATHS = {"en": "", "zh-CN": "zh-CN/", "ko": "ko/"}
NAV_IDS = ("use-cases", "how-it-works", "quick-start", "documentation", "help")
CONTINUITY = ".venv/bin/python scripts/demo_continuity.py --output-dir /tmp/retulo-continuity-demo"
DEMO = ".venv/bin/python scripts/demo_incremental.py --output-dir /tmp/retulo-report-demo"


def e(value: object) -> str:
    return html.escape(str(value), quote=True)


def lines(value: str) -> str:
    return "<br>".join(e(part) for part in value.split("\n"))


def valid_url(raw: str, *, directory: bool = False) -> str:
    parsed = urlsplit(raw)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Hosted metadata URLs must be absolute HTTPS URLs without credentials, query or fragment")
    return raw.rstrip("/") + "/" if directory else raw


def language_links(locale: str, content: dict) -> str:
    prefix = "" if locale == "en" else "../"
    links = []
    for lang in LANGUAGES:
        target = prefix + LOCALE_PATHS[lang] + "index.html"
        current = ' aria-current="page"' if locale == lang else ""
        links.append(f'<a href="{e(target)}" lang="{lang}" hreflang="{lang}"{current}>{e(content[lang]["name"])}</a>')
    return "".join(links)


def code_block(code_id: str, code: str, c: dict) -> str:
    return f'''<div class="code-frame">
      <button class="copy-button" type="button" data-copy="{code_id}" data-label="{e(c['copy'])}" data-copied="{e(c['copied'])}" data-failed="{e(c['copy_failed'])}" hidden>{e(c['copy'])}</button>
      <pre><code id="{code_id}">{e(code)}</code></pre>
    </div>'''


def metadata(locale: str, c: dict, version: str, base_url: str | None,
             repository_url: str | None, social_image_url: str | None) -> str:
    parts = [f'<meta name="description" content="{e(c["description"])}">',
             '<meta name="color-scheme" content="light">',
             f'<meta property="og:title" content="{e(c["title"])}">',
             f'<meta property="og:description" content="{e(c["description"])}">',
             '<meta property="og:type" content="website">',
             '<meta property="og:site_name" content="Retulo">']
    if not base_url:
        # A local preview must not assert a made-up official site identity.
        return "\n  ".join(parts)
    canonical = urljoin(base_url, LOCALE_PATHS[locale])
    parts.extend((f'<link rel="canonical" href="{e(canonical)}">',
                  f'<meta property="og:url" content="{e(canonical)}">',
                  '<meta name="twitter:card" content="summary_large_image">'))
    if social_image_url:
        parts.append(f'<meta property="og:image" content="{e(social_image_url)}">')
        parts.append(f'<meta name="twitter:image" content="{e(social_image_url)}">')
    for lang in LANGUAGES:
        parts.append(f'<link rel="alternate" hreflang="{lang}" href="{e(urljoin(base_url, LOCALE_PATHS[lang]))}">')
    parts.append(f'<link rel="alternate" hreflang="x-default" href="{e(base_url)}">')
    schema = {"@context": "https://schema.org", "@type": "SoftwareSourceCode",
              "name": "Retulo", "description": c["description"], "url": canonical,
              "softwareVersion": version, "programmingLanguage": "Python",
              "runtimePlatform": "Python 3.11+", "operatingSystem": "macOS",
              "license": "https://opensource.org/license/mit", "inLanguage": locale}
    if repository_url:
        schema["codeRepository"] = repository_url
    encoded = json.dumps(schema, ensure_ascii=False).replace("<", "\\u003c")
    parts.append(f'<script type="application/ld+json">{encoded}</script>')
    return "\n  ".join(parts)


def render(locale: str, c: dict, content: dict, version: str, args: argparse.Namespace,
           install: str, connect: str) -> str:
    prefix = "../" if locale == "en" else "../../"
    site_prefix = "" if locale == "en" else "../"
    css_version = hashlib.sha256((SITE / "styles.css").read_bytes()).hexdigest()[:12]
    js_version = hashlib.sha256((SITE / "app.js").read_bytes()).hexdigest()[:12]
    preview = {"en": "PREVIEW RELEASE", "zh-CN": "预览版", "ko": "프리뷰 버전"}[locale] if args.base_url else c["preview"]
    def source(path: str) -> str:
        if args.repository_url and (path.endswith(".md") or path == "LICENSE"):
            return args.repository_url.rstrip("/") + "/blob/main/" + path
        return prefix + path
    nav = "".join(f'<a href="#{anchor}">{e(label)}</a>' for anchor, label in zip(NAV_IDS, c["nav"]))
    ledger = "".join(f'<div class="ledger-line"><span>{n:02}</span><span>{e(label)}</span></div>' for n, label in enumerate(c["ledger_items"], 1))
    facts = "".join(f'<div class="fact"><strong>{e(number)}</strong><span>{e(label)}</span></div>' for number, label in c["facts"])
    def feature_list(items: list, offset: int = 0) -> str:
        return "".join(f'<article class="feature"><span class="feature-number">{i:02}</span><div><h3>{e(title)}</h3><p>{e(body)}</p></div></article>' for i, (title, body) in enumerate(items, 1 + offset))
    cases = "".join(f'<article class="use-case"><h3>{e(title)}</h3><p class="case-situation">{e(situation)}</p><p>{e(outcome)}</p></article>' for title, situation, outcome in c["cases"])
    steps = "".join(f'<article class="how-step"><span>{i:02}</span><h3>{e(title)}</h3><p>{e(body)}</p></article>' for i, (title, body) in enumerate(c["steps"], 1))
    documents = "".join(f'<li><a href="{e(source(path))}"><span><strong>{e(title)}</strong><small>{e(body)}</small></span><span class="arrow" aria-hidden="true">↗</span></a></li>' for title, body, path in c["documents"])
    faqs = "".join(f'<details><summary>{e(question)}</summary><p>{e(answer)}</p></details>' for question, answer in c["faqs"])
    help_links = "".join(f'<a href="{e(source(path))}">{e(label)}<span aria-hidden="true">↗</span></a>' for label, path in c["help_links"])
    return f'''<!doctype html>
<html lang="{locale}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{e(c['title'])}</title>
  {metadata(locale, c, version, args.base_url, args.repository_url, args.social_image_url)}
  <link rel="icon" type="image/svg+xml" href="{e(source('assets/brand/symbol.svg'))}">
  <link rel="stylesheet" href="{site_prefix}styles.css?v={css_version}">
  <script defer src="{site_prefix}app.js?v={js_version}"></script>
</head>
<body>
<a class="skip-link" href="#main">{e(c['skip'])}</a>
<header class="site-header"><div class="header-inner wrap">
  <a class="brand" href="#overview" aria-label="Retulo"><img src="{e(source('assets/brand/wordmark-light.svg'))}" alt="Retulo" width="126" height="36"></a>
  <nav class="top-nav" aria-label="{e(c['nav'][0])}">{nav}</nav>
  <nav class="languages" aria-label="Language">{language_links(locale, content)}</nav>
</div></header>
<main id="main" class="wrap">
  <section class="hero" id="overview" aria-labelledby="hero-title">
    <div><p class="eyebrow"><span class="status-dot" aria-hidden="true"></span>{e(preview)}<span class="version-tag">{e(version)}</span></p>
      <h1 id="hero-title">{lines(c['headline'])}</h1><p class="hero-intro">{e(c['intro'])}</p>
      <div class="hero-actions"><a class="button primary" href="#quick-start">{e(c['start'])}<span class="arrow" aria-hidden="true">↗</span></a><a class="button" href="#use-cases">{e(c['see'])}<span class="arrow" aria-hidden="true">↓</span></a></div>
    </div>
    <aside class="workspace-ledger" aria-label="{e(c['ledger_label'])}"><div class="ledger-top"><p>{e(c['ledger_label'])}</p><h2>{lines(c['ledger_title'])}</h2></div><div class="ledger-lines">{ledger}</div></aside>
  </section>
  <div class="facts" aria-label="Retulo {e(version)}">{facts}</div>
  <section class="section" id="use-cases" aria-labelledby="cases-title">
    <p class="section-label">{e(c['cases_label'])}</p><div class="section-heading"><h2 id="cases-title">{e(c['cases_title'])}</h2><p>{e(c['cases_intro'])}</p></div>
    <div class="use-cases">{cases}</div><p class="note">{e(c['cases_note'])}</p>
  </section>
  <section class="section" aria-labelledby="foundation-title">
    <p class="section-label">{e(c['core_label'])}</p><div class="section-heading"><h2 id="foundation-title">{lines(c['core_title'])}</h2><p>{e(c['core_intro'])}</p></div>
    <div class="features">{feature_list(c['features'])}</div>
    <figure class="diagram"><img src="{e(source('assets/demo/continuity.svg'))}" alt="{e(c['continuity_alt'])}" loading="lazy"><figcaption>{e(c['continuity_caption'])}</figcaption></figure>
    <details class="demo-code"><summary>{e(c['continuity_demo_title'])}</summary>{code_block('continuity-code', CONTINUITY, c)}<p>{e(c['continuity_demo_note'])}</p></details>
  </section>
  <section class="section" id="how-it-works" aria-labelledby="how-title">
    <p class="section-label">{e(c['how_label'])}</p><h2 class="single-heading" id="how-title">{lines(c['how_title'])}</h2><div class="how-steps">{steps}</div><p class="note">{e(c['how_note'])}</p>
  </section>
  <section class="section" id="quick-start" aria-labelledby="quick-title">
    <p class="section-label">{e(c['quick_label'])}</p><div class="section-heading"><h2 id="quick-title">{e(c['quick_title'])}</h2><p>{e(c['quick_intro'])}</p></div>
    <div class="quickstart-grid"><div class="code-section"><h3>{e(c['install_title'])}</h3>{code_block('install-code', install, c)}<p>{e(c['install_note'])}</p></div><div class="code-section"><h3>{e(c['connect_title'])}</h3>{code_block('connect-code', connect, c)}<p>{e(c['connect_note'])}</p></div></div>
    <div class="request-examples"><div><h3>{e(c['after_install'])}</h3><blockquote>{e(c['request'])}</blockquote></div><div><h3>{e(c['continue_label'])}</h3><blockquote>{e(c['continue_request'])}</blockquote></div></div>
    <a class="text-link" href="{e(source('docs/INSTALLATION.md'))}">{e(c['source_guide'])} <span aria-hidden="true">↗</span></a>
  </section>
  <section class="section" id="examples" aria-labelledby="workflow-title">
    <p class="section-label">{e(c['workflow_label'])}</p><div class="section-heading"><h2 id="workflow-title">{lines(c['workflow_title'])}</h2><p>{e(c['workflow_intro'])}</p></div>
    <div class="features workflow-features">{feature_list(c['workflow_features'], 4)}</div>
    <figure class="diagram"><img src="{e(source('assets/demo/workflow.svg'))}" alt="{e(c['workflow_alt'])}" loading="lazy"><figcaption>{e(c['workflow_caption'])}</figcaption></figure>
    <details class="demo-code"><summary>{e(c['demo_title'])}</summary>{code_block('demo-code', DEMO, c)}<p>{e(c['demo_note'])}</p></details>
  </section>
  <section class="section" id="documentation" aria-labelledby="docs-title">
    <p class="section-label">{e(c['docs_label'])}</p><h2 class="single-heading" id="docs-title">{lines(c['docs_title'])}</h2>
    <ul class="document-list">{documents}</ul><div class="faq"><h3>{e(c['faq_title'])}</h3>{faqs}</div>
  </section>
  <section class="section" id="help" aria-labelledby="help-title">
    <p class="section-label">{e(c['help_label'])}</p><div class="help-layout"><div><h2 class="single-heading" id="help-title">{e(c['help_title'])}</h2><p>{e(c['help_intro'])}</p><p class="publication-note">{e(c['publication_note'])}</p></div><nav class="help-links" aria-label="{e(c['nav'][4])}">{help_links}</nav></div>
  </section>
</main>
<footer class="site-footer"><div class="footer-inner wrap"><div class="footer-brand"><img src="{e(source('assets/brand/wordmark-dark.svg'))}" alt="Retulo" width="96" height="30"><p>{e(c['footer'])}</p></div><div class="footer-links"><a href="{e(source('README.md'))}">{e(c['source'])}</a><a href="{e(source('LICENSE'))}">{e(c['license'])}</a></div></div></footer>
<p class="visually-hidden" id="copy-status" role="status" aria-live="polite"></p>
</body>
</html>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Real HTTPS URL of the hosted site directory; omit for local preview")
    parser.add_argument("--repository-url", help="Actual published HTTPS repository URL, if available")
    parser.add_argument("--social-image-url", help="Actual absolute HTTPS social-preview image URL, if available")
    args = parser.parse_args()
    try:
        if args.base_url:
            args.base_url = valid_url(args.base_url, directory=True)
        if args.repository_url:
            args.repository_url = valid_url(args.repository_url)
        if args.social_image_url:
            args.social_image_url = valid_url(args.social_image_url)
        if (args.repository_url or args.social_image_url) and not args.base_url:
            parser.error("--repository-url and --social-image-url require --base-url")
    except ValueError as error:
        parser.error(str(error))
    document = json.loads((SITE / "content.json").read_text(encoding="utf-8"))
    content = document["locales"]
    version = document["version"]
    install = "\n".join(document["installCommands"])
    connect = "\n".join(document["connectCommands"])
    if tuple(content) != LANGUAGES:
        raise ValueError("content.json must contain the English, Simplified Chinese and Korean locales in that order")
    expected_keys = set(content["en"])
    for locale in LANGUAGES:
        if set(content[locale]) != expected_keys:
            raise ValueError(f"Locale {locale} does not have the same content fields as English")
        directory = SITE / LOCALE_PATHS[locale]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "index.html").write_text(render(locale, content[locale], content, version, args, install, connect), encoding="utf-8")
    if args.base_url:
        ET.register_namespace("", "http://www.sitemaps.org/schemas/sitemap/0.9")
        root = ET.Element("{http://www.sitemaps.org/schemas/sitemap/0.9}urlset")
        for locale in LANGUAGES:
            node = ET.SubElement(root, "{http://www.sitemaps.org/schemas/sitemap/0.9}url")
            ET.SubElement(node, "{http://www.sitemaps.org/schemas/sitemap/0.9}loc").text = urljoin(args.base_url, LOCALE_PATHS[locale])
        ET.ElementTree(root).write(SITE / "sitemap.xml", encoding="utf-8", xml_declaration=True)
        index = ["# Retulo", "", "Local workspaces for AI agents. Official documentation pages at the explicitly configured site URL.", ""]
        for locale in LANGUAGES:
            index.append(f"- [{content[locale]['name']}]({urljoin(args.base_url, LOCALE_PATHS[locale])}): {content[locale]['description']}")
        index.extend(("", "This index helps agents find documentation. It does not establish search ranking or approval of generated content.", ""))
        (SITE / "llms.txt").write_text("\n".join(index), encoding="utf-8")
    else:
        for filename in ("sitemap.xml", "llms.txt"):
            path = SITE / filename
            if path.exists():
                path.unlink()
    info = {"version": version, "mode": "hosted-metadata" if args.base_url else "local-preview",
            "base_url": args.base_url, "repository_url": args.repository_url,
            "locales": list(LANGUAGES), "generated_pages": [LOCALE_PATHS[lang] + "index.html" for lang in LANGUAGES]}
    (SITE / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
