## What this changes

<!-- One paragraph. What is different after this PR that was not true before it? -->

## Why

<!-- The problem, not the patch. Fixes #123 -->

## Does this change the wire surface?

- [ ] No — internal only.
- [ ] Yes. Then: what does the server now have to accept or return, and **has the matching change
      been made in `mu-sdk-js`?** Link it. Two SDKs that disagree about the wire are one bug in
      two places.

## How to see it fail without this change

<!-- Name the test, or paste the command and the output you saw before the fix. -->

## Gates

Run locally, with `mu-core` checked out as a sibling on `dev/mlm-build`:

- [ ] `uv run --no-sync ruff check .`
- [ ] `uv run --no-sync ruff format --check .`
- [ ] `uv run --no-sync lint-imports`
- [ ] `uv run --no-sync mypy src`
- [ ] `uv run --no-sync pytest -m "not integration"`
- [ ] `uv run --no-sync pytest -m integration` (real uvicorn conformance server — no containers needed)

> Two gates are known-red at HEAD for reasons documented in CONTRIBUTING.md: `ruff format --check`
> (4 unformatted files) and the stale `uv.lock`. If your PR is the one that fixes either, say so —
> it is a headline, not a chore.

## Checks that are not automatable

- [ ] No new module-scope import of `mu_engine` / `mu_local` / `mu_server` / `mu_client`.
- [ ] **No token, credential or memory content** in any log, trace, metric, exception message or
      retry path this PR adds or changes.
- [ ] New I/O is async, has a timeout, and handles cancellation.
- [ ] Any new test can actually fail — I mutated the line it guards and watched it go red.
- [ ] Nothing here weakens or disables a gate.

## Anything a reviewer should push back on

<!-- Shortcuts, open questions, wire-shape decisions you are unsure about. -->
