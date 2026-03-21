---
title: 'Lesson 2: Docker for Go — Multi-stage builds that produce tiny images'
author: Atharva Pandey
series: "Go Deployment & Operations"
lesson: 2
keywords:
  - Go
  - golang
  - Docker
  - multi-stage build
  - container
  - distroless
  - Dockerfile
  - deployment
tags:
  - Go tutorial
  - golang
  - deployment
  - devops
date: '2024-08-10T00:00:00.000Z'
---

The first Dockerfile I wrote for a Go service produced a 1.2 GB image. It was based on `golang:1.22`, which includes the full Go toolchain, build cache, test infrastructure, and a complete Debian system. The compiled binary was 18 MB. I was shipping 1.2 GB to run 18 MB.

The second version, after learning about multi-stage builds, produced a 22 MB image. Same binary. No compiler. No package manager. No shell. Just the binary and the absolute minimum needed to run it. The difference matters not just for storage: smaller images pull faster, have smaller attack surfaces, and fail more obviously when a required file is missing.

## The Problem

The single-stage naive Dockerfile is something most tutorials still show:

```dockerfile
# WRONG — ships the entire Go toolchain into production
FROM golang:1.22

WORKDIR /app
COPY . .
RUN go build -o myapp ./cmd/myapp

EXPOSE 8080
CMD ["./myapp"]
```

Problems: the resulting image is 800MB+. It contains the Go compiler, the entire Go module cache, source code, test files, and Debian's package manager. If an attacker gets a shell in the container, they have a full build environment to work with. And every deploy transfers this enormous image across the network.

The subtler version: using the correct base image for the build stage but forgetting to configure the build for the deployment environment:

```dockerfile
# WRONG — CGO enabled by default means the binary links against glibc
# but the runtime image might have a different version or none at all
FROM golang:1.22 AS builder
WORKDIR /app
COPY . .
RUN go build -o myapp ./cmd/myapp  # dynamic binary!

FROM debian:bookworm-slim
COPY --from=builder /app/myapp .
CMD ["./myapp"]
# Fails at runtime: error loading shared libraries: libc.so.6
```

## The Idiomatic Way

The correct pattern combines a multi-stage build with a static binary:

```dockerfile
# ── Stage 1: build ────────────────────────────────────────────────────────────
FROM golang:1.22-alpine AS builder

# Install CA certificates — needed if the binary makes HTTPS calls.
# We'll copy these into the final image.
RUN apk add --no-cache ca-certificates tzdata

WORKDIR /app

# Copy dependency files first to cache the module download layer separately.
# This layer is only invalidated when go.mod or go.sum changes.
COPY go.mod go.sum ./
RUN go mod download

# Copy source and build.
COPY . .

# Static binary: CGO disabled, symbols stripped, version embedded.
ARG VERSION=dev
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build \
    -ldflags="-s -w -X main.version=${VERSION}" \
    -o myapp \
    ./cmd/myapp

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM scratch

# Copy CA certs and timezone data from the builder.
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /usr/share/zoneinfo /usr/share/zoneinfo

# Run as a non-root user. Create the user in the build stage since
# scratch has no useradd command.
COPY --from=builder /etc/passwd /etc/passwd
USER nobody

# Copy only the compiled binary.
COPY --from=builder /app/myapp /myapp

EXPOSE 8080
ENTRYPOINT ["/myapp"]
```

The `FROM scratch` base image is literally empty — no OS, no shell, no package manager. The final image contains exactly three things: the CA certificate bundle, timezone data, and the binary. Nothing else.

This produces a 20-30 MB image for a typical Go service. The attack surface is essentially zero: there's no shell to execute, no package manager to install tools with, no other binaries to exploit.

For services that need debugging tools in production or need a minimal shell, `gcr.io/distroless/static-debian12` is the middle ground:

```dockerfile
# Distroless: no shell, but includes glibc for CGO binaries,
# and some standard certificates and timezone data built in.
FROM gcr.io/distroless/static-debian12 AS runtime

COPY --from=builder /app/myapp /myapp
USER nonroot:nonroot
ENTRYPOINT ["/myapp"]
```

The `distroless/static` variant is for CGO-disabled binaries. The `distroless/base` variant includes glibc for CGO-enabled binaries.

## In The Wild

The module cache layer optimization is worth calling out explicitly. The two-step COPY pattern:

```dockerfile
COPY go.mod go.sum ./
RUN go mod download
COPY . .
```

means that `go mod download` is a cached Docker layer as long as neither `go.mod` nor `go.sum` changes. In a monorepo or on a hot CI runner with layer caching, this eliminates the module download step for the vast majority of builds. Without this pattern, every build re-downloads all dependencies.

For multi-architecture images — increasingly important as ARM servers become common — Docker BuildKit handles the cross-compilation:

```dockerfile
# In a multi-arch Dockerfile, use the TARGETPLATFORM arg.
FROM --platform=$BUILDPLATFORM golang:1.22-alpine AS builder
ARG TARGETOS TARGETARCH

RUN CGO_ENABLED=0 GOOS=${TARGETOS} GOARCH=${TARGETARCH} go build \
    -o myapp ./cmd/myapp
```

Build and push for both architectures:

```bash
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --tag myrepo/myapp:latest \
    --push \
    .
```

The resulting image manifest is a multi-arch manifest: Docker pulls the correct variant for the host automatically.

A `.dockerignore` file is as important as the Dockerfile itself:

```
# .dockerignore — prevent local state from contaminating the build
.git
.github
*.md
dist/
*.test
coverage.out
.env
.env.*
```

Without `.dockerignore`, the COPY step includes your `.git` directory (which can be hundreds of megabytes in a mature repo), local build artifacts, and secrets in `.env` files. The `.dockerignore` prevents all of that.

## The Gotchas

**`scratch` has no `/tmp`.** Some libraries create temporary files in `/tmp`. With `FROM scratch`, that directory doesn't exist. Either use `TMPDIR` env var to point to a mounted volume, or switch to `distroless` which includes a real (but empty) `/tmp`.

**Debugging is harder with `scratch`.** When something goes wrong in a scratch container, you can't `kubectl exec` into it and run `ls` or `curl`. Keep a debug build available (e.g., a separate Docker tag built `FROM alpine`) that you can run alongside the production container for diagnosis. Alternatively, use ephemeral debug containers with `kubectl debug`.

**Health check probes need a binary.** Kubernetes liveness and readiness probes that use `exec` commands (like `/bin/sh -c`) don't work in scratch containers. Use HTTP-based probes instead — expose a `/healthz` endpoint and configure the probe to hit it.

**Layer ordering matters for cache.** Always copy the minimal set of files needed for each layer before the files that change frequently. Source code changes every commit; dependencies change rarely. Put dependency downloads before source COPY.

## Key Takeaway

Multi-stage builds are not an optimization — they're the correct way to build Go containers. The pattern is: build in a Go image with full toolchain, copy only the static binary to a scratch or distroless base. The result is a 20-30 MB image with a near-zero attack surface that deploys in seconds. Add `.dockerignore`, separate the module download layer, and embed version information at build time.

---

**Previous:** [Lesson 1: Static Binaries](/post/go/go-deploy-static-binaries/)
**Next:** [Lesson 3: Health Checks — Readiness vs liveness, and why both matter](/post/go/go-deploy-health-checks/)
