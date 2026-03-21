---
title: 'Lesson 8: Packaging and Distributing — GoReleaser, Homebrew, and getting your tool to users'
author: Atharva Pandey
series: "Go CLI & Tooling"
lesson: 8
keywords:
  - Go
  - golang
  - CLI
  - GoReleaser
  - Homebrew
  - distribution
  - packaging
tags:
  - Go tutorial
  - golang
  - CLI
date: '2025-06-05T00:00:00.000Z'
---

Building a great CLI tool is half the job. The other half is getting it to users without making them compile it from source, navigate a GitHub releases page manually, or run a curl-pipe-to-bash script from an unverified URL. Distribution is where many Go projects stop short: the binary exists, the README says `go install`, and that is considered "distributed."

`go install` is fine for Go developers. It is not acceptable for operators, system administrators, and end users who reasonably expect `brew install` or `apt install`. GoReleaser closes this gap by automating the full release pipeline — cross-compiled binaries, checksums, GitHub releases, Homebrew formulas, Debian packages, Docker images — from a single configuration file and one `git push --tags`.

## The Problem

Manual distribution is tedious and error-prone:

```bash
# WRONG — what "manual release" looks like at 11 PM before a deadline
for GOOS in linux darwin windows; do
    for GOARCH in amd64 arm64; do
        GOOS=$GOOS GOARCH=$GOARCH go build \
            -ldflags="-X main.version=v1.2.0" \
            -o dist/myapp-$GOOS-$GOARCH ./cmd/myapp
    done
done
sha256sum dist/* > dist/checksums.txt
# Now manually create a GitHub release, upload 10 files, write release notes...
```

You forget the Windows `.exe` extension. The checksum file format is wrong. The ldflags version does not match the git tag. The Homebrew formula points to the old release URL. By the time the release is live, it has been forty minutes, three mistakes, and one "re-release because the checksums were wrong" commit.

## The Idiomatic Way

GoReleaser handles this from a single `.goreleaser.yaml` file checked into your repository:

```yaml
# .goreleaser.yaml
version: 2

before:
  hooks:
    - go mod tidy
    - go generate ./...

builds:
  - id: myapp
    main: ./cmd/myapp
    binary: myapp
    env:
      - CGO_ENABLED=0
    goos:
      - linux
      - darwin
      - windows
    goarch:
      - amd64
      - arm64
    ignore:
      - goos: windows
        goarch: arm64
    ldflags:
      - -s -w
      - -X github.com/yourorg/myapp/internal/version.Version={{.Version}}
      - -X github.com/yourorg/myapp/internal/version.Commit={{.Commit}}
      - -X github.com/yourorg/myapp/internal/version.BuildTime={{.Date}}
    flags:
      - -trimpath

archives:
  - id: default
    format: tar.gz
    format_overrides:
      - goos: windows
        format: zip
    name_template: "{{ .ProjectName }}_{{ .Os }}_{{ .Arch }}"

checksum:
  name_template: "checksums.txt"
  algorithm: sha256

release:
  github:
    owner: yourorg
    name: myapp
  draft: false
  prerelease: auto

changelog:
  sort: asc
  filters:
    exclude:
      - "^docs:"
      - "^test:"
      - "^ci:"
```

To release, you tag and push:

```bash
git tag -a v1.2.0 -m "Release v1.2.0"
git push origin v1.2.0
```

GoReleaser — run from CI on the tag push — builds all targets, creates the archives, generates checksums, and publishes the GitHub release with all artifacts attached. Total time: about ninety seconds.

For a Homebrew tap, add a section to `.goreleaser.yaml`:

```yaml
brews:
  - repository:
      owner: yourorg
      name: homebrew-tap
      token: "{{ .Env.HOMEBREW_TAP_TOKEN }}"
    directory: Formula
    homepage: https://github.com/yourorg/myapp
    description: "A tool for managing resources"
    license: "MIT"
    test: |
      system "#{bin}/myapp version"
    install: |
      bin.install "myapp"
```

GoReleaser opens a pull request against your `homebrew-tap` repository (or commits directly if you configure it to) with a formula that downloads the correct tarball, verifies the checksum, and installs the binary. After merging, users can run:

```bash
brew tap yourorg/tap
brew install myapp
```

And they are running your latest release.

## In The Wild

The GitHub Actions workflow to run GoReleaser on tag push is about fifteen lines:

```yaml
# .github/workflows/release.yaml
name: Release

on:
  push:
    tags:
      - "v*"

permissions:
  contents: write

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # GoReleaser needs full git history for changelog

      - uses: actions/setup-go@v5
        with:
          go-version-file: go.mod

      - uses: goreleaser/goreleaser-action@v6
        with:
          version: latest
          args: release --clean
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          HOMEBREW_TAP_TOKEN: ${{ secrets.HOMEBREW_TAP_TOKEN }}
```

The `GITHUB_TOKEN` is provided automatically by Actions with write permission to the releases. The `HOMEBREW_TAP_TOKEN` is a personal access token with write access to your tap repository.

For projects that do not want a full Homebrew tap, `go install` with a specific version works well for Go users:

```bash
go install github.com/yourorg/myapp/cmd/myapp@v1.2.0
```

This installs from source — so it still requires a Go toolchain — but it is version-pinned and uses the module proxy for verification. It belongs in your README as the "if you have Go" option alongside the Homebrew option.

For system packages (`.deb`, `.rpm`), GoReleaser integrates with `nfpm`:

```yaml
nfpms:
  - id: packages
    package_name: myapp
    vendor: YourOrg
    homepage: https://github.com/yourorg/myapp
    description: "A tool for managing resources"
    license: MIT
    formats:
      - deb
      - rpm
    bindir: /usr/bin
```

This produces `.deb` and `.rpm` packages that install cleanly on Debian/Ubuntu and RHEL/Fedora systems. Add them to your GitHub release alongside the tarballs.

## The Gotchas

**Test your release process before your first real release.** Run `goreleaser release --snapshot --clean` locally — this produces all the artifacts without publishing anything. Inspect the output in `dist/` to verify filenames, checksums, and that the version string is correct.

**`fetch-depth: 0` in Actions is not optional.** GoReleaser uses `git log` to generate changelogs and `git describe` to determine versions. A shallow clone (the default for `actions/checkout`) breaks both. Always set `fetch-depth: 0` in the checkout step.

**Homebrew tap updates can fail silently.** If the pull request to your tap repository fails to open because of a permission issue or a stale branch, GoReleaser will report success for the rest of the release but the Homebrew formula will not be updated. Check the tap repository after your release to verify the formula was updated.

## Key Takeaway

🎓 Course Complete! You have reached the end of "Go CLI & Tooling." The pattern across all eight lessons is the same: use the right tool for each layer of the problem. Cobra for command structure, Viper or struct-based loading for config, streaming I/O for file handling, `signal.NotifyContext` for graceful shutdown, `GOOS/GOARCH` for cross-compilation, `//go:embed` for self-contained binaries, `-ldflags` for version injection, and GoReleaser for distribution. Each piece is independent and composable. Together they produce CLI tools that are professional-grade from the first release: versioned, cross-platform, signal-aware, and easy to install. That is the standard your users have come to expect, and Go's tooling makes it achievable without heroics.

---

← [Lesson 7: Build Flags and ldflags](/post/go/go-cli-build-flags) | [Course Index](/series/go-cli-tooling)
