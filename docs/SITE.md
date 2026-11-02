# Documentation site

The package includes English, Simplified Chinese, and Korean static pages. They
use readable HTML text, local assets, language links, and the same feature scope
as the README. The default build is a local preview, with no invented official
domain or repository URL.

## Preview locally

From the source directory:

```sh
python3 scripts/build_site.py
python3 -m http.server 8000 --bind 127.0.0.1
```

Open `http://127.0.0.1:8000/site/`, `/site/zh-CN/`, or `/site/ko/` in your browser.
Keep the repository root as the server root: the pages link to the sibling
`assets/`, `docs/`, README, and license files. The builder does not start a server
or deploy the site. CSS and JavaScript URLs include their current content hashes
so a rebuilt page loads the matching assets.

## GitHub Pages deployment

The public source repository is [TheStack-ai/retulo](https://github.com/TheStack-ai/retulo).
The documentation destination is
[thestack-ai.github.io/retulo/site/](https://thestack-ai.github.io/retulo/site/),
with Chinese at `site/zh-CN/` and Korean at `site/ko/`. The repository root URL
redirects to `site/`.

[The Pages workflow](../.github/workflows/pages.yml) runs on pushes to `main`
and manual dispatch. In the repository's **Settings → Pages**, use **GitHub
Actions** as the build source. The workflow builds with Python 3.11 and its
standard library, uploads a Pages artifact, then deploys it to the `github-pages`
environment. No package or JavaScript dependencies are installed for this build.

The build job can read the repository and Pages configuration. Only the deploy
job has `pages: write` and `id-token: write`. The official actions are pinned to
commit SHAs, and the `pages` concurrency group cancels superseded runs. Inspect
the workflow run and environment deployment before reporting a release as live.

The deployed artifact contains `site/`, the shared `assets/` directory, and a
root HTML redirect. It does not publish the source checkout or runtime workspaces.
The build command is:

```sh
python3 scripts/build_site.py \
  --base-url https://thestack-ai.github.io/retulo/site/ \
  --repository-url https://github.com/TheStack-ai/retulo \
  --social-image-url https://thestack-ai.github.io/retulo/assets/brand/social-preview.png
```

This emits per-language canonical and hreflang links, Open Graph metadata,
`SoftwareSourceCode` JSON-LD, a three-page sitemap, and an `llms.txt`
documentation index. The site URL is the directory containing `site/index.html`.
The builder rejects credentials, query strings, fragments, and non-HTTPS metadata
URLs. A hosted build with `--repository-url` links Markdown documents, README
files, and the license to readable `blob/main` pages on GitHub. Images retain
their relative paths so every locale can use the shared deployed assets.

After deployment, check the actual hosted URLs, asset responses, language links,
sitemap, and metadata. Markdown links open source documents; the builder does not
convert the whole technical manual into a separate documentation application.
Run `python3 scripts/build_site.py` after any local hosted-build check to return
the checked-in files to local-preview mode. CI builds its hosted files in the
runner checkout and does not commit them back to `main`.

## Search and AI discovery

GitHub About, topics, README, Issues, and releases are managed separately; see
[Publication](PUBLICATION.md). A repository does not give its owner control over
GitHub's page headers or root robots policy.

For a hosted site, use the real visible facts in metadata and keep actual demos,
methods, version, and limitations accessible as text. Check the hosting/CDN
policy for OAI-SearchBot separately from GPTBot: they serve different purposes.
An `llms.txt` index is optional and does not establish search ranking or indexing.

Deployment alone does not establish indexing, Search Console results, external AI
citations, traffic, or ranking. Check and report those separately.

References: [Google SEO basics](https://developers.google.com/search/docs/fundamentals/seo-starter-guide),
[Google AI search guidance](https://developers.google.com/search/docs/fundamentals/ai-optimization-guide),
[OpenAI crawlers](https://developers.openai.com/api/docs/bots),
[SoftwareSourceCode](https://schema.org/SoftwareSourceCode).
