# Adoption snapshots

Maintainers can take a weekly local snapshot of the signals that precede stars:

```bash
gh auth status
python scripts/adoption_snapshot.py
```

The command requires a GitHub token that can read repository traffic for
`whyy9527/ariadne`. It reads:

- GitHub clone totals and unique cloners over GitHub's rolling 14-day window.
- PyPI Stats downloads over 1, 7, and 30 days.
- GitHub issues opened in the last 30 days, separated into external,
  maintainer-authored, and bot-authored counts.

Snapshots are written as Markdown and JSON under the gitignored
`adoption-snapshots/` directory. They contain aggregate counts and timestamps,
not GitHub tokens, credentials, source code, repository contents, or individual
traffic identities.

Clone and download totals can include CI, mirrors, bots, cache refreshes, and
repeated installs. Read them alongside external issues and the opt-in feedback
form. Stars may be useful context, but they are not the primary success metric.

This script is a maintainer tool. Ariadne's CLI and MCP server do not import or
invoke it, and Ariadne sends no runtime telemetry.
