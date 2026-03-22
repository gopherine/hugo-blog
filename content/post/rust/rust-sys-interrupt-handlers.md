---
title: "Lesson 9: Interrupt Handlers and Real-Time Constraints — When timing matters"
author: Atharva Pandey
series: "Rust for Systems Programming"
lesson: 9
keywords: [Rust, rust, systems programming, interrupts, real-time, RTIC, ISR, embedded, timing]
tags: [Rust tutorial, rust, systems, low-level]
date: '2025-07-30T06:55:22.000Z'
---

I once spent three days debugging a motor controller that would randomly twitch. The code was correct. The hardware was fine. The interrupt handler was well-written. But every few thousand cycles, a timer interrupt would preempt the motor control interrupt at exactly the wrong moment, corrupting a shared variable. The fix was two lines of code — disable interrupts around the critical section — but finding it cost me a weekend.

Interrupts are where "works on my desk" meets "fails at 3 AM in production." The timing-dependent bugs you get from interrupt handlers are some of the nastiest in all of software. And Rust — surprisingly — has some real tools to help.

## What Are Interrupts, Actually?

An interrupt is the hardware saying: "Stop whatever you're doing and handle this. Now."

```
Normal execution:        With interrupt:

main() ──────────►       main() ────┐
                                    │ INTERRUPT!
                         ISR() ─────┤
                                    │ Done, resume
                         main() ◄───┘
```

The CPU literally stops executing your current instruction stream, saves some state, jumps to an interrupt service routine (ISR), and when the ISR returns, resumes where it left off. This happens *transparently* — the interrupted code doesn't know it was paused.

Types of interrupts:
- **Hardware interrupts**: External devices (GPIO pin change, timer tick, UART byte received, DMA complete)
- **Software interrupts**: Triggered by instructions (`SVC` on ARM, `INT` on x86) — used for syscalls
- **Exceptions**: CPU errors (divide by zero, page fault, invalid instruction)

## The Interrupt Priority Problem

On ARM Cortex-M processors, interrupts have configurable priorities. A higher-priority interrupt can preempt a lower-priority one:

```
Priority 0 (highest): Safety shutdown
Priority 1:           Motor control (runs every 100μs)
Priority 2:           Sensor reading (runs every 1ms)
Priority 3 (lowest):  LED status update (runs every 100ms)
```

This creates a hierarchy of preemption — and shared state between different priority levels is *the* source of bugs.

## Manual Interrupt Management

The bare-metal approach, without any framework:

```rust
#![no_std]
#![no_main]

use core::cell::UnsafeCell;
use core::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use cortex_m::interrupt;
use cortex_m_rt::{entry, exception};
use panic_halt as _;

// Shared state between main and interrupt handler
static SENSOR_VALUE: AtomicU32 = AtomicU32::new(0);
static DATA_READY: AtomicBool = AtomicBool::new(false);

// For non-atomic types, we need a critical section
struct CriticalSectionMutex<T> {
    data: UnsafeCell<T>,
}

unsafe impl<T: Send> Sync for CriticalSectionMutex<T> {}

impl<T> CriticalSectionMutex<T> {
    const fn new(val: T) -> Self {
        Self { data: UnsafeCell::new(val) }
    }

    fn lock<R>(&self, f: impl FnOnce(&mut T) -> R) -> R {
        interrupt::free(|_cs| {
            let data = unsafe { &mut *self.data.get() };
            f(data)
        })
    }
}

struct MotorState {
    position: i32,
    velocity: i32,
    target: i32,
}

static MOTOR: CriticalSectionMutex<MotorState> = CriticalSectionMutex::new(MotorState {
    position: 0,
    velocity: 0,
    target: 0,
});

#[entry]
fn main() -> ! {
    // Hardware initialization...
    // Enable timer interrupt, ADC interrupt, etc.

    loop {
        // Wait for new sensor data
        if DATA_READY.swap(false, Ordering::Acquire) {
            let value = SENSOR_VALUE.load(Ordering::Relaxed);

            // Update motor target based on sensor
            MOTOR.lock(|state| {
                state.target = value as i32;
            });
        }

        cortex_m::asm::wfi(); // Sleep until next interrupt
    }
}

// Timer interrupt — runs every 100μs for motor control
#[exception]
fn SysTick() {
    MOTOR.lock(|state| {
        // PID control loop
        let error = state.target - state.position;
        state.velocity += error / 10;
        state.position += state.velocity;

        // Write to motor driver hardware register
        unsafe {
            core::ptr::write_volatile(
                0x4000_1000 as *mut u32,
                state.velocity as u32,
            );
        }
    });
}

// ADC conversion complete interrupt
// #[interrupt]
// fn ADC() {
//     let raw_value = unsafe {
//         core::ptr::read_volatile(0x4001_2000 as *const u32)
//     };
//     SENSOR_VALUE.store(raw_value, Ordering::Release);
//     DATA_READY.store(true, Ordering::Release);
// }
```

The `interrupt::free` call disables *all* interrupts while the closure runs. This is the nuclear option — it guarantees mutual exclusion, but it also means you can miss interrupts if the critical section takes too long.

## RTIC — The Right Way to Do Interrupts in Rust

RTIC (Real-Time Interrupt-driven Concurrency) is a framework that uses Rust's type system to *statically* verify interrupt safety. No runtime overhead. No locks. Just correct-by-construction code.

```rust
#![no_std]
#![no_main]

use panic_halt as _;
use rtic::app;
use stm32f4xx_hal::{
    gpio::{Output, PushPull, PA5},
    pac,
    prelude::*,
    timer::{CounterHz, Event},
};

#[app(device = stm32f4xx_hal::pac, peripherals = true, dispatchers = [USART1])]
mod app {
    use super::*;

    // Shared resources — RTIC manages access automatically
    #[shared]
    struct Shared {
        motor_target: i32,
        error_count: u32,
    }

    // Local resources — owned by a single task, no sharing needed
    #[local]
    struct Local {
        led: PA5<Output<PushPull>>,
        timer: CounterHz<pac::TIM2>,
        motor_position: i32,
        motor_velocity: i32,
    }

    #[init]
    fn init(ctx: init::Context) -> (Shared, Local) {
        let dp = ctx.device;
        let rcc = dp.RCC.constrain();
        let clocks = rcc.cfgr.sysclk(84.MHz()).freeze();

        let gpioa = dp.GPIOA.split();
        let led = gpioa.pa5.into_push_pull_output();

        // Configure timer for 10kHz (100μs period)
        let mut timer = dp.TIM2.counter_hz(&clocks);
        timer.start(10.kHz()).unwrap();
        timer.listen(Event::Update);

        (
            Shared {
                motor_target: 0,
                error_count: 0,
            },
            Local {
                led,
                timer,
                motor_position: 0,
                motor_velocity: 0,
            },
        )
    }

    // Idle task — runs when no interrupt is active
    #[idle]
    fn idle(_ctx: idle::Context) -> ! {
        loop {
            cortex_m::asm::wfi();
        }
    }

    // Timer interrupt — highest priority motor control loop
    #[task(
        binds = TIM2,
        priority = 3,
        shared = [motor_target, error_count],
        local = [timer, motor_position, motor_velocity]
    )]
    fn motor_control(mut ctx: motor_control::Context) {
        ctx.local.timer.clear_all_flags();

        // Access shared resource — RTIC generates the minimal
        // critical section needed (might just be priority ceiling,
        // not full interrupt disable)
        let target = ctx.shared.motor_target.lock(|t| *t);

        let error = target - *ctx.local.motor_position;
        *ctx.local.motor_velocity += error / 10;
        *ctx.local.motor_position += *ctx.local.motor_velocity;

        if error.abs() > 1000 {
            ctx.shared.error_count.lock(|count| {
                *count += 1;
            });
        }

        // Write to motor hardware...
    }

    // Lower priority task — sensor processing
    #[task(
        binds = ADC,
        priority = 2,
        shared = [motor_target]
    )]
    fn sensor_update(mut ctx: sensor_update::Context) {
        let raw_value: i32 = 42; // Read from ADC register

        // This lock might get preempted by motor_control (higher priority)
        // RTIC handles this correctly via priority ceiling protocol
        ctx.shared.motor_target.lock(|target| {
            *target = raw_value;
        });
    }

    // Software task — spawned, not bound to hardware interrupt
    #[task(
        priority = 1,
        shared = [error_count],
        local = [led]
    )]
    async fn status_led(mut ctx: status_led::Context) {
        loop {
            let errors = ctx.shared.error_count.lock(|c| *c);

            if errors > 0 {
                ctx.local.led.toggle();
            }

            // Wait 500ms (using RTIC's async timing)
            // Systick::delay(500.millis()).await;
        }
    }
}
```

What makes RTIC special:

**Priority Ceiling Protocol (PCP).** Instead of disabling all interrupts when locking a resource, RTIC temporarily raises the current priority to the ceiling (the highest priority of any task that accesses that resource). This blocks only the specific tasks that could conflict, not everything.

**Static analysis.** RTIC figures out the ceiling priorities at compile time. Zero runtime overhead for locking.

**No deadlocks.** The PCP protocol is mathematically proven deadlock-free for single-core systems.

**Bounded latency.** You can calculate worst-case interrupt latency — it's the sum of all critical sections at higher priority. No unbounded waiting.

## Real-Time Constraints

Real-time doesn't mean "fast" — it means "predictable." A real-time system must respond to events within a guaranteed deadline.

**Hard real-time**: Missing a deadline is a system failure. Motor control, flight control, medical devices.

**Soft real-time**: Missing a deadline degrades quality but isn't catastrophic. Audio playback, video streaming.

```rust
/// Calculate worst-case execution time (WCET) for a function
/// This is a simplified analysis — real WCET analysis uses tools like OTAWA or aiT

/// Motor control loop — all branches must complete within 100μs
fn motor_control_loop(
    position: &mut i32,
    velocity: &mut i32,
    target: i32,
) -> i32 {
    // Simple PID — deterministic execution time
    // No loops with variable iteration count
    // No dynamic allocation
    // No recursion
    // No floating point (on MCUs without FPU)

    let error = target - *position;
    let p_term = error * KP;

    // Use saturating arithmetic — no panics from overflow
    let d_term = (*velocity).saturating_mul(KD);
    let output = p_term.saturating_add(d_term);

    // Clamp output — bounded execution, no branching surprises
    let clamped = output.clamp(-MAX_OUTPUT, MAX_OUTPUT);

    *velocity = error;
    *position = position.saturating_add(clamped);

    clamped
}

const KP: i32 = 100;
const KD: i32 = 10;
const MAX_OUTPUT: i32 = 1000;
```

Rules for real-time interrupt handlers:

1. **No heap allocation.** `malloc` has unbounded worst-case time.
2. **No unbounded loops.** Every loop must have a known maximum iteration count.
3. **No blocking I/O.** Use DMA or polling with timeouts.
4. **No recursion.** Stack usage must be statically deterministic.
5. **No panics.** Use saturating arithmetic, check bounds explicitly.
6. **Minimize critical sections.** Every cycle you spend with interrupts disabled is a cycle you might miss an interrupt.

## Interrupt Latency Analysis

Understanding your timing budget:

```
Interrupt occurs
    │
    ├── Hardware latency: ~12 cycles (ARM Cortex-M)
    │   (Save registers, fetch vector, pipeline flush)
    │
    ├── Higher-priority ISR preemption: 0-N cycles
    │   (If a higher-priority interrupt is running)
    │
    ├── Critical section blocking: 0-M cycles
    │   (If main code holds a lock)
    │
    ├── YOUR ISR code
    │
    └── Return from interrupt: ~12 cycles
```

On a 100MHz Cortex-M4:
- 12 cycles = 120ns hardware latency
- Typical ISR: 50-500 cycles = 0.5-5μs
- Total: well under 10μs for simple handlers

```rust
/// Measure interrupt latency using a hardware timer
static ISR_ENTRY_TIME: AtomicU32 = AtomicU32::new(0);
static ISR_EXIT_TIME: AtomicU32 = AtomicU32::new(0);

fn measure_latency() {
    // Configure a free-running timer (e.g., DWT cycle counter on Cortex-M)
    // Trigger interrupt via software
    // In ISR: record entry time
    // After ISR: record exit time
    // Latency = entry_time - trigger_time

    let trigger_time = cortex_m::peripheral::DWT::cycle_count();

    // Trigger software interrupt
    cortex_m::peripheral::NVIC::pend(pac::Interrupt::EXTI0);

    // ISR runs and records its time...
    cortex_m::asm::dsb();

    let entry = ISR_ENTRY_TIME.load(Ordering::SeqCst);
    let exit = ISR_EXIT_TIME.load(Ordering::SeqCst);

    let latency_cycles = entry.wrapping_sub(trigger_time);
    let execution_cycles = exit.wrapping_sub(entry);

    // At 100MHz: 1 cycle = 10ns
    // defmt::info!("Latency: {} cycles ({}ns)", latency_cycles, latency_cycles * 10);
    // defmt::info!("Execution: {} cycles ({}ns)", execution_cycles, execution_cycles * 10);
}
```

## DMA — Offloading Work from Interrupts

The best interrupt handler is one that does almost nothing. DMA (Direct Memory Access) lets you move data between peripherals and memory without CPU involvement:

```rust
/// Pattern: DMA + interrupt for efficient data transfer
///
/// Instead of:
///   UART interrupt fires for EVERY byte → ISR reads byte → stores in buffer
///
/// Use:
///   DMA transfers N bytes automatically → interrupt fires ONCE when done

struct DmaUartReceiver {
    buffer: &'static mut [u8; 256],
    half_complete: bool,
}

impl DmaUartReceiver {
    /// DMA transfer complete interrupt
    /// Fires once per 256 bytes instead of 256 times
    fn on_transfer_complete(&mut self) {
        // Process the second half of the buffer
        let data = &self.buffer[128..256];
        process_received_data(data);
    }

    /// DMA half-transfer complete interrupt
    /// Enables double-buffering: process first half while DMA fills second half
    fn on_half_transfer(&mut self) {
        let data = &self.buffer[0..128];
        process_received_data(data);
    }
}

fn process_received_data(data: &[u8]) {
    // Parse protocol, update state, etc.
    // This runs in interrupt context — keep it fast
}
```

The double-buffering pattern (using half-transfer and transfer-complete interrupts) is standard in high-throughput embedded systems. While you're processing the first half of the buffer, DMA is filling the second half. Continuous data flow with minimal CPU involvement.

## Watchdog Timers — The Last Line of Defense

When your interrupt handler hangs (and eventually, it will), a watchdog timer saves you:

```rust
/// Watchdog timer pattern
/// The watchdog must be "pet" (reset) periodically
/// If the system hangs and fails to pet it, the watchdog resets the MCU

struct Watchdog {
    // Hardware watchdog register address
    reload_reg: *mut u32,
    key_reg: *mut u32,
}

impl Watchdog {
    /// Start the watchdog with a timeout
    unsafe fn start(&self, timeout_ms: u32) {
        // Configure timeout in hardware registers
        // Once started, the watchdog cannot be stopped (by design)
    }

    /// Pet the watchdog — must be called before timeout expires
    unsafe fn pet(&self) {
        // Write the reload key to reset the countdown
        core::ptr::write_volatile(self.key_reg, 0xAAAA);
    }
}

// In your main loop or a dedicated task:
fn main_loop(watchdog: &Watchdog) -> ! {
    loop {
        // Do work...
        do_periodic_work();

        // If we get here, we're alive — pet the watchdog
        unsafe { watchdog.pet(); }

        cortex_m::asm::wfi();
    }
    // If the system hangs in do_periodic_work(), the watchdog fires
    // and resets the entire MCU — harsh but effective
}
```

Watchdog timers are non-negotiable in production embedded systems. They're the difference between "the device hung and needs a manual power cycle" and "the device recovered automatically in 200ms."

## The Bottom Line

Interrupt-driven programming is where Rust's safety story gets both stronger and weaker. Stronger because tools like RTIC can statically verify your resource sharing is correct. Weaker because you're still dealing with timing-dependent behavior that no type system can fully capture.

The discipline required — bounded execution times, no allocation, minimal critical sections, deterministic control flow — isn't unique to Rust. But Rust gives you better tools to enforce it than C ever did.

Next up: we're going all the way to the beginning — writing a bootloader, the very first code that runs on a machine.
