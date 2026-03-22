---
title: 'Lesson 7: Containers from Scratch — Namespaces, Cgroups, What Docker Does'
author: Atharva Pandey
series: "Linux for Backend Engineers"
lesson: 7
keywords:
  - linux
  - containers
  - namespaces
  - cgroups
  - Docker
  - container internals
  - backend engineering
tags:
  - fundamentals
  - linux
  - operating systems
date: '2024-07-29T00:00:00.000Z'
---

I spent the first two years of my career using Docker without understanding what it was actually doing. I thought containers were a "lightweight VM" — some kind of virtualization. Then I read Liz Rice's "Containers from Scratch" talk and wrote a 100-line container runtime in Go. Containers are not virtual machines. They are just Linux processes with restricted views of the system, implemented using two kernel features: namespaces and cgroups. Once you see how simple the underlying mechanism is, you understand exactly what Docker adds and why containers behave the way they do.

## How It Actually Works

**Namespaces** give a process a restricted view of specific kernel resources. Each namespace type isolates one dimension:

| Namespace | Flag | Isolates |
|-----------|------|----------|
| `pid` | `CLONE_NEWPID` | Process IDs — PID 1 inside the container |
| `net` | `CLONE_NEWNET` | Network interfaces, routing tables, firewall rules |
| `mnt` | `CLONE_NEWNS` | Mount points and filesystem hierarchy |
| `uts` | `CLONE_NEWUTS` | Hostname and domain name |
| `ipc` | `CLONE_NEWIPC` | Shared memory, message queues |
| `user` | `CLONE_NEWUSER` | UID/GID mapping — root inside, non-root outside |
| `cgroup` | `CLONE_NEWCGROUP` | Cgroup root |

When you create a new namespace with `clone()` or `unshare()`, the process gets a fresh, empty view of that resource. Other processes with different namespace memberships see their own separate views.

**Cgroups (Control Groups)** limit the resources a process (and its children) can use:
- CPU time (percentage or absolute core-time)
- Memory (physical RAM, swap)
- Block I/O (disk read/write rate)
- Network I/O (with tc integration)
- Number of processes/threads

A container is nothing more than a process that was started with a new set of namespaces and placed in a cgroup.

Here is a minimal container runtime in Go that demonstrates the key syscalls:

```go
package main

import (
    "fmt"
    "os"
    "os/exec"
    "syscall"
)

func main() {
    switch os.Args[1] {
    case "run":
        run()
    case "child":
        child()
    }
}

func run() {
    // Re-exec ourselves as "child" inside new namespaces
    cmd := exec.Command("/proc/self/exe", append([]string{"child"}, os.Args[2:]...)...)
    cmd.Stdin = os.Stdin
    cmd.Stdout = os.Stdout
    cmd.Stderr = os.Stderr

    cmd.SysProcAttr = &syscall.SysProcAttr{
        // Create new namespaces for this process
        Cloneflags: syscall.CLONE_NEWUTS |  // new hostname
                    syscall.CLONE_NEWPID |  // new PID space (this process = PID 1)
                    syscall.CLONE_NEWNS  |  // new mount namespace
                    syscall.CLONE_NEWNET,   // new network namespace
    }

    if err := cmd.Run(); err != nil {
        fmt.Printf("run error: %v\n", err)
        os.Exit(1)
    }
}

func child() {
    // We are now inside new namespaces.
    // Set hostname visible only inside this container.
    syscall.Sethostname([]byte("container"))

    // Mount a new root filesystem (e.g., an Alpine Linux rootfs)
    // syscall.Chroot("/var/container-rootfs/alpine")
    // os.Chdir("/")

    // Mount /proc — needed for ps, top, etc.
    syscall.Mount("proc", "/proc", "proc", 0, "")
    defer syscall.Unmount("/proc", 0)

    // Run the requested command
    cmd := exec.Command(os.Args[2], os.Args[3:]...)
    cmd.Stdin = os.Stdin
    cmd.Stdout = os.Stdout
    cmd.Stderr = os.Stderr

    if err := cmd.Run(); err != nil {
        fmt.Printf("child error: %v\n", err)
    }
}
```

Running `./container run /bin/sh` gives you a shell that:
- Has its own hostname
- Has its own PID namespace (the shell is PID 1)
- Has its own mount namespace
- Has its own network namespace (no network by default — clean slate)

That's a container. Everything Docker, Podman, and containerd add is tooling: image layers, networking setup (`veth` pairs + bridge), cgroup management, a registry, and a daemon to orchestrate it all.

## Why It Matters

Understanding the underlying mechanism explains container behavior that otherwise seems mysterious:

**Why `kill 1` inside a container doesn't kill the host process**: PID 1 inside the container maps to a different PID outside. They share the same kernel but have different PID namespace views.

**Why containers can't see each other's processes by default**: separate PID namespaces.

**Why container networking requires a veth pair**: the container has a new network namespace with no interfaces. Docker creates a `veth` pair — one end inside the container namespace (`eth0`), one end on a bridge network on the host. Packets leave the container via `eth0`, cross the veth pair, enter the bridge, and route to the outside world.

**Why `--memory=512m` in Docker works**: Docker creates a cgroup for the container and sets the memory limit. The kernel enforces it; if the container exceeds the limit, the OOM killer kills a process inside the container.

## Production Example

Understanding cgroups is essential when running Go services in Kubernetes. The container's resource limits are cgroup limits on the underlying process:

```go
// In a containerized environment, GOMAXPROCS defaults to the number of host CPUs
// This is wrong if your container has a CPU limit of 1.0
// A service with GOMAXPROCS=32 but 1 CPU core will thrash the scheduler

// Fix: use automaxprocs library which reads cgroup CPU quota
import _ "go.uber.org/automaxprocs"

// Or set manually at startup:
func init() {
    cpuQuota := readCgroupCPUQuota()
    if cpuQuota > 0 {
        runtime.GOMAXPROCS(int(math.Ceil(cpuQuota)))
    }
}

func readCgroupCPUQuota() float64 {
    // cgroups v2: /sys/fs/cgroup/cpu.max
    data, err := os.ReadFile("/sys/fs/cgroup/cpu.max")
    if err != nil {
        return 0
    }
    // Format: "quota period" or "max period"
    parts := strings.Fields(strings.TrimSpace(string(data)))
    if len(parts) != 2 || parts[0] == "max" {
        return 0
    }
    quota, _ := strconv.ParseFloat(parts[0], 64)
    period, _ := strconv.ParseFloat(parts[1], 64)
    return quota / period
}
```

Also critical for memory: `GOMEMLIMIT` should be set close to (but below) the container's memory limit to avoid OOM kills:

```go
// Set 90% of container memory limit as GOMEMLIMIT
func setGoMemLimit() {
    limit := readCgroupMemoryLimit()
    if limit > 0 {
        soft := int64(float64(limit) * 0.9)
        debug.SetMemoryLimit(soft)
    }
}
```

## The Tradeoffs

**Containers are not VMs**: they share the host kernel. A kernel vulnerability can affect all containers. A container running as root with `--privileged` has extensive host access. Defense in depth requires: minimal container capabilities (`--cap-drop=ALL`), non-root users inside containers, read-only root filesystems where possible, and network policies.

**PID 1 in containers**: Linux's PID 1 has special responsibilities — it reaps zombie processes (adopted orphans) and handles signal forwarding. Most processes are not designed to be PID 1. This is why Docker's `CMD` uses `exec` form (`["./myapp"]` not `"./myapp"`) to make your process PID 1 directly, and why tools like `tini` are used as init processes.

**cgroup v1 vs v2**: cgroup v2 (unified hierarchy) is now the default on modern Linux. The paths change: `/sys/fs/cgroup/<subsystem>/` (v1) vs `/sys/fs/cgroup/` (v2). Libraries like `automaxprocs` handle both, but if you're reading cgroup limits manually, check which version your host kernel uses.

## Key Takeaway

Containers are Linux processes running with isolated namespaces and constrained by cgroups. Namespaces provide the illusion of isolation (private PID space, network, filesystem). Cgroups enforce resource limits (CPU, memory, I/O). Docker and Kubernetes are orchestration layers on top of these two kernel primitives. For Go services in containers, use `automaxprocs` to set `GOMAXPROCS` correctly, set `GOMEMLIMIT` to prevent OOM kills, and run as non-root where possible.

---

**Previous:** [Lesson 6: Signals](/post/fundamentals/linux-signals/) | **Next:** [Lesson 8: Memory-Mapped IO — How Databases Read Files](/post/fundamentals/linux-mmap/)
