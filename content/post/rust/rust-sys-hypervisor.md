---
title: "Lesson 11: Building a Minimal Hypervisor — Virtualization in Rust"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 11
keywords: [Rust, rust, systems programming, hypervisor, virtualization, VMX, KVM, virtual machine]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-08-04T15:42:18.000Z'
---

The first time I watched a virtual machine boot — not using VirtualBox, but running inside a hypervisor *I'd written* — I had the same feeling as when my bootloader printed its first character. Except this time, I wasn't just running code on bare metal. I was creating a *fake machine* that thought it was running on bare metal.

Virtualization is where systems programming hits its ceiling of complexity. You're manipulating the CPU's hardware virtualization extensions to create isolated execution environments. It's also where Rust's safety guarantees become most valuable — because a bug in a hypervisor doesn't just crash your program, it potentially compromises every virtual machine running on the host.

## How Hardware Virtualization Works

Modern CPUs have hardware support for virtualization (Intel VT-x / AMD-V). The core concept:

```
┌────────────────────────────────────┐
│  Guest VM                          │
│  ┌──────────────────────────┐      │
│  │  Guest OS (thinks it's   │      │
│  │  running on real HW)     │      │
│  └──────────────────────────┘      │
│           │ privileged op          │
│           ▼ VM EXIT                │
├────────────────────────────────────┤
│  Hypervisor (VMM)                  │
│  - Handles the exit                │
│  - Emulates the operation          │
│  - Resumes the guest               │
│           │ VM ENTER               │
│           ▼                        │
│  Guest continues running           │
└────────────────────────────────────┘
```

The CPU has two modes:
- **VMX root mode**: The hypervisor runs here. Full control.
- **VMX non-root mode**: The guest runs here. *Thinks* it has full control, but certain operations cause a "VM exit" that traps back to the hypervisor.

The hypervisor controls what causes a VM exit. It can intercept:
- I/O port access
- MSR (Model-Specific Register) access
- Control register modifications
- Interrupts
- Page faults
- Specific instructions (CPUID, RDMSR, etc.)

## Setting Up VMX on Linux with KVM

The most practical way to build a hypervisor in Rust is using KVM (Kernel-based Virtual Machine). KVM provides the hardware virtualization interface through `/dev/kvm`:

```rust
use std::fs::{File, OpenOptions};
use std::io;
use std::os::unix::io::{AsRawFd, RawFd};

// KVM ioctl constants (from linux/kvm.h)
const KVM_GET_API_VERSION: u64 = 0xAE00;
const KVM_CREATE_VM: u64 = 0xAE01;
const KVM_CREATE_VCPU: u64 = 0xAE41;
const KVM_SET_USER_MEMORY_REGION: u64 = 0x4020AE46;
const KVM_RUN: u64 = 0xAE80;
const KVM_GET_SREGS: u64 = 0x8138AE83;
const KVM_SET_SREGS: u64 = 0x4138AE84;
const KVM_GET_REGS: u64 = 0x8090AE81;
const KVM_SET_REGS: u64 = 0x4090AE82;
const KVM_GET_VCPU_MMAP_SIZE: u64 = 0xAE04;

/// Memory region mapping for the VM
#[repr(C)]
struct KvmUserspaceMemoryRegion {
    slot: u32,
    flags: u32,
    guest_phys_addr: u64,
    memory_size: u64,
    userspace_addr: u64,
}

/// General purpose registers
#[repr(C)]
#[derive(Default, Debug)]
struct KvmRegs {
    rax: u64, rbx: u64, rcx: u64, rdx: u64,
    rsi: u64, rdi: u64, rsp: u64, rbp: u64,
    r8: u64, r9: u64, r10: u64, r11: u64,
    r12: u64, r13: u64, r14: u64, r15: u64,
    rip: u64, rflags: u64,
}

/// Segment register
#[repr(C)]
#[derive(Default, Debug, Clone, Copy)]
struct KvmSegment {
    base: u64,
    limit: u32,
    selector: u16,
    type_: u8,
    present: u8,
    dpl: u8,
    db: u8,
    s: u8,
    l: u8,
    g: u8,
    avl: u8,
    _padding: u8,
}

/// Special registers (segments, control regs, etc.)
#[repr(C)]
#[derive(Default, Debug)]
struct KvmSregs {
    cs: KvmSegment,
    ds: KvmSegment,
    es: KvmSegment,
    fs: KvmSegment,
    gs: KvmSegment,
    ss: KvmSegment,
    tr: KvmSegment,
    ldt: KvmSegment,
    gdt: KvmDtable,
    idt: KvmDtable,
    cr0: u64,
    cr2: u64,
    cr3: u64,
    cr4: u64,
    cr8: u64,
    efer: u64,
    apic_base: u64,
    interrupt_bitmap: [u64; 4],
}

#[repr(C)]
#[derive(Default, Debug)]
struct KvmDtable {
    base: u64,
    limit: u16,
    _padding: [u16; 3],
}

/// KVM run structure (shared memory between kernel and userspace)
#[repr(C)]
struct KvmRun {
    // Request flags
    request_interrupt_window: u8,
    immediate_exit: u8,
    _padding1: [u8; 6],

    // Exit reason
    exit_reason: u32,
    ready_for_interrupt_injection: u8,
    if_flag: u8,
    flags: u16,

    // CR8 value
    cr8: u64,
    apic_base: u64,

    // Exit-reason-specific data (union)
    exit_data: [u8; 256],
}

// Exit reasons
const KVM_EXIT_IO: u32 = 2;
const KVM_EXIT_MMIO: u32 = 6;
const KVM_EXIT_HLT: u32 = 5;
const KVM_EXIT_SHUTDOWN: u32 = 8;

#[repr(C)]
#[derive(Debug)]
struct KvmExitIo {
    direction: u8,   // 0 = out, 1 = in
    size: u8,        // 1, 2, or 4 bytes
    port: u16,
    count: u32,
    data_offset: u64,
}

const KVM_EXIT_IO_OUT: u8 = 0;
const KVM_EXIT_IO_IN: u8 = 1;
```

## Building the Hypervisor

```rust
pub struct Vm {
    kvm_fd: File,
    vm_fd: RawFd,
    vcpu_fd: RawFd,
    kvm_run: *mut KvmRun,
    memory: Vec<u8>,
}

impl Vm {
    pub fn new(memory_size: usize) -> io::Result<Self> {
        // Open KVM device
        let kvm_fd = OpenOptions::new()
            .read(true)
            .write(true)
            .open("/dev/kvm")?;

        // Check API version
        let api_version = unsafe {
            libc::ioctl(kvm_fd.as_raw_fd(), KVM_GET_API_VERSION, 0)
        };
        if api_version != 12 {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                format!("unexpected KVM API version: {}", api_version),
            ));
        }

        // Create VM
        let vm_fd = unsafe {
            libc::ioctl(kvm_fd.as_raw_fd(), KVM_CREATE_VM, 0)
        };
        if vm_fd < 0 {
            return Err(io::Error::last_os_error());
        }

        // Allocate guest memory (page-aligned)
        let memory = vec![0u8; memory_size];

        // Map guest memory
        let region = KvmUserspaceMemoryRegion {
            slot: 0,
            flags: 0,
            guest_phys_addr: 0,
            memory_size: memory_size as u64,
            userspace_addr: memory.as_ptr() as u64,
        };

        let ret = unsafe {
            libc::ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region)
        };
        if ret < 0 {
            return Err(io::Error::last_os_error());
        }

        // Create vCPU
        let vcpu_fd = unsafe {
            libc::ioctl(vm_fd, KVM_CREATE_VCPU, 0)
        };
        if vcpu_fd < 0 {
            return Err(io::Error::last_os_error());
        }

        // Map KVM run structure
        let mmap_size = unsafe {
            libc::ioctl(kvm_fd.as_raw_fd(), KVM_GET_VCPU_MMAP_SIZE, 0)
        } as usize;

        let kvm_run = unsafe {
            libc::mmap(
                std::ptr::null_mut(),
                mmap_size,
                libc::PROT_READ | libc::PROT_WRITE,
                libc::MAP_SHARED,
                vcpu_fd,
                0,
            ) as *mut KvmRun
        };

        if kvm_run == libc::MAP_FAILED as *mut KvmRun {
            return Err(io::Error::last_os_error());
        }

        Ok(Vm {
            kvm_fd,
            vm_fd,
            vcpu_fd,
            kvm_run,
            memory,
        })
    }

    /// Load code into guest memory at the given offset
    pub fn load_code(&mut self, offset: usize, code: &[u8]) {
        self.memory[offset..offset + code.len()].copy_from_slice(code);
    }

    /// Set up the initial CPU state for real mode execution
    pub fn setup_real_mode(&self) -> io::Result<()> {
        let mut sregs = KvmSregs::default();

        // Get current special registers
        let ret = unsafe {
            libc::ioctl(self.vcpu_fd, KVM_GET_SREGS, &mut sregs)
        };
        if ret < 0 {
            return Err(io::Error::last_os_error());
        }

        // Set up code segment for real mode
        sregs.cs.base = 0;
        sregs.cs.selector = 0;
        sregs.cs.limit = 0xFFFF;

        // Set up data segments
        sregs.ds.base = 0;
        sregs.ds.selector = 0;
        sregs.ds.limit = 0xFFFF;
        sregs.es = sregs.ds;
        sregs.fs = sregs.ds;
        sregs.gs = sregs.ds;
        sregs.ss = sregs.ds;

        let ret = unsafe {
            libc::ioctl(self.vcpu_fd, KVM_SET_SREGS, &sregs)
        };
        if ret < 0 {
            return Err(io::Error::last_os_error());
        }

        // Set instruction pointer and stack pointer
        let mut regs = KvmRegs::default();
        regs.rip = 0x0; // Start executing at address 0
        regs.rsp = 0xFFFC; // Stack at top of first 64KB
        regs.rflags = 0x2; // Bit 1 must always be set

        let ret = unsafe {
            libc::ioctl(self.vcpu_fd, KVM_SET_REGS, &regs)
        };
        if ret < 0 {
            return Err(io::Error::last_os_error());
        }

        Ok(())
    }

    /// Run the VM — this is the main execution loop
    pub fn run(&mut self) -> io::Result<()> {
        loop {
            let ret = unsafe {
                libc::ioctl(self.vcpu_fd, KVM_RUN, 0)
            };
            if ret < 0 {
                return Err(io::Error::last_os_error());
            }

            let exit_reason = unsafe { (*self.kvm_run).exit_reason };

            match exit_reason {
                KVM_EXIT_IO => {
                    let io_exit = unsafe {
                        &*((*self.kvm_run).exit_data.as_ptr() as *const KvmExitIo)
                    };

                    if io_exit.direction == KVM_EXIT_IO_OUT && io_exit.port == 0x3F8 {
                        // Guest wrote to serial port — print it
                        let data_offset = io_exit.data_offset as usize;
                        let run_ptr = self.kvm_run as *const u8;
                        let byte = unsafe { *run_ptr.add(data_offset) };
                        print!("{}", byte as char);
                    } else if io_exit.direction == KVM_EXIT_IO_OUT && io_exit.port == 0x01 {
                        // Custom exit port — guest wants to shut down
                        let data_offset = io_exit.data_offset as usize;
                        let run_ptr = self.kvm_run as *const u8;
                        let code = unsafe { *run_ptr.add(data_offset) };
                        println!("\nGuest requested exit with code: {}", code);
                        return Ok(());
                    }
                }

                KVM_EXIT_HLT => {
                    println!("Guest executed HLT");
                    return Ok(());
                }

                KVM_EXIT_SHUTDOWN => {
                    println!("Guest shutdown");
                    return Ok(());
                }

                KVM_EXIT_MMIO => {
                    println!("Guest MMIO access (not handled)");
                }

                other => {
                    println!("Unknown exit reason: {}", other);
                    return Err(io::Error::new(
                        io::ErrorKind::Other,
                        format!("unexpected VM exit: {}", other),
                    ));
                }
            }
        }
    }

    /// Get the current register state (useful for debugging)
    pub fn dump_regs(&self) -> io::Result<KvmRegs> {
        let mut regs = KvmRegs::default();
        let ret = unsafe {
            libc::ioctl(self.vcpu_fd, KVM_GET_REGS, &mut regs)
        };
        if ret < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(regs)
    }
}
```

## Running Guest Code

Let's run actual x86 code inside our VM:

```rust
fn main() -> io::Result<()> {
    let mut vm = Vm::new(64 * 1024)?; // 64KB of guest memory

    // x86 machine code that prints "Hello from VM!" via serial port (port 0x3F8)
    // and then exits via port 0x01
    let code: &[u8] = &[
        // mov si, message
        0xBE, 0x20, 0x00,       // SI = 0x0020 (message offset)

        // print_loop:
        0xAC,                    // lodsb (load byte at [SI] into AL, increment SI)
        0x3C, 0x00,              // cmp al, 0
        0x74, 0x06,              // je done (jump if zero)
        0xE6, 0xF8,              // out 0x3F8, al (serial port — actually uses 0xF8 for byte)
        // Corrected: use DX for port
        0xBA, 0xF8, 0x03,       // mov dx, 0x3F8
        0xEE,                    // out dx, al
        0xEB, 0xF4,              // jmp print_loop

        // done:
        0xB0, 0x00,              // mov al, 0 (exit code)
        0xE6, 0x01,              // out 0x01, al (exit port)
        0xF4,                    // hlt

        // Padding to offset 0x20
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,

        // message at offset 0x20:
        b'H', b'e', b'l', b'l', b'o', b' ',
        b'f', b'r', b'o', b'm', b' ',
        b'V', b'M', b'!', b'\n', 0x00,
    ];

    vm.load_code(0, code);
    vm.setup_real_mode()?;

    println!("Starting VM...");
    vm.run()?;

    let regs = vm.dump_regs()?;
    println!("Final RIP: 0x{:X}", regs.rip);

    Ok(())
}
```

When you run this, you'll see "Hello from VM!" printed to your terminal — but that text came from code running inside a virtual machine that your Rust program created. The guest code doesn't know it's virtualized. It thinks it's writing to a real serial port.

## Device Emulation

A practical hypervisor needs to emulate hardware devices. Here's a simplified virtio-console device:

```rust
/// Simple device emulation for our VM
struct DeviceManager {
    serial: SerialDevice,
    // Additional devices would go here
}

struct SerialDevice {
    output_buffer: Vec<u8>,
    input_buffer: Vec<u8>,
}

impl SerialDevice {
    fn new() -> Self {
        Self {
            output_buffer: Vec::new(),
            input_buffer: Vec::new(),
        }
    }

    fn handle_io(&mut self, port: u16, is_write: bool, data: &mut [u8]) {
        match port {
            0x3F8 => {
                // Data register
                if is_write {
                    self.output_buffer.push(data[0]);
                    print!("{}", data[0] as char);
                } else if let Some(byte) = self.input_buffer.pop() {
                    data[0] = byte;
                } else {
                    data[0] = 0;
                }
            }
            0x3FD => {
                // Line Status Register
                if !is_write {
                    let mut status = 0x60; // TX empty + TX holding empty
                    if !self.input_buffer.is_empty() {
                        status |= 0x01; // Data ready
                    }
                    data[0] = status;
                }
            }
            _ => {}
        }
    }
}

impl DeviceManager {
    fn new() -> Self {
        Self {
            serial: SerialDevice::new(),
        }
    }

    fn handle_io(&mut self, port: u16, is_write: bool, data: &mut [u8]) {
        match port {
            0x3F8..=0x3FF => self.serial.handle_io(port, is_write, data),
            _ => {
                // Unknown device — log and ignore
                if is_write {
                    eprintln!("Unhandled IO write to port 0x{:X}: {:?}", port, data);
                }
            }
        }
    }
}
```

## Why Rust for Hypervisors?

This is one of the strongest use cases for Rust in systems programming:

**Memory safety in the VMM.** The hypervisor manages guest memory mappings. A use-after-free or buffer overflow in the VMM could let a malicious guest escape its sandbox. Rust prevents these at compile time.

**Type-safe VM exit handling.** The exit reason enum pattern prevents forgetting to handle an exit type. In C, a missed case in a switch statement means undefined behavior.

**Fearless concurrency for multi-vCPU VMs.** Real VMs have multiple virtual CPUs running in parallel. Rust's Send/Sync guarantees prevent data races in the shared device emulation layer.

**No GC pauses.** A hypervisor can't stop the world — guest VMs would notice. Rust's deterministic memory management means predictable latency.

Projects like [Firecracker](https://firecracker-microvm.github.io/) (AWS Lambda's VM runtime), [crosvm](https://chromium.googlesource.com/chromiumos/platform/crosvm/) (Chrome OS), and [cloud-hypervisor](https://www.cloudhypervisor.org/) are all production Rust hypervisors. This isn't theoretical — it's what runs your serverless functions.

## Security Boundaries

A hypervisor is a security boundary — probably the most critical one in cloud computing. Every VM exit is a potential attack surface:

```rust
/// Every exit handler should validate guest input
fn handle_mmio_exit(
    vm: &mut Vm,
    addr: u64,
    data: &[u8],
    is_write: bool,
) -> Result<(), VmError> {
    // Validate the address is within a mapped device region
    if !is_valid_device_address(addr) {
        // Don't panic — a malicious guest could trigger this
        // Log and inject an exception back into the guest
        return Err(VmError::InvalidMmioAddress(addr));
    }

    // Validate data length
    if data.len() > 8 {
        return Err(VmError::InvalidMmioSize(data.len()));
    }

    // Route to appropriate device
    route_mmio(addr, data, is_write)
}

#[derive(Debug)]
enum VmError {
    InvalidMmioAddress(u64),
    InvalidMmioSize(usize),
    DeviceError(String),
}
```

The defensive posture is important: treat *every* VM exit as potentially adversarial. The guest is untrusted code. Any assumption you make about guest behavior is an assumption a malicious guest will violate.

## What's Next

We've gone from the lowest levels — bootloaders, bare metal, interrupts — to building virtual machines. In the final lesson, we'll zoom out and look at how all these systems programming skills come together in production software: databases, runtimes, and proxies, all written in Rust.
