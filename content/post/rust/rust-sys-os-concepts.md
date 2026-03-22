---
title: "Lesson 5: OS Concepts in Rust — Processes, threads, signals"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 5
keywords: [Rust, rust, systems programming, OS, processes, threads, signals, fork, syscall]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-07-19T07:45:19.000Z'
---

I used to think I understood processes. Then I tried to implement `fork()` semantics in Rust and realized I'd been cargo-culting UNIX concepts for years without actually understanding what was happening underneath.

Here's the thing — Rust forces you to think about OS primitives more carefully than C ever did. The ownership model doesn't just prevent memory bugs; it makes you confront questions like "who owns a file descriptor?" and "what happens to shared memory after fork?" that C lets you handwave past.

## Processes from the Ground Up

A process isn't your program. A process is the OS's *container* for your program. It's a data structure in the kernel that holds:

- A virtual address space (page tables mapping virtual → physical memory)
- File descriptor table (open files, sockets, pipes)
- Signal handlers and pending signals
- Scheduling state (running, sleeping, zombie)
- Credentials (uid, gid, capabilities)
- Resource limits

Let's interact with these from Rust using raw syscalls:

```rust
use std::process;
use std::os::unix::process::CommandExt;

fn process_info() {
    let pid = process::id();
    let ppid = unsafe { libc::getppid() };

    println!("PID: {}, Parent PID: {}", pid, ppid);

    // Get process memory maps
    let maps = std::fs::read_to_string(format!("/proc/{}/maps", pid))
        .unwrap();

    for line in maps.lines().take(5) {
        println!("  {}", line);
    }
}
```

But the really interesting stuff happens when you go lower:

```rust
use std::io;

/// Raw syscall wrapper for getpid
/// On x86_64 Linux, getpid is syscall number 39
fn raw_getpid() -> i64 {
    let result: i64;
    unsafe {
        core::arch::asm!(
            "syscall",
            in("rax") 39_u64,  // syscall number
            lateout("rax") result,
            // syscall clobbers rcx and r11
            out("rcx") _,
            out("r11") _,
            options(nostack, preserves_flags),
        );
    }
    result
}

/// Raw syscall wrapper for write
fn raw_write(fd: u64, buf: &[u8]) -> i64 {
    let result: i64;
    unsafe {
        core::arch::asm!(
            "syscall",
            in("rax") 1_u64,           // write syscall number
            in("rdi") fd,               // file descriptor
            in("rsi") buf.as_ptr(),     // buffer pointer
            in("rdx") buf.len() as u64, // buffer length
            lateout("rax") result,
            out("rcx") _,
            out("r11") _,
            options(nostack, preserves_flags),
        );
    }
    result
}

fn main() {
    let pid = raw_getpid();
    let msg = format!("My PID is {} (via raw syscall)\n", pid);
    raw_write(1, msg.as_bytes()); // fd 1 = stdout
}
```

This is the lowest level you can go without writing a kernel module. The `syscall` instruction is the hardware mechanism that transitions from user mode to kernel mode. Everything — `println!`, file I/O, memory allocation, thread creation — eventually boils down to a `syscall` instruction.

## fork() — The Most Controversial Syscall

`fork()` duplicates a process. The child gets a copy of the parent's entire address space, file descriptors, and state. It's elegant, weird, and a nightmare for Rust's ownership model:

```rust
use std::process;

fn demonstrate_fork() {
    println!("[parent] PID {} about to fork", process::id());

    let data = vec![1, 2, 3, 4, 5];

    let pid = unsafe { libc::fork() };

    match pid {
        -1 => {
            panic!("fork failed");
        }
        0 => {
            // Child process — we have a COPY of everything
            println!("[child] PID {}, parent was {}", process::id(),
                     unsafe { libc::getppid() });
            println!("[child] data = {:?}", data);
            // data is a copy — modifying it won't affect the parent
            // But! The Vec's heap allocation was copied too.
            // Rust's Drop will run in BOTH processes.
            // For Vec, that's fine (they have independent copies).
            // For resources like file locks or shared memory, it's NOT fine.
            std::process::exit(0);
        }
        child_pid => {
            // Parent process
            println!("[parent] spawned child {}", child_pid);

            // Wait for child to finish
            let mut status: i32 = 0;
            unsafe { libc::waitpid(child_pid, &mut status, 0) };

            println!("[parent] child exited with status {}", status);
        }
    }
}
```

Why is `fork()` problematic with Rust? Consider this:

```rust
use std::sync::{Arc, Mutex};

fn fork_with_mutex() {
    let shared = Arc::new(Mutex::new(42));

    let pid = unsafe { libc::fork() };

    if pid == 0 {
        // Child: we have a COPY of the Arc/Mutex
        // But the reference count wasn't atomically incremented for the child
        // The mutex's internal state might be inconsistent
        // If the parent held the lock during fork, the child inherits
        // a locked mutex that will NEVER be unlocked
        let guard = shared.lock().unwrap(); // Potential deadlock!
        println!("Child sees: {}", *guard);
        std::process::exit(0);
    }
}
```

This is why modern systems programming prefers `posix_spawn` or `Command` over raw `fork`:

```rust
use std::process::Command;

fn spawn_properly() {
    let output = Command::new("echo")
        .arg("Hello from child process")
        .env("MY_VAR", "my_value")
        .output()
        .expect("failed to execute process");

    println!("stdout: {}", String::from_utf8_lossy(&output.stdout));
    println!("exit code: {}", output.status.code().unwrap_or(-1));
}
```

`Command` is safe, composable, and doesn't have the fork-safety pitfalls.

## Threads — Shared Nothing (by Default)

Rust's threading model is fundamentally different from C's. In C, threads share everything — all memory is accessible from all threads. In Rust, threads share *nothing* unless you explicitly opt in:

```rust
use std::thread;
use std::sync::{Arc, Mutex, atomic::{AtomicU64, Ordering}};

fn threading_demo() {
    // Data that MOVES into the thread — owned by the thread
    let handle = thread::spawn(move || {
        let local_data = vec![1, 2, 3];
        // This data lives on this thread's stack and heap
        // No other thread can access it
        local_data.iter().sum::<i32>()
    });

    let result = handle.join().unwrap();
    println!("Thread returned: {}", result);
}

fn shared_counter() {
    let counter = Arc::new(AtomicU64::new(0));
    let mut handles = vec![];

    for _ in 0..8 {
        let counter = Arc::clone(&counter);
        handles.push(thread::spawn(move || {
            for _ in 0..1_000_000 {
                counter.fetch_add(1, Ordering::Relaxed);
            }
        }));
    }

    for h in handles {
        h.join().unwrap();
    }

    println!("Counter: {}", counter.load(Ordering::SeqCst));
    // Always exactly 8,000,000 — no data races possible
}
```

The key insight: `thread::spawn` requires `Send + 'static`. The `Send` trait means the data can safely cross thread boundaries. The `'static` lifetime means no borrowed references (you can't send a reference to stack data into a thread that might outlive it).

## Going Lower: Raw pthreads

Sometimes you need more control than `std::thread` provides — custom stack sizes, thread attributes, CPU affinity:

```rust
use std::io;
use std::ptr;

fn raw_thread_demo() -> io::Result<()> {
    let mut thread: libc::pthread_t = 0;
    let mut attr: libc::pthread_attr_t = unsafe { std::mem::zeroed() };

    unsafe {
        // Initialize thread attributes
        libc::pthread_attr_init(&mut attr);

        // Set custom stack size (2MB)
        libc::pthread_attr_setstacksize(&mut attr, 2 * 1024 * 1024);

        // Set detached state
        libc::pthread_attr_setdetachstate(
            &mut attr,
            libc::PTHREAD_CREATE_JOINABLE,
        );

        // Thread function
        extern "C" fn thread_fn(arg: *mut libc::c_void) -> *mut libc::c_void {
            let id = arg as usize;
            // Set CPU affinity — pin to a specific core
            let mut cpuset: libc::cpu_set_t = std::mem::zeroed();
            libc::CPU_ZERO(&mut cpuset);
            libc::CPU_SET(id % 4, &mut cpuset);
            libc::pthread_setaffinity_np(
                libc::pthread_self(),
                std::mem::size_of::<libc::cpu_set_t>(),
                &cpuset,
            );

            println!("Thread {} running on CPU (pinned to {})", id, id % 4);
            ptr::null_mut()
        }

        // Create the thread
        let ret = libc::pthread_create(
            &mut thread,
            &attr,
            thread_fn,
            42 as *mut libc::c_void,
        );

        libc::pthread_attr_destroy(&mut attr);

        if ret != 0 {
            return Err(io::Error::from_raw_os_error(ret));
        }

        // Wait for thread to finish
        libc::pthread_join(thread, ptr::null_mut());
    }

    Ok(())
}
```

## Signals — The Kernel's Interrupts for Userspace

Signals are the Unix mechanism for asynchronous event notification. They're also one of the most treacherous features in systems programming:

```rust
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

fn signal_handling() {
    // The safe way: use a flag
    let running = Arc::new(AtomicBool::new(true));
    let r = running.clone();

    ctrlc::set_handler(move || {
        println!("\nReceived Ctrl+C, shutting down...");
        r.store(false, Ordering::SeqCst);
    }).expect("Error setting Ctrl+C handler");

    println!("Running... press Ctrl+C to stop");
    while running.load(Ordering::SeqCst) {
        // Do work
        std::thread::sleep(std::time::Duration::from_millis(100));
    }
    println!("Clean shutdown complete");
}
```

For more control, use `signal-hook` or raw sigaction:

```rust
use std::io;

/// Install a signal handler using raw sigaction
unsafe fn install_signal_handler(
    signal: i32,
    handler: extern "C" fn(i32),
) -> io::Result<()> {
    let mut action: libc::sigaction = std::mem::zeroed();
    action.sa_sigaction = handler as usize;
    action.sa_flags = libc::SA_RESTART; // Restart interrupted syscalls

    // Block all signals during handler execution
    libc::sigfillset(&mut action.sa_mask);

    let ret = libc::sigaction(signal, &action, std::ptr::null_mut());
    if ret == -1 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

static mut SIGNAL_COUNT: i32 = 0;

extern "C" fn sigusr1_handler(sig: i32) {
    // WARNING: Very few things are safe to do in a signal handler
    // - No heap allocation (malloc is not signal-safe)
    // - No println! (uses locks internally)
    // - No mutex operations
    // Only "async-signal-safe" functions are allowed
    unsafe {
        SIGNAL_COUNT += 1;
        // write() is signal-safe, unlike printf/println
        let msg = b"Caught SIGUSR1\n";
        libc::write(2, msg.as_ptr() as *const libc::c_void, msg.len());
    }
}

fn signal_demo() {
    unsafe {
        install_signal_handler(libc::SIGUSR1, sigusr1_handler).unwrap();
    }

    println!("Send me SIGUSR1: kill -USR1 {}", std::process::id());

    loop {
        std::thread::sleep(std::time::Duration::from_secs(1));
        let count = unsafe { SIGNAL_COUNT };
        println!("Signals received: {}", count);
    }
}
```

Signal handlers are one of the few places where Rust's safety guarantees break down. Inside a signal handler, you're executing in an *arbitrary* context — the signal might have interrupted your code in the middle of a heap allocation. If your signal handler tries to allocate, you deadlock. If it tries to lock a mutex that the interrupted code held, you deadlock.

The safe pattern: in the signal handler, set an atomic flag. In the main loop, check the flag.

## Process Memory Layout

Understanding where things live in memory:

```rust
use std::alloc::{alloc, Layout};

fn memory_layout_tour() {
    // Stack
    let stack_var: u64 = 42;

    // Heap
    let heap_var = Box::new(99u64);

    // Static/BSS
    static STATIC_VAR: u64 = 100;
    static mut ZERO_INIT: u64 = 0; // BSS segment

    // Text (code)
    let fn_ptr = memory_layout_tour as *const ();

    // Dynamically allocated
    let raw_ptr = unsafe {
        alloc(Layout::new::<[u8; 4096]>())
    };

    println!("Stack:   {:p}", &stack_var);
    println!("Heap:    {:p}", &*heap_var);
    println!("Static:  {:p}", &STATIC_VAR);
    println!("Code:    {:p}", fn_ptr);
    println!("Malloc:  {:p}", raw_ptr);

    // Typical output on x86_64 Linux:
    // Stack:   0x7ffd12345678  (high addresses, grows down)
    // Heap:    0x5555abcd1234  (low addresses, grows up)
    // Static:  0x555555667788  (between code and heap)
    // Code:    0x555555554000  (lowest mapped region)
    // Malloc:  0x7f1234567890  (mmap region, between stack and heap)

    unsafe { std::alloc::dealloc(raw_ptr, Layout::new::<[u8; 4096]>()) };
}
```

## Building a Process Launcher

Let's combine these concepts into something practical — a mini process supervisor:

```rust
use std::collections::HashMap;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

struct ProcessSupervisor {
    children: HashMap<String, ManagedProcess>,
    running: Arc<AtomicBool>,
}

struct ManagedProcess {
    command: Vec<String>,
    child: Option<Child>,
    restart_count: u32,
    max_restarts: u32,
    last_start: Instant,
}

impl ProcessSupervisor {
    fn new() -> Self {
        let running = Arc::new(AtomicBool::new(true));
        let r = running.clone();

        ctrlc::set_handler(move || {
            r.store(false, Ordering::SeqCst);
        }).ok();

        Self {
            children: HashMap::new(),
            running,
        }
    }

    fn add_process(&mut self, name: &str, command: Vec<String>, max_restarts: u32) {
        self.children.insert(name.to_string(), ManagedProcess {
            command,
            child: None,
            restart_count: 0,
            max_restarts,
            last_start: Instant::now(),
        });
    }

    fn start_process(proc: &mut ManagedProcess) -> std::io::Result<()> {
        let child = Command::new(&proc.command[0])
            .args(&proc.command[1..])
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()?;

        println!("Started process PID {}", child.id());
        proc.child = Some(child);
        proc.last_start = Instant::now();
        Ok(())
    }

    fn run(&mut self) {
        // Start all processes
        for (name, proc) in self.children.iter_mut() {
            if let Err(e) = Self::start_process(proc) {
                eprintln!("Failed to start {}: {}", name, e);
            }
        }

        // Monitor loop
        while self.running.load(Ordering::SeqCst) {
            for (name, proc) in self.children.iter_mut() {
                if let Some(ref mut child) = proc.child {
                    match child.try_wait() {
                        Ok(Some(status)) => {
                            eprintln!("{} exited with {}", name, status);
                            proc.child = None;

                            // Restart if under limit
                            if proc.restart_count < proc.max_restarts {
                                proc.restart_count += 1;
                                eprintln!("Restarting {} (attempt {}/{})",
                                    name, proc.restart_count, proc.max_restarts);

                                if let Err(e) = Self::start_process(proc) {
                                    eprintln!("Restart failed: {}", e);
                                }
                            } else {
                                eprintln!("{} exceeded max restarts", name);
                            }
                        }
                        Ok(None) => {} // Still running
                        Err(e) => eprintln!("Error checking {}: {}", name, e),
                    }
                }
            }

            std::thread::sleep(Duration::from_millis(500));
        }

        // Graceful shutdown — send SIGTERM to all children
        println!("\nShutting down all processes...");
        for (name, proc) in self.children.iter_mut() {
            if let Some(ref mut child) = proc.child {
                println!("Terminating {}", name);
                child.kill().ok();
                child.wait().ok();
            }
        }
    }
}

fn main() {
    let mut supervisor = ProcessSupervisor::new();

    supervisor.add_process(
        "worker",
        vec!["sleep".into(), "10".into()],
        3,
    );

    supervisor.run();
}
```

This is a simplified version of what systemd, supervisord, or s6 do. The core concepts — process creation, status monitoring, signal-based shutdown, automatic restart — are all here.

## The Safety Boundary

Something I want to be explicit about: OS-level programming in Rust involves a lot of `unsafe`. Raw syscalls, signal handlers, shared memory, fork — these are inherently unsafe operations. Rust can't verify that a file descriptor is valid, or that a signal handler only calls async-signal-safe functions, or that fork doesn't duplicate a held mutex.

What Rust *can* do is contain the unsafety. Wrap the raw syscalls in safe abstractions. Use types to enforce invariants. Keep the `unsafe` blocks small and well-documented. The goal isn't zero `unsafe` — it's minimal, auditable `unsafe`.

Next up, we're going to build something with these primitives: a file system. From raw blocks to named files, all in Rust.
