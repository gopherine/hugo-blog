---
title: "Lesson 3: Memory-Mapped I/O — Talking to hardware"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 3
keywords: [Rust, rust, systems programming, MMIO, memory-mapped IO, volatile, hardware registers]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-07-14T19:52:07.000Z'
---

There's a moment in every systems programmer's life when they realize that writing to memory address `0x4002_0818` doesn't store a value — it turns on an LED. That address isn't RAM. It's a hardware register. And the CPU doesn't know the difference.

That's memory-mapped I/O in a nutshell, and it's how virtually all hardware communication works on modern processors. Understanding it properly is the difference between code that happens to work and code that's *correct*.

## The Mental Model

On most architectures, peripherals (GPIOs, timers, UARTs, DMA controllers) are mapped into the CPU's address space. From the CPU's perspective, reading from `0x4002_0810` is the same instruction as reading from a RAM address. But the memory controller routes that access to a peripheral instead of actual memory.

```
CPU Address Space
────────────────────
0x0000_0000 ┌──────────┐
            │  Flash   │  ← Code lives here
0x0800_0000 ├──────────┤
            │  ...     │
0x2000_0000 ├──────────┤
            │   RAM    │  ← Variables, stack, heap
0x2002_0000 ├──────────┤
            │  ...     │
0x4000_0000 ├──────────┤
            │Peripher- │  ← Hardware registers
            │  als     │     (GPIO, UART, SPI, etc.)
0x5000_0000 ├──────────┤
            │  ...     │
            └──────────┘
```

The key insight: when you write to a peripheral register, **side effects happen**. A motor turns. A byte gets transmitted over a wire. An interrupt gets acknowledged. The compiler's optimizer has no idea about any of this — and that's where things get dangerous.

## The Volatile Problem

Consider this C-like pseudocode:

```rust
// DON'T do this — the optimizer will eat your code
unsafe {
    let status_reg = 0x4000_0000 as *const u32;

    // Wait for hardware to set bit 0
    while (*status_reg & 1) == 0 {
        // spin
    }
}
```

The optimizer sees: "You're reading from the same address in a loop, and nothing in this loop writes to it. I'll just read it once and cache the result." Your infinite loop either never exits (if the bit was 0 the first time) or exits immediately (if it was 1). The optimizer is *correct* — from its model of memory. But hardware registers aren't normal memory.

`volatile` tells the compiler: "Every read from this address must actually happen. Every write must actually happen. Don't optimize them away, don't reorder them."

```rust
use core::ptr;

unsafe {
    let status_reg = 0x4000_0000 as *const u32;

    // This actually reads the register every iteration
    while (ptr::read_volatile(status_reg) & 1) == 0 {
        // spin
    }
}
```

In Rust, `ptr::read_volatile` and `ptr::write_volatile` are the primitives. They're inherently `unsafe` because you're dereferencing a raw pointer — the compiler can't verify the address is valid.

## Building a Safe MMIO Abstraction

Raw volatile pointers everywhere is a recipe for bugs. Let's build something better:

```rust
#![no_std]

use core::marker::PhantomData;
use core::ptr;

/// Marker trait for register access modes
pub trait Access {}

/// Read-only register
pub struct ReadOnly;
impl Access for ReadOnly {}

/// Write-only register
pub struct WriteOnly;
impl Access for WriteOnly {}

/// Read-write register
pub struct ReadWrite;
impl Access for ReadWrite {}

/// A memory-mapped register of type T with access mode A
#[repr(transparent)]
pub struct Register<T: Copy, A: Access> {
    value: T,
    _access: PhantomData<A>,
}

impl<T: Copy> Register<T, ReadOnly> {
    /// Read from a read-only register
    #[inline(always)]
    pub fn read(&self) -> T {
        unsafe { ptr::read_volatile(&self.value) }
    }
}

impl<T: Copy> Register<T, WriteOnly> {
    /// Write to a write-only register
    #[inline(always)]
    pub fn write(&mut self, val: T) {
        unsafe { ptr::write_volatile(&mut self.value, val) }
    }
}

impl<T: Copy> Register<T, ReadWrite> {
    #[inline(always)]
    pub fn read(&self) -> T {
        unsafe { ptr::read_volatile(&self.value) }
    }

    #[inline(always)]
    pub fn write(&mut self, val: T) {
        unsafe { ptr::write_volatile(&mut self.value, val) }
    }

    #[inline(always)]
    pub fn modify<F: FnOnce(T) -> T>(&mut self, f: F) {
        let val = self.read();
        self.write(f(val));
    }
}
```

Now let's define a peripheral using this:

```rust
/// UART peripheral register block
#[repr(C)]
pub struct UartRegisters {
    pub data: Register<u32, ReadWrite>,     // 0x00 — Data register
    pub status: Register<u32, ReadOnly>,    // 0x04 — Status register
    pub control: Register<u32, ReadWrite>,  // 0x08 — Control register
    pub baud: Register<u32, ReadWrite>,     // 0x0C — Baud rate register
}

// Status register bit definitions
pub mod status {
    pub const TX_EMPTY: u32 = 1 << 0;
    pub const RX_READY: u32 = 1 << 1;
    pub const OVERRUN: u32 = 1 << 2;
    pub const FRAMING_ERROR: u32 = 1 << 3;
}

// Control register bit definitions
pub mod control {
    pub const TX_ENABLE: u32 = 1 << 0;
    pub const RX_ENABLE: u32 = 1 << 1;
    pub const TX_IRQ_ENABLE: u32 = 1 << 2;
    pub const RX_IRQ_ENABLE: u32 = 1 << 3;
}

impl UartRegisters {
    /// Get a reference to the UART at a specific base address
    ///
    /// # Safety
    /// Caller must ensure the base address is correct and that only
    /// one reference exists at a time.
    pub unsafe fn from_base(base: usize) -> &'static mut Self {
        &mut *(base as *mut Self)
    }

    pub fn init(&mut self, baud_rate: u32, system_clock: u32) {
        let divisor = system_clock / (16 * baud_rate);
        self.baud.write(divisor);
        self.control.write(control::TX_ENABLE | control::RX_ENABLE);
    }

    pub fn write_byte(&mut self, byte: u8) {
        // Wait until TX buffer is empty
        while (self.status.read() & status::TX_EMPTY) == 0 {
            core::hint::spin_loop();
        }
        self.data.write(byte as u32);
    }

    pub fn read_byte(&mut self) -> Option<u8> {
        if (self.status.read() & status::RX_READY) != 0 {
            Some(self.data.read() as u8)
        } else {
            None
        }
    }

    pub fn write_bytes(&mut self, data: &[u8]) {
        for &byte in data {
            self.write_byte(byte);
        }
    }
}
```

The `#[repr(C)]` attribute is essential. Without it, Rust is free to reorder struct fields. With hardware registers, the layout must exactly match the hardware — field order corresponds to register offsets from the base address.

## Bit Field Management

Hardware registers are full of multi-bit fields packed into 32-bit words. Manually shifting and masking gets old fast. Here's a cleaner approach:

```rust
#![no_std]

/// A bit field descriptor
pub struct BitField {
    pub offset: u8,
    pub width: u8,
}

impl BitField {
    pub const fn new(offset: u8, width: u8) -> Self {
        Self { offset, width }
    }

    /// Create the mask for this field
    pub const fn mask(&self) -> u32 {
        ((1u32 << self.width) - 1) << self.offset
    }

    /// Extract this field from a register value
    pub const fn extract(&self, reg: u32) -> u32 {
        (reg >> self.offset) & ((1u32 << self.width) - 1)
    }

    /// Insert a value into this field position
    pub const fn insert(&self, reg: u32, value: u32) -> u32 {
        (reg & !self.mask()) | ((value & ((1u32 << self.width) - 1)) << self.offset)
    }
}

// GPIO Mode Register fields (2 bits per pin)
pub mod gpio_moder {
    use super::BitField;

    pub const PIN0: BitField = BitField::new(0, 2);
    pub const PIN1: BitField = BitField::new(2, 2);
    pub const PIN13: BitField = BitField::new(26, 2);
    // ... etc

    pub const INPUT: u32 = 0b00;
    pub const OUTPUT: u32 = 0b01;
    pub const ALTERNATE: u32 = 0b10;
    pub const ANALOG: u32 = 0b11;
}

// Usage:
fn configure_pin(moder: &mut Register<u32, ReadWrite>) {
    moder.modify(|val| {
        gpio_moder::PIN13.insert(val, gpio_moder::OUTPUT)
    });
}
```

This is still zero-cost — everything resolves to constant shifts and masks at compile time. But the code reads like documentation: "set PIN13 to OUTPUT mode."

## DMA — When the CPU Shouldn't Be Involved

Direct Memory Access is where MMIO gets really interesting. You configure DMA registers to tell a DMA controller to move data between peripherals and memory *without CPU involvement*:

```rust
#[repr(C)]
pub struct DmaStream {
    pub cr: Register<u32, ReadWrite>,      // Configuration
    pub ndtr: Register<u32, ReadWrite>,    // Number of data items
    pub par: Register<u32, ReadWrite>,     // Peripheral address
    pub m0ar: Register<u32, ReadWrite>,    // Memory 0 address
    pub m1ar: Register<u32, ReadWrite>,    // Memory 1 address (double-buffer)
    pub fcr: Register<u32, ReadWrite>,     // FIFO control
}

pub mod dma_cr {
    pub const ENABLE: u32 = 1 << 0;
    pub const TRANSFER_COMPLETE_IRQ: u32 = 1 << 4;
    pub const DIR_MEM_TO_PERIPH: u32 = 1 << 6;
    pub const DIR_PERIPH_TO_MEM: u32 = 0;
    pub const CIRCULAR_MODE: u32 = 1 << 8;
    pub const MINC: u32 = 1 << 10; // Memory address increment
}

/// Set up a DMA transfer from UART RX to a memory buffer
///
/// # Safety
/// - `buffer` must remain valid for the entire DMA transfer
/// - No other code may access `buffer` while DMA is active
/// - The DMA stream must not already be in use
unsafe fn setup_uart_rx_dma(
    stream: &mut DmaStream,
    uart_data_reg: usize,
    buffer: &mut [u8],
) {
    // Disable stream first
    stream.cr.modify(|val| val & !dma_cr::ENABLE);

    // Wait for stream to be disabled
    while (stream.cr.read() & dma_cr::ENABLE) != 0 {
        core::hint::spin_loop();
    }

    // Set peripheral address (UART data register)
    stream.par.write(uart_data_reg as u32);

    // Set memory address (our buffer)
    stream.m0ar.write(buffer.as_mut_ptr() as u32);

    // Set number of data items
    stream.ndtr.write(buffer.len() as u32);

    // Configure: peripheral-to-memory, memory increment, circular
    stream.cr.write(
        dma_cr::DIR_PERIPH_TO_MEM
        | dma_cr::MINC
        | dma_cr::CIRCULAR_MODE
        | dma_cr::TRANSFER_COMPLETE_IRQ
    );

    // Enable
    stream.cr.modify(|val| val | dma_cr::ENABLE);
}
```

Notice the safety comments. DMA is inherently dangerous — the hardware is writing to memory outside the CPU's control. If the buffer gets dropped or moved while DMA is active, you get silent memory corruption. Rust can't prevent this with lifetimes alone (the DMA controller doesn't understand Rust lifetimes), so we document the invariants and use `unsafe`.

## Memory Barriers and Ordering

On ARM and other architectures with weak memory ordering, you need barriers around MMIO access in certain situations:

```rust
use core::sync::atomic::{compiler_fence, Ordering};

fn acknowledge_interrupt_and_process(
    status: &Register<u32, ReadWrite>,
    data: &Register<u32, ReadOnly>,
) -> u32 {
    // Read the data first
    let value = data.read();

    // Ensure the read completes before we clear the interrupt
    compiler_fence(Ordering::SeqCst);

    // Clear interrupt flag
    status.write(0x01);

    // Ensure the write completes before we return
    // (on ARM, use a DSB instruction)
    cortex_m::asm::dsb();

    value
}
```

`compiler_fence` prevents the *compiler* from reordering operations. `dsb` (Data Synchronization Barrier) prevents the *CPU* from reordering them. You need both in different situations:

- **compiler_fence**: When the CPU has strong ordering (x86) or when you're preventing the compiler from being clever
- **dsb/dmb/isb**: On ARM, when you need the hardware to see writes in a specific order

I've seen bugs where an interrupt clear was reordered *before* the data read, causing the interrupt to fire again immediately with stale data. These bugs are intermittent and absolutely maddening to debug.

## Type-Safe Register Access with Macros

Let's build a macro that generates type-safe register definitions:

```rust
macro_rules! register_block {
    (
        $name:ident @ $base:expr {
            $(
                $offset:expr => $field:ident : $access:ident<$ty:ty>
            ),* $(,)?
        }
    ) => {
        pub struct $name;

        impl $name {
            $(
                #[allow(non_upper_case_globals)]
                pub const $field: *mut $ty = ($base + $offset) as *mut $ty;
            )*
        }

        impl $name {
            $(
                paste::paste! {
                    #[inline(always)]
                    pub fn [<read_ $field>]() -> $ty {
                        unsafe { core::ptr::read_volatile(Self::$field) }
                    }
                }
            )*
        }
    };
}

// Usage:
register_block! {
    Gpio @ 0x4002_0800 {
        0x00 => moder: ReadWrite<u32>,
        0x04 => otyper: ReadWrite<u32>,
        0x08 => ospeedr: ReadWrite<u32>,
        0x0C => pupdr: ReadWrite<u32>,
        0x10 => idr: ReadOnly<u32>,
        0x14 => odr: ReadWrite<u32>,
        0x18 => bsrr: WriteOnly<u32>,
    }
}
```

In practice, you'd use something like the `tock-registers` crate or `volatile-register` crate rather than rolling your own. But understanding the mechanics matters — you'll be debugging at this level when things go wrong.

## Real-World Pattern: A Complete SPI Driver

Let's put it all together with a simplified SPI (Serial Peripheral Interface) driver:

```rust
#![no_std]

use core::ptr;

const SPI1_BASE: usize = 0x4001_3000;

#[repr(C)]
struct SpiRegisters {
    cr1: u32,      // 0x00 — Control register 1
    cr2: u32,      // 0x04 — Control register 2
    sr: u32,       // 0x08 — Status register
    dr: u32,       // 0x0C — Data register
    crcpr: u32,    // 0x10 — CRC polynomial
    rxcrcr: u32,   // 0x14 — RX CRC
    txcrcr: u32,   // 0x18 — TX CRC
}

// Status register bits
const SR_RXNE: u32 = 1 << 0;  // RX buffer not empty
const SR_TXE: u32 = 1 << 1;   // TX buffer empty
const SR_BSY: u32 = 1 << 7;   // Busy flag

// CR1 bits
const CR1_SPE: u32 = 1 << 6;  // SPI enable
const CR1_MSTR: u32 = 1 << 2; // Master mode
const CR1_BR_DIV8: u32 = 0b010 << 3; // Baud rate = fPCLK/8

pub struct Spi {
    regs: *mut SpiRegisters,
}

impl Spi {
    /// # Safety
    /// Must only be called once per SPI peripheral
    pub unsafe fn new(base: usize) -> Self {
        let regs = base as *mut SpiRegisters;

        // Configure: master mode, clock/8, SPI enable
        ptr::write_volatile(
            &mut (*regs).cr1,
            CR1_MSTR | CR1_BR_DIV8 | CR1_SPE,
        );

        Self { regs }
    }

    pub fn transfer_byte(&mut self, tx: u8) -> u8 {
        unsafe {
            let regs = &mut *self.regs;

            // Wait for TX buffer empty
            while ptr::read_volatile(&regs.sr) & SR_TXE == 0 {
                core::hint::spin_loop();
            }

            // Write data (starts clock)
            ptr::write_volatile(&mut regs.dr, tx as u32);

            // Wait for RX buffer not empty (transfer complete)
            while ptr::read_volatile(&regs.sr) & SR_RXNE == 0 {
                core::hint::spin_loop();
            }

            // Read received data
            ptr::read_volatile(&regs.dr) as u8
        }
    }

    pub fn transfer(&mut self, tx: &[u8], rx: &mut [u8]) {
        assert_eq!(tx.len(), rx.len(), "SPI transfer buffers must be same length");
        for i in 0..tx.len() {
            rx[i] = self.transfer_byte(tx[i]);
        }
    }

    pub fn wait_idle(&self) {
        unsafe {
            while ptr::read_volatile(&(*self.regs).sr) & SR_BSY != 0 {
                core::hint::spin_loop();
            }
        }
    }
}
```

This is a simplified but functional SPI driver. A production version would handle:
- Configurable data frame size (8-bit vs 16-bit)
- DMA integration for bulk transfers
- Error handling (overrun, mode fault)
- Chip select management
- Different clock polarities and phases

## The Compiler Is Not Your Enemy (Usually)

I want to emphasize something: `volatile` isn't about the compiler being hostile. The optimizer is doing its job brilliantly — for normal memory. Hardware registers just don't follow normal memory semantics:

- Reading a register can have side effects (clearing flags, advancing FIFOs)
- Writing the same value twice might matter (each write triggers an action)
- The value can change between reads without the CPU writing to it (hardware updates)
- Write-only registers exist (reading gives undefined results)

`volatile` is how we communicate these semantics to the compiler. It's not a workaround — it's the correct tool.

## What's Next

We've covered how to talk to hardware from Rust. Next, we're going to take this to the most hardcore environment possible — the Linux kernel. Writing kernel modules in Rust is now officially supported, and it changes everything about how we think about systems code.
