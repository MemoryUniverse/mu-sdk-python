# Security policy

`mu-sdk` is a client library: it runs inside someone else's application, holds their credentials,
and carries their users' data over the wire. A vulnerability here is a vulnerability in every
application that depends on it.

## Reporting a vulnerability

**Do not open a public issue.** Use GitHub's private vulnerability reporting:
**[Security → Report a vulnerability](https://github.com/MemoryUniverse/mu-sdk-python/security/advisories/new)**.

If that form is unavailable to you, open a normal issue containing only *"I need a private channel
for a security report"* — **no details** — and a maintainer will open an advisory and invite you.

## What to include

- What an attacker can do, and what they must already control (the server? the network? a response
  body? a config file?).
- The smallest reproduction. A failing test against the bundled conformance server is ideal.
- The commit you saw it on.

**Never include real tokens, memory content or personal data.** Redact, and say what you redacted.

## What to expect

| | Target |
|---|---|
| Acknowledgement | within 3 working days |
| First assessment | within 10 working days |
| Fix or a dated plan | agreed with you on the advisory |

Credit in the advisory unless you ask us not to.

## Supported versions

**None yet.** No git tag, and nothing published under this project's name. Note for anyone
searching: **the PyPI package `mu-sdk` is not ours** — that name was already taken by an unrelated
project, and resolving the collision is an open blocker (see [RELEASING.md](RELEASING.md)). Do not
install `mu-sdk` from PyPI expecting this code.

## Scope

Especially in scope:

- A token, API key or credential reaching a log line, a trace, a metric label, an exception message
  or a retry warning.
- Memory content reaching any of the same places.
- TLS verification that can be disabled by configuration, or a transport that falls back to plain
  HTTP without saying so.
- A malicious or compromised **server response** that can make this client do something worse than
  raise: deserialization, path writes, unbounded allocation, or an error path that leaks the
  request it was retrying.
- Credentials read from, or written to, an unexpected location by the token auto-load path.
- Any way the `[embedded]` transport lets the engine be reached when the extra is not installed and
  the caller did not ask for it.

Out of scope: third-party dependency advisories with no exploitable path through this code (report
upstream, tell us so we can pin), and the hosted plane, which is not in this repository.
