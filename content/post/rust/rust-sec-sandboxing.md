---
title: "Lesson 7: Sandboxing and Privilege Dropping — Least privilege"
author: Atharva Pandey
series: "Rust Security in Production"
lesson: 7
keywords: [Rust, rust, security, sandboxing, privilege dropping, seccomp, namespaces, least privilege, capabilities]
tags: [Rust tutorial, rust, security]
date: '2025-05-15T07:41:00.000Z'
---

Here's a pattern I've seen too many times: a Rust web service runs as root in a Docker container because "it needs to bind port 443." The service handles user uploads, parses JSON, processes images, and talks to a database — all with root privileges. If any part of that pipeline has a vulnerability, the attacker gets root on the container. And if the container isn't properly isolated, they might get the host too.

The principle of least privilege isn't new, but it's remarkable how often it gets ignored. Your service doesn't need root. It doesn't need to call `execve`. It doesn't need to open raw sockets. So why does it have those capabilities?

Let me show you how to lock this down in Rust.

## Dropping Privileges at Startup

The classic pattern: start as root to bind privileged ports, then immediately drop to an unprivileged user.

```rust
use std::io;

#[cfg(unix)]
mod privilege {
    use std::io;

    /// Drop privileges to the specified user and group.
    /// Call this after binding privileged ports but before
    /// accepting any connections.
    pub fn drop_privileges(uid: u32, gid: u32) -> io::Result<()> {
        // Set supplementary groups to empty
        let result = unsafe { libc::setgroups(0, std::ptr::null()) };
        if result != 0 {
            return Err(io::Error::last_os_error());
        }

        // Set group ID first (you can't change it after dropping root)
        let result = unsafe { libc::setgid(gid) };
        if result != 0 {
            return Err(io::Error::last_os_error());
        }

        // Set user ID
        let result = unsafe { libc::setuid(uid) };
        if result != 0 {
            return Err(io::Error::last_os_error());
        }

        // Verify we actually dropped privileges
        if unsafe { libc::getuid() } != uid {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "failed to drop UID",
            ));
        }
        if unsafe { libc::getgid() } != gid {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "failed to drop GID",
            ));
        }

        // Try to re-escalate — this should fail
        let escalation_result = unsafe { libc::setuid(0) };
        if escalation_result == 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "was able to re-escalate to root — something is wrong",
            ));
        }

        Ok(())
    }
}

#[cfg(unix)]
fn main() -> io::Result<()> {
    use std::net::TcpListener;

    // Step 1: Bind the privileged port while we're still root
    let listener = TcpListener::bind("0.0.0.0:443")?;
    println!("Bound to port 443");

    // Step 2: Drop privileges — use UID/GID of 'nobody' or a dedicated user
    // On most Linux systems: nobody = 65534
    privilege::drop_privileges(65534, 65534)?;
    println!("Dropped privileges to nobody:nobody");

    // Step 3: Now handle connections as unprivileged user
    for stream in listener.incoming() {
        match stream {
            Ok(_stream) => {
                println!("Connection received (running as uid {})",
                    unsafe { libc::getuid() });
            }
            Err(e) => eprintln!("Connection failed: {}", e),
        }
    }

    Ok(())
}

#[cfg(not(unix))]
fn main() {
    println!("Privilege dropping is Unix-only");
}
```

The verification step is crucial. After dropping privileges, *verify* that you actually dropped them. And then try to re-escalate — if you can, something is misconfigured (probably a `setuid` binary or a capability leak).

## Linux Capabilities — Fine-Grained Privilege Control

Instead of running as root and dropping privileges, a better approach is to never run as root at all. Use Linux capabilities to grant only the specific privileges you need.

```rust
#[cfg(target_os = "linux")]
mod caps {
    use std::io;
    use std::process::Command;

    /// Check if the current process has a specific capability.
    /// Uses the `capsh` command — in production, use the `caps` crate.
    pub fn has_capability(cap: &str) -> bool {
        Command::new("capsh")
            .arg("--has-p")
            .arg(cap)
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }
}

/// Using the `caps` crate for proper capability management
/// caps = "0.5"
#[cfg(target_os = "linux")]
fn drop_all_but_net_bind() -> Result<(), Box<dyn std::error::Error>> {
    use caps::{CapSet, Capability, CapsHashSet};

    // Only keep CAP_NET_BIND_SERVICE — allows binding ports < 1024
    let mut allowed = CapsHashSet::new();
    allowed.insert(Capability::CAP_NET_BIND_SERVICE);

    // Drop everything else from all capability sets
    caps::set(None, CapSet::Effective, &allowed)?;
    caps::set(None, CapSet::Permitted, &allowed)?;
    caps::set(None, CapSet::Inheritable, &CapsHashSet::new())?;

    // Now we can bind port 443 but can't do anything else
    // that requires elevated privileges

    println!("Dropped all capabilities except NET_BIND_SERVICE");
    Ok(())
}
```

In Docker, you set capabilities at the container level:

```dockerfile
# Dockerfile
FROM rust:1.78-slim AS builder
WORKDIR /build
COPY . .
RUN cargo build --release --locked

FROM debian:bookworm-slim
RUN useradd -r -s /usr/sbin/nologin appuser
COPY --from=builder /build/target/release/myapp /usr/local/bin/myapp
USER appuser
CMD ["myapp"]
```

```bash
# Run with only the capabilities you need
docker run \
  --cap-drop ALL \
  --cap-add NET_BIND_SERVICE \
  --read-only \
  --tmpfs /tmp:noexec,nosuid \
  --security-opt no-new-privileges \
  myapp
```

The flags here are important:

- `--cap-drop ALL` removes all Linux capabilities
- `--cap-add NET_BIND_SERVICE` adds back only port binding
- `--read-only` makes the filesystem read-only
- `--tmpfs /tmp:noexec,nosuid` gives a writable temp dir but blocks execution
- `--security-opt no-new-privileges` prevents privilege escalation via setuid binaries

## Seccomp — Restricting System Calls

Seccomp (Secure Computing Mode) lets you restrict which system calls a process can make. This is the nuclear option for sandboxing — if your process tries to call `execve`, `ptrace`, or `mount`, the kernel kills it.

```rust
#[cfg(target_os = "linux")]
mod seccomp_filter {
    use std::io;

    /// A simplified seccomp filter using the `seccompiler` crate.
    /// In production, you'd define this more carefully based on
    /// your application's actual syscall needs.
    pub fn apply_web_server_filter() -> Result<(), Box<dyn std::error::Error>> {
        use std::collections::BTreeMap;

        // Use seccompiler to build a BPF filter
        // seccompiler = "0.4"
        use seccompiler::{
            BpfProgram, SeccompAction, SeccompFilter, SeccompRule,
        };

        let mut rules: BTreeMap<i64, Vec<SeccompRule>> = BTreeMap::new();

        // Allow only the syscalls a typical web server needs
        let allowed_syscalls = [
            libc::SYS_read,
            libc::SYS_write,
            libc::SYS_close,
            libc::SYS_fstat,
            libc::SYS_mmap,
            libc::SYS_mprotect,
            libc::SYS_munmap,
            libc::SYS_brk,
            libc::SYS_rt_sigaction,
            libc::SYS_rt_sigprocmask,
            libc::SYS_ioctl,
            libc::SYS_accept4,
            libc::SYS_socket,
            libc::SYS_bind,
            libc::SYS_listen,
            libc::SYS_epoll_create1,
            libc::SYS_epoll_ctl,
            libc::SYS_epoll_wait,
            libc::SYS_futex,
            libc::SYS_clock_gettime,
            libc::SYS_getrandom,
            libc::SYS_exit,
            libc::SYS_exit_group,
            libc::SYS_setsockopt,
            libc::SYS_getsockopt,
            libc::SYS_recvfrom,
            libc::SYS_sendto,
            libc::SYS_openat,
            libc::SYS_sigaltstack,
            libc::SYS_sched_getaffinity,
        ];

        for &syscall in &allowed_syscalls {
            rules.insert(syscall, vec![SeccompRule::new(vec![]).unwrap()]);
        }

        // Default action: kill the process if it tries a disallowed syscall
        let filter = SeccompFilter::new(
            rules,
            SeccompAction::KillProcess,
            SeccompAction::Allow,
            std::env::consts::ARCH.try_into().unwrap(),
        )?;

        let bpf: BpfProgram = filter.try_into()?;
        seccompiler::apply_filter(&bpf)?;

        println!("Seccomp filter applied — only allowed syscalls will work");
        Ok(())
    }
}
```

Getting the syscall list right is tricky. Too restrictive and your app crashes. Too permissive and the sandbox doesn't help. My approach:

**1. Profile first.** Run your app under `strace` to see what syscalls it actually makes:

```bash
strace -c -f ./target/release/myapp
# This gives you a summary of all syscalls used

strace -f -o syscalls.log ./target/release/myapp
# This logs every syscall for detailed analysis
```

**2. Start permissive, then tighten.** Begin with a filter that logs violations instead of killing:

```rust
// Use SeccompAction::Log instead of KillProcess during development
let filter = SeccompFilter::new(
    rules,
    SeccompAction::Log,  // Log instead of kill
    SeccompAction::Allow,
    std::env::consts::ARCH.try_into().unwrap(),
)?;
```

**3. Test thoroughly.** Seccomp violations are hard to debug — the process just dies. Make sure your filter allows all the syscalls your normal operation needs.

## Filesystem Sandboxing with chroot

For services that process untrusted files (uploads, document conversion, image processing), restricting filesystem access is essential:

```rust
#[cfg(unix)]
fn sandbox_filesystem(sandbox_dir: &str) -> std::io::Result<()> {
    use std::ffi::CString;

    let dir = CString::new(sandbox_dir).expect("invalid path");

    // Change root directory — the process can no longer access
    // anything outside sandbox_dir
    let result = unsafe { libc::chroot(dir.as_ptr()) };
    if result != 0 {
        return Err(std::io::Error::last_os_error());
    }

    // Change to the new root
    std::env::set_current_dir("/")?;

    println!("Filesystem sandboxed to {}", sandbox_dir);
    Ok(())
}

/// A safer approach: use `unshare` to create a mount namespace
/// and then `pivot_root` instead of `chroot` (harder to escape)
#[cfg(target_os = "linux")]
fn sandbox_with_namespace(new_root: &str) -> std::io::Result<()> {
    use std::ffi::CString;

    // Create a new mount namespace
    let result = unsafe {
        libc::unshare(libc::CLONE_NEWNS)
    };
    if result != 0 {
        return Err(std::io::Error::last_os_error());
    }

    // Make the mount namespace private so changes don't propagate
    let none = CString::new("none").unwrap();
    let slash = CString::new("/").unwrap();
    let private = CString::new("private").unwrap();
    unsafe {
        libc::mount(
            none.as_ptr(),
            slash.as_ptr(),
            std::ptr::null(),
            libc::MS_REC | libc::MS_PRIVATE,
            std::ptr::null(),
        );
    }

    // Bind-mount the new root onto itself (required for pivot_root)
    let new_root_c = CString::new(new_root).unwrap();
    unsafe {
        libc::mount(
            new_root_c.as_ptr(),
            new_root_c.as_ptr(),
            std::ptr::null(),
            libc::MS_BIND | libc::MS_REC,
            std::ptr::null(),
        );
    }

    println!("Mount namespace created and pivot prepared");
    Ok(())
}
```

## A Production-Ready Sandboxing Pattern

Here's how I structure a service that processes untrusted input — combining all the techniques:

```rust
use std::io;
use std::net::TcpListener;

struct SandboxConfig {
    listen_addr: String,
    listen_port: u16,
    run_as_uid: u32,
    run_as_gid: u32,
    sandbox_dir: String,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let config = SandboxConfig {
        listen_addr: "0.0.0.0".to_string(),
        listen_port: 8443,
        run_as_uid: 65534,
        run_as_gid: 65534,
        sandbox_dir: "/var/lib/myapp/sandbox".to_string(),
    };

    // Phase 1: Privileged setup (runs as root)
    println!("Phase 1: Privileged setup");

    // Bind the listening socket while we have privileges
    let addr = format!("{}:{}", config.listen_addr, config.listen_port);
    let listener = TcpListener::bind(&addr)?;
    println!("  Bound to {}", addr);

    // Create sandbox directory
    std::fs::create_dir_all(&config.sandbox_dir)?;

    // Phase 2: Drop privileges
    println!("Phase 2: Dropping privileges");

    #[cfg(unix)]
    {
        // Drop supplementary groups
        unsafe { libc::setgroups(0, std::ptr::null()) };

        // Drop to unprivileged user
        unsafe { libc::setgid(config.run_as_gid) };
        unsafe { libc::setuid(config.run_as_uid) };

        let current_uid = unsafe { libc::getuid() };
        let current_gid = unsafe { libc::getgid() };
        println!("  Running as uid={}, gid={}", current_uid, current_gid);

        // Verify we can't re-escalate
        if unsafe { libc::setuid(0) } == 0 {
            panic!("SECURITY: was able to re-escalate to root!");
        }
    }

    // Phase 3: Apply seccomp (after all setup is complete)
    println!("Phase 3: Applying syscall restrictions");
    // apply_seccomp_filter()?;

    // Phase 4: Run the service
    println!("Phase 4: Service ready");

    for stream in listener.incoming() {
        match stream {
            Ok(stream) => {
                // Handle each connection in the sandbox
                std::thread::spawn(move || {
                    handle_connection(stream);
                });
            }
            Err(e) => eprintln!("Accept error: {}", e),
        }
    }

    Ok(())
}

fn handle_connection(mut stream: std::net::TcpStream) {
    use std::io::{Read, Write};

    let mut buf = [0u8; 4096];
    match stream.read(&mut buf) {
        Ok(n) => {
            println!("Received {} bytes", n);
            let _ = stream.write_all(b"HTTP/1.1 200 OK\r\n\r\nOK");
        }
        Err(e) => eprintln!("Read error: {}", e),
    }
}
```

The ordering matters: bind sockets (needs privilege) → drop privileges → apply seccomp → start serving. Once you apply seccomp, you can't do privileged operations anymore, so everything that needs elevation has to happen first.

## Docker Best Practices for Rust Services

Most production Rust services run in containers. Here's a hardened Dockerfile:

```dockerfile
# Build stage
FROM rust:1.78-slim AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock ./
COPY src/ src/
RUN cargo build --release --locked

# Runtime stage — use distroless for minimal attack surface
FROM gcr.io/distroless/cc-debian12:nonroot

# Copy only the binary — no shell, no package manager, nothing else
COPY --from=builder /build/target/release/myapp /myapp

# Run as nonroot user (UID 65532 in distroless)
USER nonroot:nonroot

ENTRYPOINT ["/myapp"]
```

And the `docker-compose.yml` or run command:

```yaml
# docker-compose.yml
services:
  myapp:
    build: .
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,size=64m
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    deploy:
      resources:
        limits:
          memory: 256m
          cpus: '1.0'
    ulimits:
      nproc: 128
      nofile:
        soft: 1024
        hard: 2048
```

The distroless base image is key — there's no shell, no package manager, no unnecessary utilities. If an attacker somehow gets code execution, they can't `apt-get install` anything or spawn a shell because there isn't one.

## The Layered Defense Model

No single technique is sufficient. Layer them:

1. **Rust's memory safety** — eliminates the most common exploit vector
2. **Input validation** (Lesson 2) — prevents logic-level attacks
3. **Privilege dropping** — limits damage if something goes wrong
4. **Capabilities** — fine-grained privilege control
5. **Seccomp** — restricts available syscalls
6. **Filesystem isolation** — limits data access
7. **Container hardening** — provides OS-level isolation
8. **Network policies** — restricts what the service can talk to

Each layer catches what the previous one misses. Memory safety prevents buffer overflows, but if someone finds a logic bug that lets them call `execve`, seccomp blocks it. If seccomp somehow fails, the container's namespace isolation contains the damage.

Defense in depth isn't paranoia. It's engineering.
