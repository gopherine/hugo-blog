---
title: "Lesson 8: Distribution — Static binaries, cargo-dist, Homebrew"
author: Atharva Pandey
series: "Rust CLI & Tooling"
lesson: 8
keywords: [Rust, rust, CLI, distribution, cargo-dist, homebrew, npm, static binary, release, packaging]
tags: [Rust tutorial, rust, cli]
date: '2024-09-16T19:40:00.000Z'
---

I built a CLI tool that three teams at work used daily. It lived in a shared directory on an NFS mount. Every time I pushed an update, I'd post in Slack: "new version in /shared/tools, please copy it to your PATH." Half the team was running a version from three months ago because they forgot. The other half had four copies scattered across their home directories. Distribution matters. If installing your tool is harder than `brew install myapp`, most people won't bother.

## The Distribution Channels

There are several ways users can install your Rust CLI:

1. **`cargo install`** — For Rust developers. Compiles from source.
2. **GitHub Releases** — Download pre-built binaries. Universal.
3. **Homebrew** — macOS and Linux. The gold standard for developer tools.
4. **npm** — Surprisingly good for CLI distribution. Works everywhere Node works.
5. **cargo-binstall** — Binary-first alternative to `cargo install`.
6. **System package managers** — apt, dnf, pacman. High effort, high reach.

Let me walk through each one.

## cargo install

This is the easiest — it's free if your crate is published to crates.io:

```bash
cargo install myapp
```

Users get the latest version compiled from source. The binary goes into `~/.cargo/bin/`. It works, but has drawbacks: requires Rust installed, takes minutes to compile for larger projects, and can fail if the user's Rust version is too old.

To publish:

```bash
# First time — set up crates.io authentication
cargo login

# Publish
cargo publish
```

Your `Cargo.toml` needs some fields filled out:

```toml
[package]
name = "myapp"
version = "0.1.0"
edition = "2021"
description = "A tool that does something useful"
license = "MIT"
repository = "https://github.com/yourname/myapp"
readme = "README.md"
keywords = ["cli", "tool"]
categories = ["command-line-utilities"]

# Only include what's needed
exclude = ["tests/fixtures/*", ".github/*", "docs/*"]
```

The `exclude` field keeps your crate size down. Nobody needs your test fixtures or CI configs when they `cargo install`.

## GitHub Releases: Pre-Built Binaries

Most users don't have Rust installed and don't want to install it just to try your tool. Pre-built binaries solve this.

The CI workflow from the previous lesson builds the binaries. Now you need to package and upload them. Here's a complete release script:

```bash
#!/bin/bash
set -euo pipefail

NAME="myapp"
VERSION=$(grep '^version' Cargo.toml | head -1 | cut -d'"' -f2)
DIST_DIR="dist"

mkdir -p "$DIST_DIR"

package() {
    local target=$1
    local suffix=$2
    local archive_name="${NAME}-v${VERSION}-${suffix}"

    echo "Building for ${target}..."

    if command -v cross &>/dev/null && [[ "$target" == *"linux"* ]]; then
        cross build --release --target "$target"
    else
        cargo build --release --target "$target"
    fi

    local bin_path="target/${target}/release/${NAME}"
    [[ "$target" == *"windows"* ]] && bin_path="${bin_path}.exe"

    if [[ "$target" == *"windows"* ]]; then
        (cd "$(dirname "$bin_path")" && zip "../../../${DIST_DIR}/${archive_name}.zip" "$(basename "$bin_path")")
    else
        tar -czf "${DIST_DIR}/${archive_name}.tar.gz" -C "$(dirname "$bin_path")" "$(basename "$bin_path")"
    fi

    # Generate checksum
    (cd "$DIST_DIR" && shasum -a 256 "${archive_name}"* >> checksums.txt)
}

# Build all targets
package x86_64-unknown-linux-musl "linux-amd64"
package aarch64-unknown-linux-musl "linux-arm64"
package x86_64-apple-darwin "darwin-amd64"
package aarch64-apple-darwin "darwin-arm64"
package x86_64-pc-windows-gnu "windows-amd64"

echo "Done. Archives in ${DIST_DIR}/"
ls -la "$DIST_DIR/"
```

Then create the release and upload:

```bash
gh release create "v${VERSION}" dist/* \
    --title "v${VERSION}" \
    --notes "Release notes here"
```

## cargo-dist: The Modern Answer

Writing all that CI and packaging code is tedious. `cargo-dist` automates the entire release pipeline — CI configuration, binary building, archive creation, and GitHub Release uploads:

```bash
cargo install cargo-dist

# Initialize cargo-dist in your project
cargo dist init
```

This adds configuration to your `Cargo.toml`:

```toml
# The profile that 'cargo dist' will build with
[profile.dist]
inherits = "release"
lto = "thin"

# Config for 'cargo dist'
[workspace.metadata.dist]
# The preferred cargo-dist version to use in CI
cargo-dist-version = "0.14.1"
# CI backends to support
ci = "github"
# The installers to generate for each app
installers = ["shell", "powershell", "homebrew"]
# Target platforms to build
targets = [
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "x86_64-unknown-linux-musl",
    "x86_64-pc-windows-msvc",
]
# Publish jobs to run in CI
publish-jobs = ["homebrew"]
# A GitHub repo to push Homebrew formulas to
tap = "yourname/homebrew-tap"
```

Then generate the CI workflow:

```bash
cargo dist generate
```

This creates `.github/workflows/release.yml` that handles everything. When you push a tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

cargo-dist automatically:
1. Builds binaries for all configured targets
2. Creates platform-appropriate archives (.tar.gz for Unix, .zip for Windows)
3. Generates an installer script (`curl -fsSL https://yourapp.dev/install.sh | sh`)
4. Creates a GitHub Release with all assets
5. Updates your Homebrew tap

Users install with:

```bash
# Shell installer (macOS/Linux)
curl --proto '=https' --tlsv1.2 -LsSf https://github.com/yourname/myapp/releases/latest/download/myapp-installer.sh | sh

# PowerShell installer (Windows)
powershell -ExecutionPolicy ByPass -c "irm https://github.com/yourname/myapp/releases/latest/download/myapp-installer.ps1 | iex"

# Homebrew
brew install yourname/tap/myapp

# cargo-binstall (no compilation)
cargo binstall myapp
```

Honestly, cargo-dist is what I recommend for most projects. It's opinionated but it covers 95% of use cases and saves you from maintaining CI configs.

## Homebrew Tap

If you're not using cargo-dist, you can set up a Homebrew tap manually. Create a repo called `homebrew-tap` on GitHub, then add a formula:

```ruby
# Formula/myapp.rb
class Myapp < Formula
  desc "A tool that does something useful"
  homepage "https://github.com/yourname/myapp"
  version "0.1.0"
  license "MIT"

  on_macos do
    on_arm do
      url "https://github.com/yourname/myapp/releases/download/v0.1.0/myapp-v0.1.0-darwin-arm64.tar.gz"
      sha256 "abc123..."
    end
    on_intel do
      url "https://github.com/yourname/myapp/releases/download/v0.1.0/myapp-v0.1.0-darwin-amd64.tar.gz"
      sha256 "def456..."
    end
  end

  on_linux do
    on_arm do
      url "https://github.com/yourname/myapp/releases/download/v0.1.0/myapp-v0.1.0-linux-arm64.tar.gz"
      sha256 "ghi789..."
    end
    on_intel do
      url "https://github.com/yourname/myapp/releases/download/v0.1.0/myapp-v0.1.0-linux-amd64.tar.gz"
      sha256 "jkl012..."
    end
  end

  def install
    bin.install "myapp"
  end

  test do
    system "#{bin}/myapp", "--version"
  end
end
```

Users install with `brew install yourname/tap/myapp`. Updating the formula for each release is the tedious part — which is exactly what cargo-dist automates.

## Shell Completion Scripts

A small thing that makes a big difference: generating shell completion scripts. clap can do this at build time:

```rust
// build.rs
use clap::CommandFactory;
use clap_complete::{generate_to, shells};
use std::env;
use std::io::Error;

include!("src/cli.rs");

fn main() -> Result<(), Error> {
    let outdir = match env::var_os("OUT_DIR") {
        Some(dir) => dir,
        None => return Ok(()),
    };

    let mut cmd = Cli::command();
    generate_to(shells::Bash, &mut cmd, "myapp", &outdir)?;
    generate_to(shells::Zsh, &mut cmd, "myapp", &outdir)?;
    generate_to(shells::Fish, &mut cmd, "myapp", &outdir)?;

    Ok(())
}
```

Or generate them at runtime with a `completions` subcommand:

```rust
use clap::{CommandFactory, Parser, Subcommand};
use clap_complete::{generate, Shell};

#[derive(Parser)]
#[command(name = "myapp")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Generate shell completion scripts
    Completions {
        /// Shell to generate completions for
        #[arg(value_enum)]
        shell: Shell,
    },

    /// Do something
    Run,
}

fn main() {
    let cli = Cli::parse();

    match cli.command {
        Commands::Completions { shell } => {
            let mut cmd = Cli::command();
            generate(shell, &mut cmd, "myapp", &mut std::io::stdout());
        }
        Commands::Run => {
            println!("Running...");
        }
    }
}
```

Usage:
```bash
# Bash
myapp completions bash > /usr/local/etc/bash_completion.d/myapp

# Zsh
myapp completions zsh > "${fpath[1]}/_myapp"

# Fish
myapp completions fish > ~/.config/fish/completions/myapp.fish
```

Include these instructions in your README and in the Homebrew formula's post-install message.

## Man Pages

For Unix tools, man pages are expected. The `clap_mangen` crate generates them from your clap definitions:

```toml
[build-dependencies]
clap_mangen = "0.2"
```

```rust
// build.rs
use clap::CommandFactory;
use std::fs;

include!("src/cli.rs");

fn main() -> std::io::Result<()> {
    let outdir = std::env::var("OUT_DIR").unwrap();
    let cmd = Cli::command();

    let man = clap_mangen::Man::new(cmd);
    let mut buffer: Vec<u8> = Default::default();
    man.render(&mut buffer)?;

    let man_path = std::path::PathBuf::from(&outdir).join("myapp.1");
    fs::write(&man_path, buffer)?;

    println!("cargo:warning=man page generated at {}", man_path.display());
    Ok(())
}
```

Include the generated man page in your release archives and install it to the right location in your Homebrew formula:

```ruby
def install
  bin.install "myapp"
  man1.install "myapp.1"
end
```

## Self-Update

For tools distributed as standalone binaries, self-updating is a nice touch:

```rust
use std::io::Write;

fn self_update() -> Result<(), Box<dyn std::error::Error>> {
    let current_version = env!("CARGO_PKG_VERSION");

    // Check GitHub for latest release
    let client = reqwest::blocking::Client::new();
    let release: serde_json::Value = client
        .get("https://api.github.com/repos/yourname/myapp/releases/latest")
        .header("User-Agent", "myapp")
        .send()?
        .json()?;

    let latest = release["tag_name"]
        .as_str()
        .unwrap_or("")
        .trim_start_matches('v');

    if latest == current_version {
        println!("Already at latest version ({})", current_version);
        return Ok(());
    }

    println!("Update available: {} -> {}", current_version, latest);
    print!("Download and install? [y/N] ");
    std::io::stdout().flush()?;

    let mut input = String::new();
    std::io::stdin().read_line(&mut input)?;

    if !input.trim().eq_ignore_ascii_case("y") {
        println!("Cancelled.");
        return Ok(());
    }

    println!("Downloading v{}...", latest);
    // Download and replace binary...
    // (In practice, use the `self_update` crate for this)

    Ok(())
}
```

For production use, the `self_update` crate handles the details — downloading, verifying checksums, replacing the running binary atomically.

## What to Actually Do

Here's my recommended setup, ranked by effort-to-value:

1. **Publish to crates.io** — 5 minutes. Covers all Rust users.
2. **Set up cargo-dist** — 30 minutes. Gets you GitHub Releases, installer scripts, and Homebrew for free.
3. **Add shell completions** — 20 minutes. Makes power users happy.
4. **Man pages** — 15 minutes if you already have clap definitions.

Skip npm distribution unless your tool is primarily for web developers. Skip system package managers (apt, dnf) unless you have a large user base — the maintenance overhead isn't worth it for small tools.

Distribute your tool well and people actually use it. Lock it in a GitHub repo with build-from-source instructions and you'll have five users forever. Next lesson: building terminal UIs with ratatui — full-screen interactive applications in the terminal.
