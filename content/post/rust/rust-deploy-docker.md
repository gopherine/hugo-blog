---
title: "Lesson 1: Docker for Rust — Multi-stage builds, minimal images"
author: Atharva Pandey
series: "Rust Deployment & Operations"
lesson: 1
keywords: [Rust, rust, deployment, Docker, multi-stage build, minimal images, scratch, distroless]
tags: [Rust tutorial, rust, deployment, devops]
date: '2025-04-20T09:15:00.000Z'
---

The first time I shipped a Rust service in Docker, my image was 2.1 GB. Two. Point. One. Gigabytes. For a binary that was 8 MB. I'd used `rust:latest` as my base, ran `cargo build --release` inside it, and called it a day. The image had GCC, LLVM, every system library known to humanity, and my tiny HTTP server somewhere in the corner.

That was the day I learned about multi-stage builds. And honestly, getting Docker right for Rust is one of those things that separates "I deployed it" from "I deployed it well."

## Why Docker for Rust at All?

I hear this question a lot. Rust compiles to a native binary — why not just `scp` the binary to a server? You absolutely can. But Docker gives you something a raw binary doesn't: a reproducible environment with defined dependencies, consistent networking, and orchestration support. Once you need more than one service, or you're deploying to Kubernetes, or you want zero-config rollbacks, Docker is the pragmatic choice.

The trick is making sure Docker doesn't negate Rust's advantages. A 2 GB image with a 30-second startup time defeats the purpose of writing fast, lean Rust code.

## The Naive Approach (Don't Do This)

Let's start with what not to do, because you'll find this pattern in too many tutorials:

```dockerfile
FROM rust:1.82
WORKDIR /app
COPY . .
RUN cargo build --release
CMD ["./target/release/myservice"]
```

This "works." Your binary runs. But the image is massive because it includes the entire Rust toolchain, all your source code, the `target` directory with hundreds of intermediate artifacts, and a full Debian installation. It's a security nightmare — you're shipping a compiler into production.

## Multi-Stage Builds: The Right Way

The idea is simple: use one stage to build, another to run. The build stage has the compiler. The run stage has only your binary.

```dockerfile
# Build stage
FROM rust:1.82-bookworm AS builder
WORKDIR /app

# Copy manifests first for dependency caching
COPY Cargo.toml Cargo.lock ./

# Create a dummy main.rs to build dependencies
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release
RUN rm -rf src

# Now copy actual source and rebuild
COPY src ./src
RUN touch src/main.rs && cargo build --release

# Runtime stage
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/myservice /usr/local/bin/myservice

# Don't run as root
RUN useradd -r -s /bin/false appuser
USER appuser

EXPOSE 3000
CMD ["myservice"]
```

This drops the image from 2+ GB to around 80-100 MB. Let's break down what's happening.

### The Dependency Caching Trick

The lines where I copy `Cargo.toml` and `Cargo.lock` first, then create a dummy `main.rs`, then build — that's the single most important Docker optimization for Rust. Here's why.

Docker caches layers. If a layer's input hasn't changed, Docker reuses the cached result. Dependencies change rarely. Your source code changes constantly. By building dependencies in a separate layer from your source code, you skip the 2-5 minute dependency compilation on most builds.

Without this trick, every source code change triggers a full rebuild of every dependency. With it, changing `src/main.rs` only rebuilds *your* code — dependencies come from cache. The difference is massive: 30 seconds vs 5 minutes on a typical project.

### The `touch` Trick

Notice `touch src/main.rs` before the real build. This updates the file's modification timestamp so Cargo knows it needs to recompile your code even though the `target` directory already has a build from the dummy source. Without this, Cargo might think nothing changed and skip recompilation.

## Going Even Smaller: Distroless and Scratch

Debian slim is fine for most cases, but we can go further.

### Google's Distroless Images

```dockerfile
FROM rust:1.82-bookworm AS builder
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release
RUN rm -rf src
COPY src ./src
RUN touch src/main.rs && cargo build --release

FROM gcr.io/distroless/cc-debian12
COPY --from=builder /app/target/release/myservice /
CMD ["/myservice"]
```

Distroless images contain only your binary and the minimal set of libraries needed to run it. No shell, no package manager, no `ls` or `cat`. This is great for security — an attacker who gets code execution in your container can't do much without basic utilities.

The `cc-debian12` variant includes the C runtime library, which most Rust binaries need (unless you statically link with musl — more on that in the next lesson).

Image size: roughly 30-40 MB.

### The Scratch Image

If your binary is fully statically linked, you can use `scratch` — literally an empty filesystem:

```dockerfile
FROM scratch
COPY --from=builder /app/target/release/myservice /
CMD ["/myservice"]
```

Image size: just your binary. 8-15 MB typically. But there are caveats — no TLS certificates, no timezone data, no DNS resolution libraries. You need to either statically link everything (including OpenSSL or use rustls) or copy the needed files from the builder stage:

```dockerfile
FROM scratch
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /usr/share/zoneinfo /usr/share/zoneinfo
COPY --from=builder /app/target/release/myservice /
CMD ["/myservice"]
```

I'll cover static linking in detail in the next lesson. For now, stick with `debian:bookworm-slim` or distroless unless you have a specific reason to go smaller.

## Handling Workspaces

Most real Rust projects use Cargo workspaces. The caching strategy needs adjustment:

```dockerfile
FROM rust:1.82-bookworm AS builder
WORKDIR /app

# Copy all Cargo.toml files to preserve workspace structure
COPY Cargo.toml Cargo.lock ./
COPY crates/api/Cargo.toml crates/api/Cargo.toml
COPY crates/core/Cargo.toml crates/core/Cargo.toml
COPY crates/db/Cargo.toml crates/db/Cargo.toml

# Create dummy source files for each crate
RUN mkdir -p crates/api/src && echo "fn main() {}" > crates/api/src/main.rs
RUN mkdir -p crates/core/src && echo "" > crates/core/src/lib.rs
RUN mkdir -p crates/db/src && echo "" > crates/db/src/lib.rs

# Build dependencies
RUN cargo build --release

# Remove dummy sources
RUN rm -rf crates/*/src

# Copy actual source
COPY crates crates

# Rebuild with real source
RUN touch crates/api/src/main.rs && cargo build --release -p api

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/api /usr/local/bin/api

RUN useradd -r -s /bin/false appuser
USER appuser

EXPOSE 3000
CMD ["api"]
```

The key is replicating the workspace's `Cargo.toml` structure exactly. Every crate needs its manifest in the right place, or Cargo will complain about the workspace definition not matching reality.

## cargo-chef: Automating the Caching Dance

Manually creating dummy source files gets tedious, especially with workspaces. `cargo-chef` automates this pattern:

```dockerfile
FROM rust:1.82-bookworm AS chef
RUN cargo install cargo-chef
WORKDIR /app

FROM chef AS planner
COPY . .
RUN cargo chef prepare --recipe-path recipe.json

FROM chef AS builder
COPY --from=planner /app/recipe.json recipe.json
RUN cargo chef cook --release --recipe-path recipe.json
COPY . .
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/target/release/myservice /usr/local/bin/myservice

RUN useradd -r -s /bin/false appuser
USER appuser

EXPOSE 3000
CMD ["myservice"]
```

`cargo chef prepare` analyzes your project and creates a "recipe" — a JSON file describing your dependency graph. `cargo chef cook` builds only the dependencies based on that recipe. Then you copy your actual source and build. The recipe only changes when dependencies change, so Docker caches the expensive `cook` step.

This is what I use for any project with more than a couple crates. It's cleaner than the manual approach and handles edge cases like build scripts and proc macros correctly.

## .dockerignore: Don't Copy Garbage

Every Rust Docker build needs a `.dockerignore` file:

```
target/
.git/
.github/
*.md
.env
.env.*
docker-compose*.yml
Dockerfile
.dockerignore
tests/
benches/
docs/
```

Without this, you're sending the entire `target` directory (which can be 5-20 GB) to the Docker daemon as build context. Even though it doesn't end up in the final image, it slows down every build because Docker has to copy it all before starting.

## BuildKit and Build Arguments

Enable BuildKit for better caching and parallel stage execution:

```bash
DOCKER_BUILDKIT=1 docker build -t myservice .
```

Or set it globally in `/etc/docker/daemon.json`:

```json
{
  "features": {
    "buildkit": true
  }
}
```

For configurable builds, use build arguments:

```dockerfile
FROM rust:1.82-bookworm AS builder
ARG FEATURES=""
WORKDIR /app
COPY . .
RUN cargo build --release --features "${FEATURES}"
```

```bash
docker build --build-arg FEATURES="metrics,tracing" -t myservice .
```

## Sane Defaults for Production

A few things I always include in production Dockerfiles:

**Non-root user.** Never run as root in a container. Create a dedicated user with no shell and no home directory.

**Health checks.** Docker can automatically restart unhealthy containers:

```dockerfile
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["/usr/local/bin/myservice", "--health-check"]
```

**Labels.** Add metadata so you can trace images back to commits:

```dockerfile
ARG GIT_SHA
LABEL org.opencontainers.image.revision="${GIT_SHA}"
LABEL org.opencontainers.image.source="https://github.com/you/myservice"
```

**Signal handling.** Use `CMD` with the exec form (JSON array syntax), not shell form. Shell form wraps your binary in `/bin/sh -c`, which swallows signals. Your binary needs to receive SIGTERM directly for graceful shutdown — we'll cover this in detail in Lesson 6.

## What We'll Cover Next

This lesson got your Rust binary into a Docker image efficiently. But we glossed over something important: dynamic vs static linking. In the next lesson, we'll tackle musl-based static linking — building a single, self-contained binary that runs on `scratch` images without any external dependencies. It's the cleanest deployment story in any compiled language, and Rust makes it surprisingly straightforward.
