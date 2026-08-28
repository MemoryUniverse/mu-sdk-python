# Releasing — proposed convention

**Status: a proposal, not yet in force.** There is **no git tag** in this repository and nothing
published. A maintainer should ratify or amend this before the first tag is cut.

## The blocker that has to be decided first: the name

**`mu-sdk` is taken on PyPI.** `pypi.org/pypi/mu-sdk` returns `200` for an unrelated project
(verified 2026-08-28). This package cannot be published under the name it currently declares in
`pyproject.toml`.

What is free, checked the same day: `mu-core`, `mu-contracts`, `mu-engine`, `mu-local`,
`mu-engine-server`, `mu-client` on PyPI — and `mu-sdk` on **npm**, which the JavaScript SDK does
claim. So a rename here also creates an asymmetry between the two SDKs' package names, which is
itself a decision (do both rename to stay symmetrical, or does only Python move?).

This is not a packaging chore. It propagates into: this repo's `pyproject.toml`, `mu-core`'s
`acceptance` dependency group, both SDK READMEs, every example, and the LangGraph demo. **Decide
the name before writing any of the release steps below.**

## Versioning

SemVer, `v`-prefixed annotated tags: `v0.1.0`. Pre-1.0: a **minor** bump may break the client API
or the wire shapes it speaks; a **patch** bump never does. No compatibility promise before
`v1.0.0`.

The Python and JavaScript SDKs should carry **the same minor version** when they speak the same
wire surface, so that `0.3.x` means one thing in both languages. That is a convention, not a
mechanism — nothing enforces it, which is exactly why it needs writing down.

## The other blockers

- **Path dependency on `mu-core`.** `mu-contracts` (and `mu-local`, for the `[embedded]` extra) are
  filesystem path dependencies. A wheel built from this tree declares dependencies that resolve on
  one machine. `mu-core` must publish first; then these become version ranges
  (`mu-contracts>=0.1,<0.2`), re-locked, with a clean single-repo clone proven to sync.
- **No `py.typed`.** This package ships no `py.typed` marker, so a typed consumer gets `Any` from a
  package whose selling point is a typed client. All four `mu-core` distributions ship one; this
  one does not. Add it and include it in the wheel before the first release.
- **`uv.lock` is stale** — `uv sync --locked` fails today. Fix with `uv lock` and restore `--locked`
  to CI.
- **Four files fail `ruff format --check`** at HEAD. One command, one commit.

## The procedure

1. Name decided; `mu-core` published and tagged; path deps replaced with version ranges; `py.typed`
   shipped; lockfile refreshed; formatting fixed. CI green, all six gates.
2. `chore(release): v0.1.0` — bump `version` in `pyproject.toml`, `uv lock`, one commit.
3. `git tag -a v0.1.0 -m "mu-sdk-python v0.1.0"`.
4. Push commit, then tag.
5. GitHub Release from the tag; notes grouped by Conventional-Commit type, breaking changes first.
6. Publish to PyPI **via Trusted Publishing** (OIDC), not a long-lived API token in a repository
   secret. That configuration is done on PyPI against this repo and workflow, and must be set up
   before the publishing workflow is written.
7. Attach the sdist and wheel from the `build` job.

Steps 3-7 stay manual until a human has done the first release once. A publishing workflow written
before its first successful manual run is an untested script wired to a trigger.

## What a tag here does not claim

That the hosted plane accepts every route this client can speak. The integration tier proves this
client against **the bundled conformance server**, which is a faithful stand-in and not the real
deployment. Wire conformance against a live server is a separate claim, and belongs in the release
notes as prose.
