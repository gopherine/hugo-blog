---
title: "Lesson 9: Dependency Scanning — govulncheck before you deploy"
author: Atharva Pandey
series: "Go Security in Production"
lesson: 9
keywords:
  - Go
  - golang
  - security
tags:
  - Go tutorial
  - golang
  - security
date: '2025-05-05T00:00:00.000Z'
---

The security posture of your Go application is not just about the code you write — it is also about the code you import. A typical Go microservice will have dozens of direct and transitive dependencies. Any one of them might have a known vulnerability in the specific version you are using. Unlike the bugs in your own code, these vulnerabilities are publicly catalogued, exploits are often published, and attackers scan for them at scale.

I have run `govulncheck` on codebases I inherited and found critical vulnerabilities in HTTP libraries, JWT packages, and database drivers — not because the teams were careless, but because they updated dependencies irregularly and had no automated scanning in place. Finding a critical vulnerability in a library is not embarrassing. Finding it in a post-mortem is.

## The Problem

The most common failure is simply having no dependency scanning at all — dependencies are added once and never reviewed:

```go
// WRONG — go.mod with pinned old versions, never updated or checked
module myapp

go 1.21

require (
    github.com/dgrijalva/jwt-go v3.2.0+incompatible  // deprecated, known vulnerabilities
    golang.org/x/crypto v0.0.0-20200622213623         // old, missing security patches
    github.com/gin-gonic/gin v1.7.0                   // older version with CVEs
)
```

None of these will fail to compile or cause obvious runtime errors. The vulnerability is silent until it is exploited.

A second failure pattern is scanning only at release time, rather than continuously:

```go
// WRONG — security as a one-time gate rather than a continuous check
// Manual process:
// 1. Developer writes code
// 2. Code ships to production
// 3. ... three months later ...
// 4. Security team runs a scan
// 5. Finds vulnerabilities in dependencies added six months ago
// By this point the vulnerability has been in production for months
```

The third failure is responding to findings by suppressing them rather than fixing or tracking them:

```bash
# WRONG — ignoring findings without investigation
govulncheck ./... 2>/dev/null  # pipe to /dev/null to make the CI step "pass"
```

## The Idiomatic Way

Install and run `govulncheck` as part of your standard development and CI workflow:

```bash
# Install
go install golang.org/x/vuln/cmd/govulncheck@latest

# Run against all packages in the module
govulncheck ./...

# Example output:
# Vulnerability #1: GO-2023-1988
# A maliciously crafted HTTP/2 stream could cause excessive CPU consumption
# in the HPACK decoder, sufficient to cause a denial of service from a
# single connection.
#   More info: https://pkg.go.dev/vuln/GO-2023-1988
#   Module: golang.org/x/net
#     Found in: golang.org/x/net@v0.8.0
#     Fixed in: golang.org/x/net@v0.17.0
#     Call stacks in your code:
#       main.go:12:14: myapp/main.go calls net/http.ListenAndServe
```

`govulncheck` is notable because it only reports vulnerabilities that are actually called in your code — not just imported. This dramatically reduces false positives compared to tools that flag every dependency regardless of usage.

Add it to your CI pipeline as a mandatory step that blocks deployment:

```yaml
# RIGHT — govulncheck in GitHub Actions CI
name: Security Scan

on:
  push:
    branches: [main]
  pull_request:
  schedule:
    - cron: '0 8 * * 1'  # Run every Monday morning

jobs:
  vuln-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-go@v5
        with:
          go-version: '1.22'

      - name: Install govulncheck
        run: go install golang.org/x/vuln/cmd/govulncheck@latest

      - name: Run govulncheck
        run: govulncheck ./...
        # Non-zero exit code if vulnerabilities are found — fails the CI step
```

The scheduled cron is crucial: new vulnerabilities are discovered in existing library versions continuously. Running only on push means a dependency you added last month could have a new CVE published today, and you would not know until someone checks.

For updating dependencies once vulnerabilities are found:

```bash
# RIGHT — targeted dependency update
go get golang.org/x/net@latest        # update specific vulnerable package
go mod tidy                            # clean up go.sum
govulncheck ./...                      # verify the fix resolved the finding

# Or update all direct dependencies
go get -u ./...
go mod tidy
govulncheck ./...
```

## In The Wild

**`go list -m -json all` for full dependency auditing.** `govulncheck` uses the Go vulnerability database, but for a broader view of your dependency tree — licenses, provenance, deprecated packages — pipe `go list -m -json all` into your security tooling or use `nancy` from Sonatype, which checks the OSS Index:

```bash
go list -json -m all | nancy sleuth
```

**`go mod verify`.** Before running in production, verify that your module cache has not been tampered with:

```bash
go mod verify
# all modules verified — confirms checksums match go.sum
```

If you use vendoring, `go mod vendor` pins exact checksums. Combine with `go mod verify` in CI.

**Dependabot and Renovate.** GitHub's Dependabot and Renovate Bot can automatically open pull requests when a dependency has a known vulnerability or when a newer version is available. For Go modules, Dependabot opened several PRs for me last year on services I had not touched in months. Every one of them was a legitimate security fix. The cost of reviewing a PR is much lower than the cost of a breach.

**Snyk and similar commercial scanners.** `govulncheck` uses the Go-specific vulnerability database. Commercial scanners like Snyk, Trivy, or Grype scan the same data plus additional databases, container image layers, and IaC configurations. If your organization already has one of these, integrate it alongside — not instead of — `govulncheck`.

## The Gotchas

**`govulncheck` vs `go list -m -u`.** `go list -m -u` tells you which modules have newer versions available, but it does not tell you whether those versions fix vulnerabilities. Newer is not always safer — a major version bump might introduce breaking changes or new vulnerabilities. Let `govulncheck` drive your security updates and use `go list -m -u` for feature updates on your own schedule.

**Vendoring and vulnerability scanning.** If you use `go mod vendor`, `govulncheck` still works correctly — it analyzes the code, not the registry. But your vendored dependencies will not automatically receive security fixes when you run `go get`. You must run `go get`, `go mod vendor`, and commit the updated vendor directory.

**Private modules.** `govulncheck` uses `https://vuln.go.dev` to fetch vulnerability data. If your build environment has no internet access, the check will fail. Pre-download the vulnerability database and configure `GONOSUMDB` and `GOFLAGS=-mod=vendor` appropriately, or mirror the vulnerability database on your internal infrastructure.

**False sense of security.** `govulncheck` only knows about vulnerabilities that have been reported and added to the Go vulnerability database. A zero-day in a dependency will not appear in any scan. Dependency scanning reduces your risk; it does not eliminate it. Keep your dependencies as up-to-date as practical to minimize exposure to any window between disclosure and scanning.

## Key Takeaway

Run `govulncheck ./...` in every CI pipeline, add a weekly scheduled scan, and update vulnerable dependencies as soon as findings are discovered. The Go vulnerability database is curated and low-noise — it only reports vulnerabilities reachable from your code, not just present in your imports. The thirty seconds it takes to add this step to your CI pipeline is one of the highest-ROI security investments you can make.

---

**Go Security in Production**

Previous: [Lesson 8: Secure HTTP Defaults — Your production server needs these headers](/post/go/go-sec-http-defaults/)

---

🎓 **Course Complete!** You have finished Go Security in Production. These nine lessons cover the foundational security practices every Go production service needs — from input validation at the edge to dependency scanning in CI. Security is not a feature you add at the end; it is the discipline of building correctly from the start.
