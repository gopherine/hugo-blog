---
title: "Lesson 4: Writing Linux Kernel Modules in Rust — Rust in the kernel"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 4
keywords: [Rust, rust, systems programming, Linux kernel, kernel module, driver, Rust for Linux]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-07-17T11:23:45.000Z'
---

In December 2022, Rust officially merged into the Linux kernel source tree. Not as an experiment. Not as a sidecar. As a first-class language for writing kernel code. Linus Torvalds signed off on it.

I remember reading the mailing list thread and thinking: "This is either going to be the most important thing to happen to systems programming in twenty years, or the most spectacular failure." Three years in, it's looking a lot like the former.

## Why Rust in the Kernel Matters

The Linux kernel has roughly 30 million lines of C code. It's been developed for over 30 years by thousands of contributors. And despite heroic efforts, memory safety bugs remain the single largest category of kernel vulnerabilities.

Microsoft reported that ~70% of their security bugs are memory safety issues. Google found the same in Android. The kernel is no different — use-after-free, buffer overflows, data races, null pointer dereferences. These aren't amateur mistakes. They're the fundamental consequence of writing millions of lines of C.

Rust doesn't magically fix everything. But it eliminates *entire classes* of bugs at compile time. In kernel code — where a bug means a system crash or a privilege escalation exploit — that matters enormously.

## The Kernel Rust Environment

Kernel Rust is *not* normal Rust. You're in `no_std` territory with extra constraints:

- No standard library (obviously)
- No `alloc` crate — the kernel has its own allocators
- No unwinding — panics must abort
- No floating point (kernel code generally can't use FPU)
- Specific allocator flags (GFP_KERNEL, GFP_ATOMIC, etc.)
- Must interoperate with existing C APIs

The kernel provides its own Rust abstractions in the `kernel` crate, which wraps C kernel APIs in safe Rust interfaces.

## Setting Up the Build Environment

You need a kernel source tree with Rust support. As of kernel 6.1+:

```bash
# Clone the kernel source
git clone --depth 1 https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git
cd linux

# Install Rust toolchain (specific version required — check Documentation/process/changes.rst)
rustup override set $(scripts/min-tool-version.sh rustc)
rustup component add rust-src

# Install bindgen (generates Rust bindings from C headers)
cargo install --locked bindgen-cli

# Configure kernel with Rust support
make LLVM=1 rustavailable  # Check if Rust toolchain is compatible
make LLVM=1 menuconfig
# Enable: General setup -> Rust support
```

The `LLVM=1` flag is important — Rust for Linux requires the LLVM/Clang toolchain, not GCC. This is because Rust and Clang share the LLVM backend, so they produce compatible object code.

## Your First Kernel Module

Let's write the kernel equivalent of "Hello World":

```rust
// SPDX-License-Identifier: GPL-2.0

//! Minimal Rust kernel module

use kernel::prelude::*;

module! {
    type: HelloModule,
    name: "hello_rust",
    author: "Atharva Pandey",
    description: "A minimal Rust kernel module",
    license: "GPL",
}

struct HelloModule;

impl kernel::Module for HelloModule {
    fn init(_module: &'static ThisModule) -> Result<Self> {
        pr_info!("Hello from Rust in the kernel!\n");
        pr_info!("Module loaded successfully\n");
        Ok(HelloModule)
    }
}

impl Drop for HelloModule {
    fn drop(&mut self) {
        pr_info!("Goodbye from Rust! Module unloading\n");
    }
}
```

The `module!` macro generates the boilerplate that the kernel's module loader expects — the init/exit functions, module metadata, license declaration. In C, you'd write `module_init()`, `module_exit()`, `MODULE_LICENSE()`, etc. The macro handles all of that.

The Kbuild file (`Makefile` for kernel modules):

```makefile
# samples/rust/Kbuild
obj-m += hello_rust.o
```

Build it:

```bash
make LLVM=1 M=samples/rust modules
```

Load and test:

```bash
sudo insmod samples/rust/hello_rust.ko
dmesg | tail -2
# [12345.678] hello_rust: Hello from Rust in the kernel!
# [12345.678] hello_rust: Module loaded successfully

sudo rmmod hello_rust
dmesg | tail -1
# [12346.789] hello_rust: Goodbye from Rust! Module unloading
```

## Kernel Memory Allocation

In the kernel, you can't just `Box::new()` — allocation can fail, and *how* you allocate matters:

```rust
use kernel::prelude::*;
use kernel::alloc::flags;

struct MyDriver {
    buffer: Box<[u8]>,
    name: CString,
}

impl kernel::Module for MyDriver {
    fn init(_module: &'static ThisModule) -> Result<Self> {
        // Kernel allocations are fallible — they return Result
        // GFP_KERNEL: can sleep, suitable for process context
        let buffer = Box::new_zeroed_slice(4096, flags::GFP_KERNEL)?
            .assume_init();

        // CString for kernel string handling
        let name = CString::try_from_fmt(fmt!("my_driver_instance"))?;

        pr_info!("Allocated {} bytes for {}\n", buffer.len(), &*name);

        Ok(Self { buffer, name })
    }
}

impl Drop for MyDriver {
    fn drop(&mut self) {
        pr_info!("Freeing resources for {}\n", &*self.name);
        // buffer is automatically freed via Drop — no manual kfree needed
    }
}
```

Key differences from userspace Rust:

- `Box::new()` can fail — returns `Result`, not a raw pointer
- Allocation flags specify context: `GFP_KERNEL` (can sleep), `GFP_ATOMIC` (can't sleep — interrupt context)
- The `?` operator propagates allocation failures cleanly
- `Drop` handles deallocation automatically — no more forgetting `kfree()`

## A Character Device Driver

Let's build something real — a character device that userspace programs can read from and write to:

```rust
// SPDX-License-Identifier: GPL-2.0

//! A simple character device that echoes data back

use kernel::prelude::*;
use kernel::sync::Mutex;
use kernel::file::{self, File, Operations};
use kernel::io_buffer::{IoBufferReader, IoBufferWriter};
use kernel::miscdev::Registration;
use kernel::alloc::flags;

module! {
    type: EchoDevice,
    name: "echo_rust",
    author: "Atharva Pandey",
    description: "Character device that stores and echoes data",
    license: "GPL",
}

const BUFFER_SIZE: usize = 4096;

struct DeviceState {
    data: [u8; BUFFER_SIZE],
    len: usize,
}

struct EchoDevice {
    _registration: Pin<Box<Registration<EchoDevice>>>,
    state: Pin<Box<Mutex<DeviceState>>>,
}

#[vtable]
impl Operations for EchoDevice {
    type Data = Pin<Box<Mutex<DeviceState>>>;
    type OpenData = Pin<Box<Mutex<DeviceState>>>;

    fn open(state: &Pin<Box<Mutex<DeviceState>>>, _file: &File) -> Result<Pin<Box<Mutex<DeviceState>>>> {
        pr_info!("Device opened\n");
        Ok(state.clone())
    }

    fn read(
        state: &Pin<Box<Mutex<DeviceState>>>,
        _file: &File,
        writer: &mut impl IoBufferWriter,
        offset: u64,
    ) -> Result<usize> {
        let guard = state.lock();
        let offset = offset as usize;

        if offset >= guard.len {
            return Ok(0); // EOF
        }

        let available = guard.len - offset;
        let to_read = core::cmp::min(available, writer.len());
        writer.write_slice(&guard.data[offset..offset + to_read])?;

        Ok(to_read)
    }

    fn write(
        state: &Pin<Box<Mutex<DeviceState>>>,
        _file: &File,
        reader: &mut impl IoBufferReader,
        _offset: u64,
    ) -> Result<usize> {
        let mut guard = state.lock();
        let to_write = core::cmp::min(reader.len(), BUFFER_SIZE);

        reader.read_slice(&mut guard.data[..to_write])?;
        guard.len = to_write;

        pr_info!("Wrote {} bytes to device\n", to_write);
        Ok(to_write)
    }
}

impl kernel::Module for EchoDevice {
    fn init(_module: &'static ThisModule) -> Result<Self> {
        pr_info!("Initializing echo device\n");

        let state = Pin::from(Box::new(
            Mutex::new(DeviceState {
                data: [0u8; BUFFER_SIZE],
                len: 0,
            }),
            flags::GFP_KERNEL,
        )?);

        let reg = Registration::new_pinned(
            fmt!("echo_rust"),
            state.clone(),
            flags::GFP_KERNEL,
        )?;

        Ok(Self {
            _registration: reg,
            state,
        })
    }
}

impl Drop for EchoDevice {
    fn drop(&mut self) {
        pr_info!("Echo device unloaded\n");
    }
}
```

Userspace interaction:

```bash
# Load the module
sudo insmod echo_rust.ko

# Check it created a device
ls -la /dev/echo_rust

# Write data
echo "Hello from userspace" > /dev/echo_rust

# Read it back
cat /dev/echo_rust
# Hello from userspace

# Unload
sudo rmmod echo_rust
```

The beauty here is how Rust's ownership model maps onto kernel resource management. The `Registration` type automatically unregisters the device when it's dropped. The `Mutex` provides safe concurrent access. And if any initialization step fails, everything allocated so far is automatically cleaned up via `Drop`.

In C, you'd have a chain of `goto cleanup_X` labels. In Rust, error handling is structural.

## Locking in the Kernel

The kernel has multiple lock types, and using the wrong one is a bug:

```rust
use kernel::sync::{Mutex, SpinLock};

// Mutex — can sleep, suitable for process context
struct ProcessContextData {
    data: Mutex<Vec<u8>>,
}

// SpinLock — cannot sleep, suitable for interrupt context
struct InterruptContextData {
    counter: SpinLock<u64>,
}

fn process_context_work(data: &ProcessContextData) {
    let mut guard = data.data.lock();
    // Can do things that might sleep here (allocate, do I/O)
    guard.push(42);
    // Lock automatically released when guard is dropped
}

fn interrupt_context_work(data: &InterruptContextData) {
    let mut guard = data.counter.lock();
    // Must NOT sleep here — we're holding a spinlock
    *guard += 1;
    // SpinLock guard released on drop
}
```

Rust's type system helps here: if you try to call a sleeping function while holding a `SpinLock`, the kernel's lock dependency tracker (lockdep) will catch it at runtime. Future work on the Rust kernel abstractions aims to catch some of these at compile time.

## Interfacing with C Code

Most kernel subsystems are written in C. Rust modules need to call into them:

```rust
use kernel::bindings; // Auto-generated Rust bindings to C kernel headers

fn get_system_info() {
    // Calling C functions is unsafe
    let jiffies = unsafe { bindings::jiffies };
    let hz = unsafe { bindings::HZ };

    pr_info!("System uptime: {} seconds\n", jiffies / hz as u64);
}

// The kernel crate provides safe wrappers for common operations
use kernel::delay::coarse_sleep;
use core::time::Duration;

fn wait_a_bit() {
    coarse_sleep(Duration::from_millis(100));
}
```

The `bindings` module is generated automatically by `bindgen` during the kernel build. It creates Rust FFI declarations for every C function, struct, and constant in the kernel headers. The `kernel` crate then builds safe abstractions on top of these raw bindings.

## Error Handling the Kernel Way

Kernel functions return error codes as negative integers in C. In Rust, they become proper `Result` types:

```rust
use kernel::error::code;

fn do_something_risky() -> Result {
    // The ? operator propagates kernel errors
    let resource = acquire_resource()?;

    if some_condition_fails() {
        // Return a kernel error code
        return Err(code::EINVAL); // Invalid argument
    }

    if out_of_memory() {
        return Err(code::ENOMEM); // Out of memory
    }

    // Success
    Ok(())
}
```

Compare this to the C equivalent:

```c
int do_something_risky(void) {
    struct resource *res;
    int ret;

    res = acquire_resource();
    if (IS_ERR(res))
        return PTR_ERR(res);

    if (some_condition_fails()) {
        ret = -EINVAL;
        goto cleanup;
    }

    if (out_of_memory()) {
        ret = -ENOMEM;
        goto cleanup;
    }

    return 0;

cleanup:
    release_resource(res);
    return ret;
}
```

The Rust version is shorter, and — critically — the cleanup happens automatically through `Drop`. No goto chains. No forgetting to free a resource on one of five error paths.

## Current Limitations and the Road Ahead

Let me be upfront about what's still rough:

**API coverage is limited.** The Rust kernel abstractions cover a fraction of the kernel's surface area. Networking, filesystem internals, scheduler interfaces — many of these don't have Rust wrappers yet. You'll fall back to `unsafe` bindings.

**Compile times are slow.** The kernel already takes a while to build. Rust adds to that, especially the first time (building `core` for the kernel target).

**Debugger support isn't great.** GDB works with Rust, but kernel debugging with Rust code is still less polished than with C. Symbols, stack traces, and variable inspection can be hit-or-miss.

**Community friction exists.** Some kernel maintainers are enthusiastic about Rust. Others are... less so. Submitting Rust patches to subsystems maintained by C-only developers can be a process.

But the momentum is real. Android is using Rust for new kernel drivers. The Asahi Linux GPU driver for Apple Silicon is written in Rust. Google is funding full-time developers to build out the Rust kernel infrastructure.

## When to Write Kernel Rust

Write kernel modules in Rust when:
- You're starting a new driver from scratch
- The module handles untrusted input (network, USB, filesystem)
- You need strong concurrency guarantees
- You're on a team that knows Rust

Stick with C when:
- You're modifying existing C subsystems
- You need to interface heavily with C-only APIs without wrappers
- Your team doesn't know Rust (seriously — kernel development is hard enough)

## What's Next

We've seen Rust running in the kernel. Next, we're going to look at OS concepts from Rust's perspective — processes, threads, signals, and how Rust's safety guarantees interact with operating system primitives. We're moving from writing *inside* the kernel to understanding *how* the kernel works.
