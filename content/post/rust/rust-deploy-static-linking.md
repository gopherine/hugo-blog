---
title: "Lesson 2: Static Linking with musl — Single binary deploys"
author: Atharva Pandey
series: "Rust Deployment & Operations"
lesson: 2
keywords: [Rust, rust, deployment, musl, static linking, cross-compilation, alpine, scratch]
tags: [Rust tutorial, rust, deployment, devops]
date: '2025-04-22T16:40:00.000Z'
---

Last year I had to deploy a Rust service to a hardened environment — no package manager, no shared libraries, no internet access. Just a bare Linux kernel and my binary. If my binary depended on libc, libssl, or anything else in `/usr/lib`, it simply wouldn't start. That constraint forced me to learn static linking properly, and it turned out to be one of the best deployment patterns I've ever used.

A statically linked Rust binary carries everything it needs inside itself. No `.so` files, no version mismatches, no "works on my machine." You `scp` a single file to a server and it runs. That's it.

## Dynamic vs Static Linking — What's Actually Happening

By default, `cargo build --release` on Linux produces a dynamically linked binary. You can verify this:

```bash
$ cargo build --release
$ file target/release/myservice
target/release/myservice: ELF 64-bit LSB pie executable, x86-64, dynamically linked, interpreter /lib64/ld-linux-x86-64.so.2

$ ldd target/release/myservice
    linux-vdso.so.1
    libgcc_s.so.1 => /lib/x86_64-linux-gnu/libgcc_s.so.1
    libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6
    /lib64/ld-linux-x86-64.so.2
```

See those dependencies? `libc.so.6` is the big one. Your binary expects the system to provide a compatible C library at runtime. If it doesn't — or provides a different version — you get cryptic errors about missing symbols or segfaults.

Static linking bakes all of these into the binary itself. The binary is larger (typically 10-20 MB instead of 5-10 MB), but it has zero runtime dependencies.

## Enter musl

Here's the thing: you can't easily statically link against glibc. It wasn't designed for it, and it pulls in runtime dependencies (NSS for DNS, for example) that break static linking assumptions. The Rust community's answer is musl — an alternative C library that's designed from the ground up for static linking.

### Setting Up the musl Target

First, add the musl target:

```bash
rustup target add x86_64-unknown-linux-musl
```

Now build against it:

```bash
cargo build --release --target x86_64-unknown-linux-musl
```

Check the result:

```bash
$ file target/x86_64-unknown-linux-musl/release/myservice
target/x86_64-unknown-linux-musl/release/myservice: ELF 64-bit LSB executable, x86-64, statically linked

$ ldd target/x86_64-unknown-linux-musl/release/myservice
    not a dynamic executable
```

That's what we want. "Not a dynamic executable" — it needs nothing from the host system.

## The OpenSSL Problem

If your project depends on anything that wraps a C library, musl builds get complicated fast. The biggest offender is OpenSSL. If you use `reqwest`, `hyper` with TLS, or any database driver that speaks TLS, you probably depend on `openssl-sys`, which wants to link against the system's OpenSSL.

You have two options:

### Option 1: Use rustls Instead of OpenSSL

This is my preferred approach. `rustls` is a pure-Rust TLS implementation — no C dependencies, no linking headaches:

```toml
# Cargo.toml
[dependencies]
reqwest = { version = "0.12", default-features = false, features = ["rustls-tls", "json"] }
tokio = { version = "1", features = ["full"] }

# If you use sqlx
sqlx = { version = "0.8", features = ["runtime-tokio", "tls-rustls", "postgres"] }
```

Most Rust crates that need TLS offer both `native-tls` (wraps OpenSSL) and `rustls` backends. Switch to `rustls` and your musl builds just work.

Is `rustls` as fast as OpenSSL? For 99% of workloads, you won't notice a difference. OpenSSL has hardware-accelerated crypto that can matter at extreme scale, but `rustls` has been narrowing that gap steadily. I've shipped `rustls` in production handling thousands of TLS connections per second without issues.

### Option 2: Cross-Compile with musl and OpenSSL

Sometimes you're stuck with OpenSSL — maybe a dependency doesn't support `rustls`, or corporate policy mandates OpenSSL. In that case, you need to cross-compile OpenSSL for musl:

```dockerfile
FROM rust:1.82-bookworm AS builder

# Install musl tools
RUN apt-get update && apt-get install -y \
    musl-tools \
    musl-dev \
    && rm -rf /var/lib/apt/lists/*

RUN rustup target add x86_64-unknown-linux-musl

# Build OpenSSL statically for musl
ENV OPENSSL_VERSION=3.2.1
RUN curl -LO "https://www.openssl.org/source/openssl-${OPENSSL_VERSION}.tar.gz" && \
    tar xzf "openssl-${OPENSSL_VERSION}.tar.gz" && \
    cd "openssl-${OPENSSL_VERSION}" && \
    CC="musl-gcc -fPIE -pie" ./Configure no-shared no-async \
        --prefix=/usr/local/musl --openssldir=/usr/local/musl/ssl \
        linux-x86_64 && \
    make -j$(nproc) && \
    make install_sw

ENV OPENSSL_DIR=/usr/local/musl
ENV OPENSSL_STATIC=1
ENV PKG_CONFIG_ALLOW_CROSS=1

WORKDIR /app
COPY . .
RUN cargo build --release --target x86_64-unknown-linux-musl

FROM scratch
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/
COPY --from=builder /app/target/x86_64-unknown-linux-musl/release/myservice /
CMD ["/myservice"]
```

This is painful. But once you have the Dockerfile working, it's a one-time cost.

## The cargo-zigbuild Alternative

There's a newer approach that's gained traction: using Zig's C compiler as a cross-compilation backend. It ships with musl and handles cross-compilation better than the traditional toolchain:

```bash
cargo install cargo-zigbuild
cargo zigbuild --release --target x86_64-unknown-linux-musl
```

`cargo-zigbuild` replaces the linker with Zig's, which includes musl and can cross-compile to many targets from any host. It handles the OpenSSL mess better and produces clean static binaries. I've been using this more and more for projects that can't fully eliminate C dependencies.

## Docker + musl = Tiny Images

Combining musl static linking with Docker's `scratch` image gives you the smallest possible deployment:

```dockerfile
FROM rust:1.82-bookworm AS builder

RUN apt-get update && apt-get install -y musl-tools && rm -rf /var/lib/apt/lists/*
RUN rustup target add x86_64-unknown-linux-musl

WORKDIR /app

# Dependency caching
COPY Cargo.toml Cargo.lock ./
RUN mkdir src && echo "fn main() {}" > src/main.rs
RUN cargo build --release --target x86_64-unknown-linux-musl
RUN rm -rf src

COPY src ./src
RUN touch src/main.rs && cargo build --release --target x86_64-unknown-linux-musl

# Final image — nothing but the binary
FROM scratch

# TLS certificates for HTTPS connections
COPY --from=builder /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/

COPY --from=builder /app/target/x86_64-unknown-linux-musl/release/myservice /myservice
USER 1000
CMD ["/myservice"]
```

Final image size? Your binary plus ~230 KB of CA certificates. For a typical web service, that's 12-18 MB total. Compare that to a Python Flask container at 900 MB or a Java Spring Boot container at 400 MB. Rust wins this game handily.

## Gotchas and Troubleshooting

### DNS Resolution

musl's DNS resolver is simpler than glibc's. It doesn't support the full range of `/etc/nsswitch.conf` configurations. In Docker with default networking, this rarely matters. But if you're doing complex DNS (mDNS, LDAP-based resolution), you might hit edge cases.

For most services, Rust's `tokio::net` or the `trust-dns` resolver handle DNS independently of libc, sidestepping this entirely.

### proc-macro Crates and Build Scripts

Some crates run build scripts that compile C code. These build scripts run on your *host* architecture, not the target. If a build script tries to compile C code for the target, it needs the musl cross-compiler:

```bash
# Install musl cross-compilation tools
apt-get install -y musl-tools

# Tell CC to use musl-gcc for the target
export CC_x86_64_unknown_linux_musl=musl-gcc
```

### Performance Differences

musl's memory allocator is slower than glibc's for some workloads — particularly those with heavy allocation churn. If you notice performance degradation with musl, swap in `jemalloc` or `mimalloc`:

```toml
[dependencies]
tikv-jemallocator = "0.6"
```

```rust
#[global_allocator]
static GLOBAL: tikv_jemallocator::Jemalloc = tikv_jemallocator::Jemalloc;

fn main() {
    // your code
}
```

This is linked statically too, so it doesn't break your static binary setup.

### Binary Size

Static binaries are larger because they include libc. You can reduce them:

```toml
# Cargo.toml
[profile.release]
strip = true       # Remove debug symbols
lto = true          # Link-time optimization
codegen-units = 1   # Better optimization, slower compile
```

With these settings, a typical web service binary drops from ~20 MB to ~8-12 MB. We'll dive deeper into release profile tuning in Lesson 8.

## When to Use Static Linking

**Use it when:**
- Deploying to minimal environments (scratch containers, embedded systems, Lambda)
- You want truly reproducible builds — the binary from CI is byte-for-byte what runs in production
- You're distributing CLI tools that users download and run directly
- Security scanning requires you to eliminate shared library dependencies

**Skip it when:**
- You're deploying on a full Linux distro and shared libraries are fine
- You have C dependencies that are painful to cross-compile
- Binary size is a hard constraint (static binaries are 2-3x larger)

For most web services, I default to musl static linking with rustls. The simplicity of "one file, runs anywhere on Linux" is worth the slightly larger binary. There's something deeply satisfying about a deployment that's just copying a file.

## What's Next

We've got our binary built — efficiently with Docker, optionally statically linked. But building locally doesn't scale. In the next lesson, we'll set up CI/CD with GitHub Actions: caching Cargo dependencies across runs, running tests with `cargo-nextest`, and automating the Docker build pipeline we've built here.
