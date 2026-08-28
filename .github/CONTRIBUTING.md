# Contributing to mu-sdk-python

`mu-sdk` is a **thin async wire client** — `add` / `search` / `recall` / `get` / `buildContext` /
the lifecycle verbs — and nothing else. No engine, no stores, no strategies, no embedder. That
constraint is the package: it is what lets an application depend on this without pulling in torch.
It is enforced by an `import-linter` contract, not by good intentions.

## Setup — you need two repositories

This SDK depends on `mu-contracts` by **filesystem path** (`../mu-core/packages/mu-contracts`), and
the optional `[embedded]` extra adds `mu-local` the same way. A clone of this repo on its own
cannot resolve either.

```bash
git clone https://github.com/MemoryUniverse/mu-core.git
git clone https://github.com/MemoryUniverse/mu-sdk-python.git
cd mu-core && git checkout dev/mlm-build && cd ../mu-sdk-python
uv sync --extra dev --extra embedded
```

`git checkout dev/mlm-build` is load-bearing: `mu-core`'s GitHub default branch is `main`, but its
integration trunk — the branch this SDK is developed against — is `dev/mlm-build`. CI does the same
checkout; it is one `env:` line (`MU_CORE_REF`) in [`ci.yml`](workflows/ci.yml).

`--extra embedded` is not optional comfort. `mu_sdk.transport` imports `mu_local` / `mu_engine`
**lazily** (inside `__init__`, never at module scope) so that the default install stays engine-free;
mypy has no notion of "lazy" and reports 20 module-not-found errors without the extra installed.
Installing it does **not** weaken the boundary: `lint-imports` still passes, because the contract
allowlists exactly those two lazy edges by name and catches every other one.

## The gates — run them before you push

Exactly what CI runs:

```bash
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync lint-imports
uv run --no-sync mypy src
uv run --no-sync pytest -m "not integration"
uv run --no-sync pytest -m integration
```

`--no-sync` matters more here than anywhere else in the project. Without it, `uv run` re-resolves
the environment and can fall back to a tool that is *not* the pinned one — measured: a bare
`uv run ruff` gave **0.15.10** instead of the pinned **0.8.6**, and the two disagree about
formatting. A green check from the wrong tool is worse than a red one from the right tool.

Measured on a clean two-repo checkout:

| Gate | Result |
|---|---|
| `ruff check .` | `All checks passed!` |
| `ruff format --check .` | **FAILS — 4 files, see below** |
| `lint-imports` | `Contracts: 1 kept, 0 broken.` (34 files, 85 dependencies) |
| `mypy src` | `Success: no issues found in 15 source files` (with `--extra embedded`) |
| `pytest -m "not integration"` | `92 passed, 37 deselected in 4.79s` |
| `pytest -m integration` | `37 passed, 92 deselected in 30.92s` |
| `uv build` | sdist + wheel |

## Two known-red things, stated rather than hidden

**1. `ruff format --check` fails at HEAD.** Four files are unformatted under the pinned ruff 0.8.6:

```
src/mu_sdk/client.py
tests/unit/test_client.py
tests/integration/test_embedded_transport_namespace_parity.py
tests/integration/test_middleware_auto_inject_capture.py
```

One command fixes all four: `uv run --no-sync ruff format .`. Until someone commits that, this
step is red on every run. The gate was left in place on purpose — dropping it would have converted
a one-command defect into an invisible one.

**2. `uv.lock` is stale, so CI cannot use `--locked`.** `uv sync --locked` currently fails with
*"The lockfile at `uv.lock` needs to be updated"*. The lock predates three dependencies that
arrived transitively through `mu-core` (`authlib`, `cryptography`, `cffi`, via `mu-engine`'s
`weaviate-client`). `uv lock`, committed, fixes it — and then `--locked` should be added back to
the workflow so the next drift is caught rather than tolerated. If you find that
`uv sync --locked --extra dev --extra embedded` already succeeds on your checkout, that fix has
landed: put `--locked` back in [`ci.yml`](workflows/ci.yml) and delete this paragraph.

## The integration tier runs in CI, and it mocks nothing

Unlike the rest of the project, this repo's `integration` tests need no containers: they start the
**real uvicorn conformance server** and talk to it with **real httpx over real TCP**. 37 tests,
about 30 seconds, zero mocks. That is why CI gates on them. If you change the wire surface, they
are the tests that will tell you.

## Conventions

- **Commits** follow Conventional Commits: `fix(client): …`, `feat(transport): …`, `!` for
  breaking.
- **Never import `mu_engine`, `mu_local`, `mu_server` or `mu_client` at module scope.** The
  `[embedded]` transport's two lazy imports are the only exceptions and they are allowlisted by
  name; a new one will fail `lint-imports`, and correctly.
- **The wire surface is a contract shared with `mu-sdk-js`.** A change to a request or response
  shape here that is not mirrored there is a bug in both.
- **No memory content in logs, traces, metrics or error messages** — including in exception text
  and retry logging. This client sits in someone else's application; what it emits, they inherit.
- **Tests must be able to fail.** Mutate the line your new test guards, watch it go red, then
  revert.

## Licensing

By contributing you agree that your contributions are licensed under the
[Apache License 2.0](../LICENSE).
