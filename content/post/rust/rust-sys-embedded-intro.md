---
title: "Lesson 2: Embedded Rust — Microcontrollers and bare metal"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 2
keywords: [Rust, rust, systems programming, embedded, microcontroller, ARM, Cortex-M, bare metal]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-07-12T14:37:51.000Z'
---

I bricked my first development board within forty-five minutes of getting it out of the box. Wrote some C code, forgot to configure the clock properly, flashed it, and the thing just... stopped responding. No debugger connection. No serial output. A $15 paperweight.

That experience — the raw, unforgiving nature of hardware programming — is exactly why Rust matters in embedded. Not because it prevents you from writing to the wrong register (it can't, really), but because it gives you tools to *structure* hardware access so mistakes become harder to make.

## The Embedded Rust Ecosystem

Embedded Rust isn't some experimental side project anymore. There's a proper ecosystem with layers:

```
┌─────────────────────────────────────────┐
│  Your Application                        │
├─────────────────────────────────────────┤
│  HAL Crate (e.g., stm32f4xx-hal)       │  ← Safe hardware API
├─────────────────────────────────────────┤
│  PAC (e.g., stm32f4)                    │  ← Raw register access
├─────────────────────────────────────────┤
│  cortex-m / cortex-m-rt                 │  ← CPU support + runtime
├─────────────────────────────────────────┤
│  Bare Metal (your #![no_std] binary)    │
└─────────────────────────────────────────┘
```

**PAC (Peripheral Access Crate):** Auto-generated from SVD files (XML descriptions of chip registers). Gives you raw, typed access to every register on the chip. Still unsafe, but at least the register addresses and bit fields are correct by construction.

**HAL (Hardware Abstraction Layer):** Builds safe APIs on top of the PAC. Instead of writing bits to three different registers to configure a GPIO pin, you call `pin.into_push_pull_output()`. Type-state programming ensures you can't use a pin as output before configuring it that way.

**Board Support Packages:** Optional crate that ties together the HAL with board-specific details like which pin is connected to which LED.

## Setting Up a Real Project

Let's target an STM32F4 — one of the most common ARM Cortex-M4 chips. If you don't have hardware, that's fine — we'll discuss emulation too.

```bash
# Install the target
rustup target add thumbv7em-none-eabihf

# Install cargo-binutils for inspecting binaries
cargo install cargo-binutils
rustup component add llvm-tools-preview

# Install probe-rs for flashing and debugging (replaces openocd + gdb)
cargo install probe-rs-tools
```

Create the project:

```bash
cargo init --name blinky
```

`Cargo.toml`:

```toml
[package]
name = "blinky"
version = "0.1.0"
edition = "2021"

[dependencies]
cortex-m = "0.7"
cortex-m-rt = "0.7"
panic-halt = "0.2"
stm32f4xx-hal = { version = "0.21", features = ["stm32f411"] }

[profile.release]
opt-level = "s"     # Optimize for size
lto = true          # Link-time optimization
codegen-units = 1   # Better optimization, slower compile
debug = true        # Keep debug info for probe-rs
```

`.cargo/config.toml`:

```toml
[target.thumbv7em-none-eabihf]
runner = "probe-rs run --chip STM32F411CEUx"
rustflags = ["-C", "link-arg=-Tlink.x"]

[build]
target = "thumbv7em-none-eabihf"
```

`memory.x` — This is critical. It tells the linker where your chip's memory lives:

```
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 512K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
}
```

## The Classic Blinky

Every embedded journey starts here. Let's blink an LED:

```rust
#![no_std]
#![no_main]

use cortex_m_rt::entry;
use panic_halt as _;
use stm32f4xx_hal::{
    pac,
    prelude::*,
};

#[entry]
fn main() -> ! {
    // Take ownership of the device peripherals — this can only be called once
    let dp = pac::Peripherals::take().unwrap();
    let cp = cortex_m::Peripherals::take().unwrap();

    // Configure the clock system
    let rcc = dp.RCC.constrain();
    let clocks = rcc.cfgr
        .sysclk(84.MHz())
        .freeze();

    // Configure GPIO pin C13 as push-pull output (common LED pin)
    let gpioc = dp.GPIOC.split();
    let mut led = gpioc.pc13.into_push_pull_output();

    // Set up a delay provider using the system timer
    let mut delay = cp.SYST.delay(&clocks);

    // Blink forever
    loop {
        led.set_high();
        delay.delay_ms(500u32);
        led.set_low();
        delay.delay_ms(500u32);
    }
}
```

A few things to notice here:

**`pac::Peripherals::take()`** returns `Option<Peripherals>` and only succeeds once. After that, it returns `None`. This is singleton enforcement at the type level — you can't accidentally create two references to the same hardware register bank.

**`into_push_pull_output()`** is a type-state transition. The return type is different from the input type. You literally *cannot* call `set_high()` on a pin that hasn't been configured as an output — it won't compile. This is Rust's type system preventing hardware misconfiguration at compile time.

**The `-> !` return type** on `main()`. Embedded programs don't return. There's nowhere to return to. The `loop` at the end enforces this.

## Going Lower: Direct Register Access

The HAL is convenient, but sometimes you need to hit registers directly. Here's what blinky looks like using just the PAC:

```rust
#![no_std]
#![no_main]

use cortex_m_rt::entry;
use panic_halt as _;
use stm32f4xx_hal::pac;

#[entry]
fn main() -> ! {
    let dp = pac::Peripherals::take().unwrap();

    // Enable GPIOC clock (RCC_AHB1ENR register, bit 2)
    dp.RCC.ahb1enr.modify(|_, w| w.gpiocen().enabled());

    // Configure PC13 as output (MODER register)
    dp.GPIOC.moder.modify(|_, w| w.moder13().output());

    // Set output type to push-pull (OTYPER register)
    dp.GPIOC.otyper.modify(|_, w| w.ot13().push_pull());

    // Set speed to low (OSPEEDR register)
    dp.GPIOC.ospeedr.modify(|_, w| w.ospeedr13().low_speed());

    loop {
        // Set PC13 high (BSRR register — write-only, atomic)
        dp.GPIOC.bsrr.write(|w| w.bs13().set());
        cortex_m::asm::delay(8_000_000);

        // Set PC13 low (BSRR register, reset bits)
        dp.GPIOC.bsrr.write(|w| w.br13().reset());
        cortex_m::asm::delay(8_000_000);
    }
}
```

This is substantially more code, but you can see exactly what's happening. Every register access is typed — `modify` gives you a read-modify-write, `write` gives you a write-only access. The closures provide builder-style access to individual bit fields. You can't accidentally write to a bit that doesn't exist.

## And Even Lower: Raw Pointers

If the PAC doesn't cover your chip (rare but it happens), you go fully manual:

```rust
#![no_std]
#![no_main]

use core::panic::PanicInfo;
use core::ptr;

// STM32F411 register addresses
const RCC_BASE: usize = 0x4002_3800;
const RCC_AHB1ENR: *mut u32 = (RCC_BASE + 0x30) as *mut u32;

const GPIOC_BASE: usize = 0x4002_0800;
const GPIOC_MODER: *mut u32 = (GPIOC_BASE + 0x00) as *mut u32;
const GPIOC_BSRR: *mut u32 = (GPIOC_BASE + 0x18) as *mut u32;

#[panic_handler]
fn panic(_: &PanicInfo) -> ! {
    loop {}
}

#[no_mangle]
pub extern "C" fn _start() -> ! {
    unsafe {
        // Enable GPIOC clock
        let val = ptr::read_volatile(RCC_AHB1ENR);
        ptr::write_volatile(RCC_AHB1ENR, val | (1 << 2));

        // Set PC13 as output (bits 27:26 = 01)
        let val = ptr::read_volatile(GPIOC_MODER);
        ptr::write_volatile(GPIOC_MODER, (val & !(3 << 26)) | (1 << 26));

        loop {
            // Set PC13
            ptr::write_volatile(GPIOC_BSRR, 1 << 13);
            for _ in 0..800_000 {
                core::hint::black_box(());
            }
            // Reset PC13
            ptr::write_volatile(GPIOC_BSRR, 1 << (13 + 16));
            for _ in 0..800_000 {
                core::hint::black_box(());
            }
        }
    }
}
```

`ptr::read_volatile` and `ptr::write_volatile` are critical here. Without `volatile`, the optimizer might remove or reorder your register accesses. The hardware doesn't care about your optimizer's opinion — a write to a register is a side effect that *must* happen, in order.

## Interrupts — Where It Gets Interesting

Real embedded code is interrupt-driven. You don't poll in a loop; hardware tells you when something happens:

```rust
#![no_std]
#![no_main]

use core::cell::RefCell;
use cortex_m::interrupt::Mutex;
use cortex_m_rt::entry;
use panic_halt as _;
use stm32f4xx_hal::{
    gpio::{Edge, Input, PC13, PinState},
    interrupt,
    pac::{self, EXTI},
    prelude::*,
};

// Shared resources between main and interrupt handler
static BUTTON: Mutex<RefCell<Option<PC13<Input>>>> = Mutex::new(RefCell::new(None));
static EXTI_PERIPHERAL: Mutex<RefCell<Option<EXTI>>> = Mutex::new(RefCell::new(None));
static PRESS_COUNT: Mutex<RefCell<u32>> = Mutex::new(RefCell::new(0));

#[entry]
fn main() -> ! {
    let mut dp = pac::Peripherals::take().unwrap();

    let rcc = dp.RCC.constrain();
    let _clocks = rcc.cfgr.sysclk(84.MHz()).freeze();

    let gpioc = dp.GPIOC.split();

    // Configure PC13 as input with interrupt on falling edge
    let mut button = gpioc.pc13.into_pull_up_input();
    let mut syscfg = dp.SYSCFG.constrain();
    button.make_interrupt_source(&mut syscfg);
    button.trigger_on_edge(&mut dp.EXTI, Edge::Falling);
    button.enable_interrupt(&mut dp.EXTI);

    // Move resources into global statics (interrupt handler needs them)
    cortex_m::interrupt::free(|cs| {
        BUTTON.borrow(cs).replace(Some(button));
        EXTI_PERIPHERAL.borrow(cs).replace(Some(dp.EXTI));
    });

    // Enable the interrupt in the NVIC
    unsafe {
        cortex_m::peripheral::NVIC::unmask(pac::Interrupt::EXTI15_10);
    }

    loop {
        cortex_m::asm::wfi(); // Wait for interrupt — saves power
    }
}

#[interrupt]
fn EXTI15_10() {
    cortex_m::interrupt::free(|cs| {
        if let Some(ref mut button) = *BUTTON.borrow(cs).borrow_mut() {
            if button.is_low() {
                let mut count = PRESS_COUNT.borrow(cs).borrow_mut();
                *count += 1;
            }
            button.clear_interrupt_pending_bit();
        }
    });
}
```

This looks verbose, and honestly, it is. The `Mutex<RefCell<Option<...>>>` pattern is the standard way to share state between `main` and interrupt handlers in embedded Rust. It's ugly, but it's *correct*:

- `Mutex` (from `cortex_m`, not `std`) disables interrupts while you hold the lock
- `RefCell` gives you runtime borrow checking
- `Option` lets you initialize the resource in `main` and then move it to the global

The RTIC framework (Real-Time Interrupt-driven Concurrency) cleans this up dramatically — but understanding the manual approach first matters.

## Debugging Embedded Rust

`probe-rs` has transformed the debugging experience. You get:

```bash
# Flash and run with RTT (Real-Time Transfer) logging
cargo run --release

# Or use defmt for efficient logging
# In Cargo.toml: defmt = "0.3", defmt-rtt = "0.4", panic-probe = "0.3"
```

With `defmt`, you get structured logging that compiles down to almost nothing:

```rust
use defmt::info;
use defmt_rtt as _;
use panic_probe as _;

#[entry]
fn main() -> ! {
    info!("Boot complete, clock: {} MHz", 84);
    info!("Starting sensor loop");

    let mut reading: u16 = 0;
    loop {
        reading = read_sensor();
        defmt::debug!("Sensor: {}", reading);
        if reading > 1000 {
            defmt::warn!("Threshold exceeded: {}", reading);
        }
    }
}
```

`defmt` encodes log messages as indexes into a table — the actual strings stay on your host machine. So `info!("Boot complete, clock: {} MHz", 84)` might only transmit 3 bytes over the wire. On a chip with 2KB of RAM, that matters.

## Binary Size — Because Every Byte Counts

Check what you're producing:

```bash
cargo size --release -- -A

# Typical output:
# section              size      addr
# .vector_table        1024  0x8000000
# .text                2848  0x8000400
# .rodata               128  0x8000b20
# .data                   8  0x20000000
# .bss                   16  0x20000008
# Total                4024
```

4KB for a working blinky with interrupts. In C, you'd get roughly the same. Rust's zero-cost abstractions aren't marketing — they're measurable.

If your binary is too big, common culprits:

```toml
[profile.release]
opt-level = "z"       # Optimize aggressively for size
lto = true
codegen-units = 1
strip = true          # Strip debug symbols from binary
panic = "abort"       # Don't include unwinding code
```

Also check for formatting code — `core::fmt` is surprisingly large. Each `write!` invocation can add several KB. In extreme cases, use manual byte-level output instead.

## Testing Without Hardware

You don't need a physical board to develop embedded Rust:

```bash
# QEMU can emulate various ARM boards
cargo install cargo-embed

# Run tests on the host
cargo test --target x86_64-unknown-linux-gnu
```

Structure your code so the hardware-dependent layer is thin:

```rust
// This module is pure logic — testable on any platform
#[cfg_attr(test, derive(Debug, PartialEq))]
pub struct PidController {
    kp: f32,
    ki: f32,
    kd: f32,
    integral: f32,
    prev_error: f32,
}

impl PidController {
    pub fn new(kp: f32, ki: f32, kd: f32) -> Self {
        Self { kp, ki, kd, integral: 0.0, prev_error: 0.0 }
    }

    pub fn update(&mut self, setpoint: f32, measured: f32, dt: f32) -> f32 {
        let error = setpoint - measured;
        self.integral += error * dt;
        let derivative = (error - self.prev_error) / dt;
        self.prev_error = error;

        self.kp * error + self.ki * self.integral + self.kd * derivative
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pid_converges_to_setpoint() {
        let mut pid = PidController::new(1.0, 0.1, 0.01);
        let mut value = 0.0;
        for _ in 0..1000 {
            let output = pid.update(100.0, value, 0.01);
            value += output * 0.01;
        }
        assert!((value - 100.0).abs() < 1.0);
    }
}
```

Keep your business logic in pure Rust. Push hardware interaction to the edges. This isn't just good embedded practice — it's good software engineering.

## Where Embedded Rust Stands Today

I'll be honest — embedded Rust has rough edges. The ecosystem is still maturing. HAL crate quality varies by chip family. Some vendors have excellent support (Nordic, STM32), others are spotty. You'll occasionally hit a missing peripheral driver and have to write PAC-level code.

But the trajectory is clear. The type-state patterns for pin configuration catch real bugs. The ownership model prevents the shared-mutable-state nightmares that plague C firmware. And the tooling with `probe-rs` and `defmt` is genuinely better than the traditional OpenOCD + GDB + printf debugging workflow.

Next lesson, we're going deeper into hardware communication — memory-mapped I/O, the fundamental mechanism that makes all of this work.
