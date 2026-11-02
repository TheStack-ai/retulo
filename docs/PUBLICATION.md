# Prepare the GitHub publication

This is the maintainer's publication checklist for **Retulo 0.4.0rc2**. The source
package and local checks are prepared separately from GitHub publication. The public repository is [TheStack-ai/retulo](https://github.com/TheStack-ai/retulo).
Discussions and private vulnerability reporting are enabled. The first [hosted validation](https://github.com/TheStack-ai/retulo/actions/runs/41441041094)
and [documentation deployment](https://github.com/TheStack-ai/retulo/actions/runs/41441041053)
passed. The candidate release is linked below.

## Repository metadata

Published owner and name: **TheStack-ai/retulo**.

**About description**

> Local workspaces for AI agents. Preserve file versions, continue tasks across conversations, rebuild affected steps, and reuse workflows through CLI and MCP.

**Topics**

`ai-agents` · `model-context-protocol` · `codex` · `workflow-management` ·
`incremental-workflows` · `local-first` · `python` · `developer-tools`

Use English as the primary README and link **English · 简体中文 · 한국어** near the
top. Keep the same feature scope and support facts in all three versions. Add the
actual published documentation URL to the repository's Website field only after
that site is available.

Repository destinations:

```text
Repository: https://github.com/TheStack-ai/retulo
RC release: https://github.com/TheStack-ai/retulo/releases/tag/v0.4.0rc2
CI workflow: https://github.com/TheStack-ai/retulo/actions/workflows/ci.yml
Discussions: https://github.com/TheStack-ai/retulo/discussions
Private reporting: https://github.com/TheStack-ai/retulo/security/advisories/new
```

## Before the first upload

- [ ] Confirm the exact owner, repository name, visibility, and publication instruction.
- [ ] Review the three README versions, logo, shared preview image, and actual demo.
- [ ] Check relative document and asset links in the source archive as well as the checkout.
- [ ] Check public source and both distribution formats for unwanted private or runtime files.
- [ ] Confirm MIT and all required dependency notices are included.
- [ ] Retain the earlier source and distribution package before preparing a new one.
- [ ] Reproduce the documented quick start with the intended release package and a fresh environment.

Use [qualification](QUALIFICATION.md) for actual local results. A prepared GitHub
Actions workflow is not a hosted pass.

## Repository settings after upload

- [ ] Set the About description, topics, and published Website URL.
- [ ] Upload the GitHub social preview image. Use 1280 × 640 pixels and a file below 1 MB.
- [ ] Check the README at desktop and phone widths in light and dark themes.
- [ ] Open **Issues → New issue** and check the Bug, Feature, and Documentation or usage question forms.
- [ ] Create only the labels listed below, then attach labels to templates where appropriate.
- [ ] Enable private vulnerability reporting and confirm the Security tab exposes the intended private route.
- [ ] Update SECURITY.md with the route actually available, keeping the fallback if needed.
- [ ] Run the checked-in `ci.yml` on the uploaded branch and inspect its actual result.
- [ ] Decide whether to enable Discussions using the small category set below.

Start with these eight labels: `bug`, `enhancement`, `documentation`,
`needs-reproduction`, `needs-design`, `help wanted`, `good first issue`, and `blocked`.
The templates do not assume that these labels already exist. Use `good first issue`
only for a scoped task with a location, reproduction or example, and completion criteria.

For Discussions, start with **Q&A**, **Recipes & showcases**, and **Announcements**.
Keep the documentation issue form as a working question route until Q&A is enabled.
After checking the actual category URLs, add the live contact links to
`.github/ISSUE_TEMPLATE/config.yml` and update [Community](COMMUNITY.md). Avoid
advertising an inactive Discord server, email address, or support deadline.

## Badges and release links

Keep the initial badge row small: **preview version**, **checks**, **Python**, and
**MIT**. Each badge must say what its evidence supports.

- The version badge can identify the prepared `0.4.0rc2` candidate before upload, linking to the local release notes. Switch its link to the exact published RC afterward.
- Before a hosted run exists, label checks as local-only or pending publication. Never display a GitHub CI success based on a local run.
- After the hosted run, use GitHub's status badge for `ci.yml`, with the intended branch and event explicit.
- Python `≥3.11` expresses the requirement; link the verified environment table rather than suggesting every Python/OS combination was tested.
- Link MIT to LICENSE. Keep Stars, downloads, and coverage out of the first row unless they answer a useful question with actual data.

For a dynamic release badge, include prereleases and use date sorting for this PEP
440 RC tag. Shields supports `include_prereleases` and `sort=date`; verify that the
rendered badge displays the intended tag. Link downloads directly to
`/releases/tag/v0.4.0rc2`. A GitHub prerelease cannot be marked as the latest stable
release, so `/releases/latest` is not the first RC download destination.

## Publish the candidate

- [ ] Confirm the remote CI result belongs to the release source revision.
- [ ] Create the `v0.4.0rc2` tag for that exact revision and publish it as a prerelease.
- [ ] Attach the inspected source and wheel packages and their checksums.
- [ ] Include installation instructions, the actual demo, support scope, and known limits in the release notes.
- [ ] Test the exact download links and installation from the downloaded source package.
- [ ] Confirm the preview wording and badge targets match the published state.

The wheel contains the CLI/MCP core. The documented Codex integration is installed
from the source checkout. Do not advertise `pip install retulo` before the intended
package has actually been published and checked on PyPI.

## First four weeks

Record the publication date and review installation problems, usable bug reports,
questions, and independently completed examples each week. GitHub traffic has a
short reporting window, so retain weekly observations if they will be useful.
For a published documentation site, review indexing and search performance in
Search Console. Use real questions and linked answers when assessing AI search
visibility; a logo, a badge, or an `llms.txt` file does not establish search ranking.

## Official references

- [GitHub repository topics](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics)
- [GitHub social preview specifications](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview)
- [GitHub workflow status badges](https://docs.github.com/en/actions/how-tos/monitor-workflows/add-a-status-badge)
- [GitHub release creation and latest-release behavior](https://docs.github.com/en/rest/releases/releases#create-a-release)
- [Shields GitHub release badge options](https://shields.io/badges/git-hub-release)
- [GitHub private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/working-with-repository-security-advisories/configuring-private-vulnerability-reporting-for-a-repository)
