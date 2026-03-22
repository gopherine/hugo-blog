---
title: "Lesson 10: Writing a Bootloader — The first code that runs"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 10
keywords: [Rust, rust, systems programming, bootloader, boot process, BIOS, UEFI, x86, bare metal]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-08-01T22:10:44.000Z'
---

There's something almost spiritual about writing a bootloader. Your code is the *first thing that runs* on a machine. Before the OS. Before any drivers. Before the memory manager. Before anything. The CPU comes out of reset, fetches an instruction from a known address, and that instruction is yours.

I spent a weekend writing one. By Sunday night, I had four characters on screen — `BOOT` — rendered by writing directly to VGA memory. And it felt like I'd conquered the world.

## The Boot Process on x86

When a PC powers on, here's what happens:

```
Power On
    │
    ├── CPU starts in Real Mode (16-bit, 1MB address space)
    │   Fetches first instruction from 0xFFFF:0x0000 (reset vector)
    │
    ├── BIOS/UEFI firmware runs
    │   - POST (Power-On Self Test)
    │   - Initializes basic hardware
    │   - Searches for bootable devices
    │
    ├── BIOS loads first sector (512 bytes) of boot device
    │   into memory at 0x7C00
    │   Checks for 0xAA55 signature at bytes 510-511
    │   Jumps to 0x7C00
    │
    ├── YOUR BOOTLOADER RUNS (Stage 1)
    │   - 512 bytes maximum (MBR)
    │   - Real Mode (16-bit)
    │   - Must load more code (Stage 2)
    │
    ├── Stage 2 bootloader
    │   - Switch to Protected Mode (32-bit) or Long Mode (64-bit)
    │   - Set up GDT, page tables
    │   - Load kernel into memory
    │   - Jump to kernel entry point
    │
    └── Kernel takes over
```

## Why Is This Hard?

Several reasons:

1. **512 bytes.** The BIOS loads exactly one sector. Your Stage 1 bootloader must fit in 512 bytes, including the boot signature.

2. **Real Mode.** The CPU starts in 16-bit mode with segmented addressing. You're limited to 1MB of address space.

3. **No standard library, no OS, no allocator.** Obviously.

4. **You set up everything.** The GDT (Global Descriptor Table), page tables, stack — it's all on you.

For our bootloader, we'll use UEFI instead of legacy BIOS. UEFI starts in a much nicer environment (64-bit mode, flat address space, firmware services) and is what modern machines actually use.

## A UEFI Bootloader in Rust

UEFI is more practical than legacy BIOS booting, and there's excellent Rust support via the `uefi` crate.

```toml
# Cargo.toml
[package]
name = "rust-bootloader"
version = "0.1.0"
edition = "2021"

[dependencies]
uefi = "0.32"
uefi-services = "0.25"
log = "0.4"

[profile.release]
opt-level = "s"
lto = true
```

`.cargo/config.toml`:

```toml
[build]
target = "x86_64-unknown-uefi"

[unstable]
build-std = ["core", "alloc"]
build-std-features = ["compiler-builtins-mem"]
```

The bootloader itself:

```rust
#![no_std]
#![no_main]

use core::fmt::Write;
use uefi::prelude::*;
use uefi::proto::console::text::Color;
use uefi::proto::media::file::{
    File, FileAttribute, FileInfo, FileMode, RegularFile,
};
use uefi::proto::media::fs::SimpleFileSystem;
use uefi::table::boot::{AllocateType, MemoryType};
use uefi::CStr16;

// Kernel load address — must match what the kernel expects
const KERNEL_LOAD_ADDR: u64 = 0x100000; // 1MB

#[entry]
fn main(image: Handle, mut system_table: SystemTable<Boot>) -> Status {
    // Initialize UEFI services (logging, allocation)
    uefi::helpers::init(&mut system_table).unwrap();

    let stdout = system_table.stdout();
    stdout.clear().unwrap();
    stdout.set_color(Color::LightGreen, Color::Black).unwrap();
    writeln!(stdout, "=== Rust Bootloader v0.1 ===").unwrap();
    writeln!(stdout).unwrap();

    // Step 1: Get the memory map
    writeln!(stdout, "[*] Querying memory map...").unwrap();
    print_memory_map(&system_table);

    // Step 2: Load the kernel from the boot filesystem
    writeln!(stdout, "[*] Loading kernel...").unwrap();
    let kernel_size = load_kernel(&system_table, image);
    writeln!(stdout, "    Loaded {} bytes at 0x{:X}",
             kernel_size, KERNEL_LOAD_ADDR).unwrap();

    // Step 3: Set up framebuffer for the kernel
    writeln!(stdout, "[*] Configuring framebuffer...").unwrap();
    let fb_info = setup_framebuffer(&system_table);

    // Step 4: Exit boot services and jump to kernel
    writeln!(stdout, "[*] Exiting boot services...").unwrap();
    writeln!(stdout, "[*] Jumping to kernel at 0x{:X}", KERNEL_LOAD_ADDR).unwrap();

    // After ExitBootServices, we can't use any UEFI services
    // The kernel is on its own
    let (runtime, memory_map) = system_table.exit_boot_services(MemoryType::LOADER_DATA);

    // Build boot info struct for the kernel
    let boot_info = BootInfo {
        memory_map_addr: 0, // Would be filled with actual memory map
        memory_map_entries: 0,
        framebuffer: fb_info,
    };

    // Jump to kernel entry point
    unsafe {
        let kernel_entry: extern "sysv64" fn(&BootInfo) -> ! =
            core::mem::transmute(KERNEL_LOAD_ADDR as *const ());
        kernel_entry(&boot_info);
    }
}

/// Information passed from bootloader to kernel
#[repr(C)]
struct BootInfo {
    memory_map_addr: u64,
    memory_map_entries: u64,
    framebuffer: FramebufferInfo,
}

#[repr(C)]
#[derive(Clone, Copy)]
struct FramebufferInfo {
    address: u64,
    width: u32,
    height: u32,
    stride: u32, // Pixels per row (may differ from width due to padding)
    bpp: u32,    // Bits per pixel
}

fn print_memory_map(st: &SystemTable<Boot>) {
    // Query memory map size and allocate buffer
    // In a real bootloader, this gives us the physical memory layout
    // that we pass to the kernel
}

fn load_kernel(st: &SystemTable<Boot>, image: Handle) -> usize {
    let bs = st.boot_services();

    // Open the filesystem
    let fs_handle = bs
        .get_handle_for_protocol::<SimpleFileSystem>()
        .unwrap();
    let mut fs = bs
        .open_protocol_exclusive::<SimpleFileSystem>(fs_handle)
        .unwrap();

    // Open root directory
    let mut root = fs.open_volume().unwrap();

    // Open kernel file
    let kernel_path = CStr16::from_str_with_buf(
        "\\EFI\\BOOT\\kernel.elf",
        &mut [0u16; 64],
    ).unwrap();

    let kernel_file = root
        .open(kernel_path, FileMode::Read, FileAttribute::empty())
        .unwrap();

    let mut kernel_file: RegularFile = kernel_file
        .into_regular_file()
        .expect("kernel.elf is not a regular file");

    // Get file size
    let mut info_buf = [0u8; 256];
    let info: &FileInfo = kernel_file.get_info(&mut info_buf).unwrap();
    let kernel_size = info.file_size() as usize;

    // Allocate pages at the kernel load address
    let pages = (kernel_size + 4095) / 4096;
    bs.allocate_pages(
        AllocateType::Address(KERNEL_LOAD_ADDR),
        MemoryType::LOADER_DATA,
        pages,
    ).expect("failed to allocate memory for kernel");

    // Read kernel into memory
    let kernel_buf = unsafe {
        core::slice::from_raw_parts_mut(KERNEL_LOAD_ADDR as *mut u8, kernel_size)
    };
    kernel_file.read(kernel_buf).unwrap();

    kernel_size
}

fn setup_framebuffer(st: &SystemTable<Boot>) -> FramebufferInfo {
    // In a real bootloader, you'd use the GOP (Graphics Output Protocol)
    // to set a video mode and get the framebuffer address
    FramebufferInfo {
        address: 0,
        width: 1024,
        height: 768,
        stride: 1024,
        bpp: 32,
    }
}
```

## ELF Loading — Parsing the Kernel Binary

A real bootloader needs to parse ELF headers to load the kernel properly:

```rust
/// Minimal ELF64 header parsing
#[repr(C)]
#[derive(Debug, Clone, Copy)]
struct Elf64Header {
    magic: [u8; 4],       // 0x7F, 'E', 'L', 'F'
    class: u8,             // 2 = 64-bit
    endian: u8,            // 1 = little endian
    version: u8,
    os_abi: u8,
    _padding: [u8; 8],
    elf_type: u16,         // 2 = executable
    machine: u16,          // 0x3E = x86_64
    version2: u32,
    entry: u64,            // Entry point address
    phoff: u64,            // Program header table offset
    shoff: u64,            // Section header table offset
    flags: u32,
    ehsize: u16,
    phentsize: u16,        // Program header entry size
    phnum: u16,            // Number of program headers
    shentsize: u16,
    shnum: u16,
    shstrndx: u16,
}

#[repr(C)]
#[derive(Debug, Clone, Copy)]
struct Elf64ProgramHeader {
    seg_type: u32,         // 1 = PT_LOAD
    flags: u32,
    offset: u64,           // Offset in file
    vaddr: u64,            // Virtual address to load at
    paddr: u64,            // Physical address (usually same as vaddr)
    filesz: u64,           // Size in file
    memsz: u64,            // Size in memory (may be larger — BSS)
    align: u64,
}

const PT_LOAD: u32 = 1;

fn load_elf(elf_data: &[u8]) -> Result<u64, &'static str> {
    // Validate ELF header
    if elf_data.len() < core::mem::size_of::<Elf64Header>() {
        return Err("file too small for ELF header");
    }

    let header = unsafe { &*(elf_data.as_ptr() as *const Elf64Header) };

    if &header.magic != b"\x7FELF" {
        return Err("invalid ELF magic");
    }

    if header.class != 2 {
        return Err("not a 64-bit ELF");
    }

    if header.machine != 0x3E {
        return Err("not an x86_64 ELF");
    }

    // Load each PT_LOAD segment
    let ph_offset = header.phoff as usize;
    let ph_size = header.phentsize as usize;

    for i in 0..header.phnum as usize {
        let ph_start = ph_offset + i * ph_size;
        let ph = unsafe {
            &*(elf_data.as_ptr().add(ph_start) as *const Elf64ProgramHeader)
        };

        if ph.seg_type != PT_LOAD {
            continue;
        }

        // Copy segment data from file to memory
        let src = &elf_data[ph.offset as usize..(ph.offset + ph.filesz) as usize];
        let dst = ph.vaddr as *mut u8;

        unsafe {
            core::ptr::copy_nonoverlapping(src.as_ptr(), dst, ph.filesz as usize);

            // Zero out BSS (memsz > filesz)
            if ph.memsz > ph.filesz {
                let bss_start = dst.add(ph.filesz as usize);
                let bss_size = (ph.memsz - ph.filesz) as usize;
                core::ptr::write_bytes(bss_start, 0, bss_size);
            }
        }
    }

    Ok(header.entry)
}
```

## Page Tables — Setting Up Virtual Memory

Before handing off to a 64-bit kernel, the bootloader must set up page tables:

```rust
/// x86_64 page table entry
#[repr(transparent)]
#[derive(Clone, Copy)]
struct PageTableEntry(u64);

impl PageTableEntry {
    const PRESENT: u64 = 1 << 0;
    const WRITABLE: u64 = 1 << 1;
    const HUGE_PAGE: u64 = 1 << 7; // 2MB pages at PD level

    fn new(phys_addr: u64, flags: u64) -> Self {
        Self((phys_addr & 0x000F_FFFF_FFFF_F000) | flags)
    }

    fn empty() -> Self {
        Self(0)
    }
}

/// A 4-level page table (PML4 → PDPT → PD → PT)
#[repr(align(4096))]
struct PageTable {
    entries: [PageTableEntry; 512],
}

impl PageTable {
    fn new() -> Self {
        Self {
            entries: [PageTableEntry::empty(); 512],
        }
    }
}

/// Set up identity mapping for the first N gigabytes
/// (virtual address = physical address)
/// Uses 2MB huge pages for simplicity
unsafe fn setup_page_tables(gigabytes: usize) -> *const PageTable {
    // In a real bootloader, these would be allocated from the UEFI memory map
    // For illustration, we use static storage
    static mut PML4: PageTable = PageTable { entries: [PageTableEntry(0); 512] };
    static mut PDPT: PageTable = PageTable { entries: [PageTableEntry(0); 512] };
    // We'd need one PD per GB
    static mut PD: [PageTable; 4] = [PageTable { entries: [PageTableEntry(0); 512] }; 4];

    let pml4 = &mut PML4;
    let pdpt = &mut PDPT;

    // PML4[0] -> PDPT
    pml4.entries[0] = PageTableEntry::new(
        pdpt as *const _ as u64,
        PageTableEntry::PRESENT | PageTableEntry::WRITABLE,
    );

    for gb in 0..gigabytes.min(4) {
        // PDPT[gb] -> PD[gb]
        pdpt.entries[gb] = PageTableEntry::new(
            &PD[gb] as *const _ as u64,
            PageTableEntry::PRESENT | PageTableEntry::WRITABLE,
        );

        // Each PD entry maps 2MB
        for mb in 0..512 {
            let phys_addr = (gb * 0x4000_0000 + mb * 0x20_0000) as u64;
            PD[gb].entries[mb] = PageTableEntry::new(
                phys_addr,
                PageTableEntry::PRESENT
                    | PageTableEntry::WRITABLE
                    | PageTableEntry::HUGE_PAGE,
            );
        }
    }

    pml4 as *const PageTable
}

/// Load page tables and enable paging
unsafe fn enable_paging(pml4_addr: *const PageTable) {
    // Load CR3 with the PML4 physical address
    core::arch::asm!(
        "mov cr3, {}",
        in(reg) pml4_addr as u64,
        options(nostack, preserves_flags),
    );
}
```

## Testing with QEMU

You don't need real hardware to test a bootloader. QEMU with OVMF (open-source UEFI firmware) works great:

```bash
# Build the bootloader
cargo build --release --target x86_64-unknown-uefi

# Create a disk image
mkdir -p esp/EFI/BOOT
cp target/x86_64-unknown-uefi/release/rust-bootloader.efi esp/EFI/BOOT/BOOTX64.EFI

# Run in QEMU with UEFI firmware
qemu-system-x86_64 \
    -bios /usr/share/OVMF/OVMF_CODE.fd \
    -drive format=raw,file=fat:rw:esp \
    -serial stdio \
    -m 512M \
    -no-reboot
```

For development, you'll want to add debug output over the serial port:

```rust
/// Write to the serial port (0x3F8 = COM1)
fn serial_print(s: &str) {
    for byte in s.bytes() {
        unsafe {
            // Wait for transmit buffer empty
            while (x86_64_inb(0x3FD) & 0x20) == 0 {}
            x86_64_outb(0x3F8, byte);
        }
    }
}

unsafe fn x86_64_outb(port: u16, value: u8) {
    core::arch::asm!(
        "out dx, al",
        in("dx") port,
        in("al") value,
        options(nostack, preserves_flags),
    );
}

unsafe fn x86_64_inb(port: u16) -> u8 {
    let value: u8;
    core::arch::asm!(
        "in al, dx",
        in("dx") port,
        out("al") value,
        options(nostack, preserves_flags),
    );
    value
}
```

## The Bootloader Ecosystem

In practice, most Rust OS projects use one of these:

**[bootloader](https://crates.io/crates/bootloader)** — Philipp Oppermann's bootloader crate, used by the excellent [Writing an OS in Rust](https://os.phil-opp.com/) blog series. Handles BIOS and UEFI.

**[limine](https://limine-bootloader.org/)** — A modern bootloader with a clean Rust-friendly protocol.

**[bootimage](https://crates.io/crates/bootimage)** — Cargo subcommand that creates bootable disk images.

These handle the hundreds of edge cases we've skipped — A20 line, multiboot compliance, ACPI table discovery, multi-core startup. Writing your own is educational. Shipping one of these is practical.

## What Makes Boot Code Special

Bootloader code has constraints that no other code shares:

- **No stack until you set one up.** The first thing your bootloader does is set `RSP` to a valid memory region.
- **No assumptions about memory state.** RAM might contain anything — zeros, garbage, or data from the previous boot.
- **Hardware is in an unknown state.** Devices need explicit initialization before use.
- **Mistakes are invisible.** No debugger, no serial output, no screen — until you set those up yourself.

This is the most "bare metal" you can get. And honestly? It's addicting. Once you've booted a machine with your own code, writing a web server feels almost boring.

Next up: we go from booting machines to virtualizing them — writing a hypervisor in Rust.
