---
title: "Lesson 5: Dependency Auditing — cargo-audit and cargo-deny"
author: Atharva Pandey
series: "Rust Security in Production"
lesson: 5
keywords: [Rust, rust, security, cargo-audit, cargo-deny, dependencies, vulnerabilities, RustSec]
tags: [Rust tutorial, rust, security]
date: '2025-05-09T14:55:00.000Z'
---

You know what keeps me up at night? Not my own code — I can review that. It's the 200+ transitive dependencies in my `Cargo.lock` that I've never read a single line of. Every one of those crates runs with the same permissions as my code. If any of them has a vulnerability, it's my vulnerability.

This isn't theoretical. In 2024, the `xz` backdoor showed that even core infrastructure maintained by a single person can be compromised. Rust's ecosystem isn't immune. We've had actual advisories for real crates — buffer overflows in parsing libraries, unsound `unsafe` code in popular crates, logic bugs in crypto implementations.

The good news: Rust has better tooling for dependency auditing than most ecosystems. The bad news: most people don't use it.

## cargo-audit — The First Line of Defense

`cargo-audit` checks your `Cargo.lock` against the RustSec Advisory Database, which is a curated list of known vulnerabilities in Rust crates.

### Installation and Basic Usage

```bash
cargo install cargo-audit
```

```bash
# Check for known vulnerabilities
cargo audit

# Output looks like this when it finds something:
# Crate:         smallvec
# Version:       0.6.9
# Title:         Buffer overflow vulnerability in SmallVec::insert_many
# Date:          2019-06-06
# ID:            RUSTSEC-2019-0009
# URL:           https://rustsec.org/advisories/RUSTSEC-2019-0009
# Solution:      Upgrade to >=0.6.10
```

### Running in CI

This is non-negotiable. Every CI pipeline should include `cargo audit`. Here's a GitHub Actions step:

```yaml
# .github/workflows/security.yml
name: Security Audit

on:
  push:
    branches: [main]
  pull_request:
  schedule:
    # Run daily at 6am UTC — catches new advisories
    - cron: '0 6 * * *'

jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: rustsec/audit-check@v2
        with:
          token: ${{ secrets.GITHUB_TOKEN }}
```

The scheduled run is important. A dependency you shipped last week might get an advisory today — you want to know immediately, not when someone happens to push a commit.

### Handling False Positives and Advisories You Can't Fix

Sometimes an advisory applies to a feature you don't use, or the fix requires a breaking change from an upstream crate you can't control. Use an audit config file:

```toml
# .cargo/audit.toml

[advisories]
# Advisories you've investigated and determined don't affect you
ignore = [
    "RUSTSEC-2023-0001",  # Only affects feature X which we don't use
]

# You can also set severity thresholds
# informational_warnings = ["unmaintained"]

[database]
# Fetch the advisory database from this URL
# (default is the RustSec repo)
# url = "https://github.com/RustSec/advisory-db"

# Ensure the advisory DB is recent
stale = true
```

**Important rule:** every ignored advisory gets a comment explaining *why* it's ignored. No uncommented ignores in code review.

## cargo-deny — The Comprehensive Policy Engine

`cargo-deny` goes far beyond vulnerability checking. It's a policy enforcement tool that checks:

1. **Advisories** — like `cargo-audit` but integrated
2. **Licenses** — ensure all dependencies use approved licenses
3. **Bans** — block specific crates or versions
4. **Sources** — ensure crates come from trusted registries
5. **Duplicates** — warn about multiple versions of the same crate

### Installation and Setup

```bash
cargo install cargo-deny

# Generate a config file
cargo deny init
```

This creates a `deny.toml` file. Let me walk through each section.

### Advisory Configuration

```toml
# deny.toml

[advisories]
# The path to the advisory database
db-path = "~/.cargo/advisory-db"
# The URL of the advisory database
db-urls = ["https://github.com/rustsec/advisory-db"]
# How to handle advisory vulnerabilities
vulnerability = "deny"
# How to handle unmaintained crates
unmaintained = "warn"
# How to handle advisories that are informational
notice = "warn"
# How to handle yanked crates
yanked = "deny"
# Advisories to ignore
ignore = []
```

### License Checking

This is where `cargo-deny` really shines. You define which licenses are acceptable, and it rejects anything else:

```toml
[licenses]
# Reject unlicensed crates
unlicensed = "deny"

# List of allowed licenses
allow = [
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "Unicode-DFS-2016",
    "Zlib",
]

# What to do if a license isn't in the allow list
default = "deny"

# Some crates have non-standard license expressions
# or exceptions you need to handle
exceptions = [
    # ring uses a custom license combining ISC, MIT, and OpenSSL
    { allow = ["ISC", "MIT", "OpenSSL"], name = "ring", version = "*" },
]

# Clarify crates whose license is ambiguous
[[licenses.clarify]]
name = "encoding_rs"
version = "*"
expression = "(Apache-2.0 OR MIT) AND BSD-3-Clause"
license-files = [{ path = "COPYRIGHT", hash = 0x39f8_ad31 }]
```

I've seen teams ship code that transitively depended on a GPL-licensed crate in a proprietary product. The license check caught it in CI. Without it, that's a legal problem waiting to happen.

### Banning Specific Crates

Sometimes you want to explicitly ban a crate — maybe it's known to be poorly maintained, or you have an internal replacement:

```toml
[bans]
# What to do if multiple versions of the same crate are found
multiple-versions = "warn"
# Highlight the highest version to help resolve duplicates
highlight = "lowest-version"

# Specific crates to deny
deny = [
    # Deprecated in favor of our internal crate
    { name = "old-http-client", wrappers = [] },

    # Known unsound unsafe code, hasn't been fixed
    { name = "sketchy-parser", wrappers = [] },
]

# Allow specific crates (deny everything else)
# This is the whitelist approach — strict but thorough
# allow = [...]

# Skip checking certain duplicate crates that are hard to unify
skip = [
    # syn 1.x and 2.x are commonly both present during transition
    { name = "syn", version = "=1" },
]
```

### Source Verification

Ensure all crates come from crates.io (or your approved registry), not random git repos:

```toml
[sources]
# Refuse crates from unknown registries
unknown-registry = "deny"
# Refuse crates from git repos (they bypass audit)
unknown-git = "deny"

# If you have a private registry
[sources.allow-registry]
# https://my-company-registry.example.com
```

Git dependencies are a real risk. They bypass the crates.io publish process, can change at any time (if you didn't pin a rev), and aren't checked by `cargo-audit`. If you must use a git dependency, pin it to a specific commit hash.

### Running cargo-deny

```bash
# Check everything
cargo deny check

# Check only advisories
cargo deny check advisories

# Check only licenses
cargo deny check licenses

# Check only bans
cargo deny check bans

# Check only sources
cargo deny check sources
```

### CI Integration

```yaml
# .github/workflows/deny.yml
name: Dependency Policy

on:
  push:
    branches: [main]
  pull_request:

jobs:
  deny:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        checks:
          - advisories
          - licenses
          - bans
          - sources
    steps:
      - uses: actions/checkout@v4
      - uses: EmbarkStudios/cargo-deny-action@v2
        with:
          command: check ${{ matrix.checks }}
```

Running each check as a separate matrix job means you get clear, independent failure messages. You know immediately whether you have a license problem versus a vulnerability.

## Analyzing Your Dependency Tree

Before you can audit effectively, you need to understand what you actually depend on. Most of your dependencies are transitive — pulled in by crates you directly depend on.

```bash
# Show the full dependency tree
cargo tree

# Show why a specific crate is included
cargo tree -i smallvec
# Output shows which of YOUR dependencies pulled in smallvec

# Show duplicated dependencies (different versions of same crate)
cargo tree --duplicates

# Show dependencies with specific features enabled
cargo tree -f '{p} {f}'

# Count your total dependencies
cargo tree --prefix none | sort -u | wc -l
```

Understanding *why* each dependency exists helps you make informed decisions. Maybe you pulled in a huge framework crate for one function — you could replace it with a few lines of hand-written code and eliminate 30 transitive dependencies.

## Reducing Your Attack Surface

Every dependency is an attack surface. Here's my approach to minimizing it:

**1. Audit direct dependencies before adding them.**

```bash
# Before adding a crate, check it out:
# - How many downloads? (low downloads = higher risk)
# - How many maintainers? (bus factor)
# - Last commit? (unmaintained = unpatched)
# - Any unsafe code?

cargo install cargo-crev
cargo crev query review <crate-name>
```

**2. Use feature flags to minimize what you pull in.**

```toml
# Don't do this — pulls in everything
serde = { version = "1", features = ["derive"] }
reqwest = "0.12"

# Do this — only what you need
serde = { version = "1", features = ["derive"], default-features = false }
reqwest = { version = "0.12", default-features = false, features = ["json", "rustls-tls"] }
```

Disabling default features can eliminate entire dependency subtrees. For `reqwest`, turning off `default-features` and explicitly choosing `rustls-tls` instead of `native-tls` avoids linking to OpenSSL.

**3. Periodically prune unused dependencies.**

```bash
cargo install cargo-udeps
cargo +nightly udeps
```

`cargo-udeps` finds crates in your `Cargo.toml` that your code doesn't actually use. It's not uncommon to find leftovers from removed features.

**4. Pin important dependencies.**

For security-critical crates, consider pinning to exact versions and reviewing diffs before upgrading:

```toml
# Pin security-critical deps
ring = "=0.17.7"
rustls = "=0.23.5"
```

This means you don't get automatic patch upgrades, but you also don't get surprise changes. Review each upgrade manually.

## A Complete deny.toml for Production

Here's a `deny.toml` that represents what I'd use for a production service:

```toml
[graph]
targets = []
all-features = false
no-default-features = false

[output]
feature-depth = 1

[advisories]
vulnerability = "deny"
unmaintained = "warn"
yanked = "deny"
notice = "warn"
ignore = []

[licenses]
unlicensed = "deny"
allow = [
    "MIT",
    "Apache-2.0",
    "Apache-2.0 WITH LLVM-exception",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "Unicode-DFS-2016",
    "Unicode-3.0",
    "Zlib",
    "BSL-1.0",
    "0BSD",
]
default = "deny"
confidence-threshold = 0.8
exceptions = []

[bans]
multiple-versions = "warn"
wildcards = "allow"
highlight = "lowest-version"
workspace-default-features = "allow"
external-default-features = "allow"
allow = []
deny = []
skip = []
skip-tree = []

[sources]
unknown-registry = "deny"
unknown-git = "deny"
allow-registry = ["https://github.com/rust-lang/crates.io-index"]
allow-git = []
```

## What I Tell New Team Members

Your `Cargo.lock` is a list of code you're shipping to production. Every entry is code that runs with your permissions, accesses your data, and talks to your network. Treat it with the same scrutiny you'd give a new hire's first PR.

Run `cargo audit` in CI. Run `cargo deny` in CI. Review your dependency tree quarterly. Remove what you don't need. Pin what's critical. And when `cargo audit` tells you there's a vulnerability — drop what you're doing and fix it. That advisory is public. Attackers read RustSec too.
