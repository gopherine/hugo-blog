---
title: "Lesson 14: Const Generics — Types parameterized by values"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 14
keywords: [Rust, rust, traits, generics, const generics, array, compile time, type-level values]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-07-10T15:30:00.000Z'
---

Before const generics landed (Rust 1.51), working with arrays was painful. You couldn't write a function that accepted `[T; N]` for any `N`. The standard library had trait implementations for arrays up to size 32 — manually written, one per size. Need `[u8; 33]` to implement `Debug`? Too bad. That era is over, and honestly, const generics are one of the most underappreciated features in modern Rust.

Instead of parameterizing types by *other types*, you parameterize them by *values*. An array's size, a buffer's capacity, a matrix's dimensions — baked into the type system at compile time.

## The Basics

```rust
fn print_array<T: std::fmt::Debug, const N: usize>(arr: [T; N]) {
    println!("[{}] {:?}", N, arr);
}

fn main() {
    print_array([1, 2, 3]);          // N = 3
    print_array([1, 2, 3, 4, 5]);    // N = 5
    print_array(["a", "b"]);         // N = 2
}
```

`const N: usize` is a const generic parameter. It's a value — not a type — that's part of the function's signature. The compiler monomorphizes `print_array` for each array size, just like it does for type parameters.

## Why This Matters

Without const generics, you'd need to either:
1. Use slices (`&[T]`) — losing compile-time size information
2. Write separate functions for each array size — not scalable
3. Use macros — ugly and error-prone

Const generics let you keep the size in the type system:

```rust
#[derive(Debug)]
struct FixedBuffer<const N: usize> {
    data: [u8; N],
    len: usize,
}

impl<const N: usize> FixedBuffer<N> {
    fn new() -> Self {
        FixedBuffer {
            data: [0u8; N],
            len: 0,
        }
    }

    fn capacity(&self) -> usize {
        N
    }

    fn push(&mut self, byte: u8) -> Result<(), &'static str> {
        if self.len >= N {
            return Err("buffer full");
        }
        self.data[self.len] = byte;
        self.len += 1;
        Ok(())
    }

    fn as_slice(&self) -> &[u8] {
        &self.data[..self.len]
    }
}

impl<const N: usize> std::fmt::Display for FixedBuffer<N> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "FixedBuffer<{}>[{}/{}]", N, self.len, N)
    }
}

fn main() {
    let mut small = FixedBuffer::<4>::new();
    let mut large = FixedBuffer::<1024>::new();

    small.push(b'h').unwrap();
    small.push(b'i').unwrap();

    large.push(b'H').unwrap();
    large.push(b'e').unwrap();
    large.push(b'l').unwrap();
    large.push(b'l').unwrap();
    large.push(b'o').unwrap();

    println!("{}", small);  // FixedBuffer<4>[2/4]
    println!("{}", large);  // FixedBuffer<1024>[5/1024]

    // These are DIFFERENT types:
    // let x: FixedBuffer<4> = large; // ERROR: mismatched types
}
```

`FixedBuffer<4>` and `FixedBuffer<1024>` are different types. The compiler enforces at the type level that you can't accidentally mix buffer sizes. And the array lives on the stack — no heap allocation.

## Const Generic Structs

The matrix example is the classic motivator:

```rust
use std::ops::{Add, Mul};

#[derive(Debug, Clone)]
struct Matrix<const ROWS: usize, const COLS: usize> {
    data: [[f64; COLS]; ROWS],
}

impl<const ROWS: usize, const COLS: usize> Matrix<ROWS, COLS> {
    fn new() -> Self {
        Matrix {
            data: [[0.0; COLS]; ROWS],
        }
    }

    fn from_array(data: [[f64; COLS]; ROWS]) -> Self {
        Matrix { data }
    }

    fn get(&self, row: usize, col: usize) -> f64 {
        self.data[row][col]
    }

    fn set(&mut self, row: usize, col: usize, val: f64) {
        self.data[row][col] = val;
    }

    fn transpose(&self) -> Matrix<COLS, ROWS> {
        let mut result = Matrix::<COLS, ROWS>::new();
        for i in 0..ROWS {
            for j in 0..COLS {
                result.data[j][i] = self.data[i][j];
            }
        }
        result
    }
}

// Addition: only matrices of the same size
impl<const ROWS: usize, const COLS: usize> Add for Matrix<ROWS, COLS> {
    type Output = Matrix<ROWS, COLS>;

    fn add(self, rhs: Self) -> Self::Output {
        let mut result = Matrix::new();
        for i in 0..ROWS {
            for j in 0..COLS {
                result.data[i][j] = self.data[i][j] + rhs.data[i][j];
            }
        }
        result
    }
}

// Multiplication: (M×N) * (N×P) = (M×P)
// The inner dimensions must match — enforced at compile time!
impl<const M: usize, const N: usize, const P: usize> Mul<Matrix<N, P>> for Matrix<M, N> {
    type Output = Matrix<M, P>;

    fn mul(self, rhs: Matrix<N, P>) -> Matrix<M, P> {
        let mut result = Matrix::<M, P>::new();
        for i in 0..M {
            for j in 0..P {
                let mut sum = 0.0;
                for k in 0..N {
                    sum += self.data[i][k] * rhs.data[k][j];
                }
                result.data[i][j] = sum;
            }
        }
        result
    }
}

impl<const ROWS: usize, const COLS: usize> std::fmt::Display for Matrix<ROWS, COLS> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        for row in &self.data {
            let formatted: Vec<String> = row.iter().map(|v| format!("{:7.2}", v)).collect();
            writeln!(f, "| {} |", formatted.join(" "))?;
        }
        Ok(())
    }
}

fn main() {
    let a = Matrix::from_array([
        [1.0, 2.0, 3.0],
        [4.0, 5.0, 6.0],
    ]); // 2×3

    let b = Matrix::from_array([
        [7.0, 8.0],
        [9.0, 10.0],
        [11.0, 12.0],
    ]); // 3×2

    let c = a.clone() * b; // (2×3) * (3×2) = (2×2) ✅
    println!("A * B (2x2):");
    println!("{}", c);

    let t = a.transpose(); // 2×3 → 3×2
    println!("A transposed (3x2):");
    println!("{}", t);

    // This won't compile — dimension mismatch:
    // let bad = a * Matrix::<2, 2>::new(); // (2×3) * (2×2) — inner dims don't match
}
```

The matrix multiplication signature `Mul<Matrix<N, P>> for Matrix<M, N>` ensures the inner dimensions match. If you try to multiply a 2x3 matrix by a 2x2 matrix, the compiler catches it. Dimensional analysis, enforced by the type checker. This is incredible.

## Const Generics in Trait Implementations

You can implement traits conditionally based on const generic values:

```rust
#[derive(Debug)]
struct SmallVec<T, const N: usize> {
    data: [Option<T>; N],
    len: usize,
}

// Need a helper because [Option<T>; N] requires T: Copy for array init
impl<T: Copy + Default, const N: usize> SmallVec<T, N> {
    fn new() -> Self {
        SmallVec {
            data: [None; N],
            len: 0,
        }
    }

    fn push(&mut self, item: T) -> bool {
        if self.len >= N {
            return false;
        }
        self.data[self.len] = Some(item);
        self.len += 1;
        true
    }

    fn get(&self, index: usize) -> Option<&T> {
        if index < self.len {
            self.data[index].as_ref()
        } else {
            None
        }
    }

    fn as_slice(&self) -> Vec<&T> {
        self.data[..self.len]
            .iter()
            .filter_map(|x| x.as_ref())
            .collect()
    }
}

impl<T: Copy + Default + std::fmt::Display, const N: usize> std::fmt::Display for SmallVec<T, N> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "SmallVec<{}>[ ", N)?;
        for i in 0..self.len {
            if let Some(ref item) = self.data[i] {
                write!(f, "{} ", item)?;
            }
        }
        write!(f, "]")
    }
}

fn main() {
    let mut sv = SmallVec::<i32, 4>::new();
    sv.push(10);
    sv.push(20);
    sv.push(30);
    println!("{}", sv);

    // Won't fit
    let mut tiny = SmallVec::<u8, 2>::new();
    tiny.push(1);
    tiny.push(2);
    let ok = tiny.push(3); // returns false
    println!("{}, overflow: {}", tiny, !ok);
}
```

## Const Generics with Default Values

As of recent Rust, you can have default values for const generics (though full support is still stabilizing):

```rust
struct RingBuffer<T, const N: usize = 64> {
    data: Vec<Option<T>>,
    head: usize,
    tail: usize,
}

impl<T, const N: usize> RingBuffer<T, N> {
    fn new() -> Self {
        let mut data = Vec::with_capacity(N);
        for _ in 0..N {
            data.push(None);
        }
        RingBuffer {
            data,
            head: 0,
            tail: 0,
        }
    }

    fn push(&mut self, item: T) {
        self.data[self.tail] = Some(item);
        self.tail = (self.tail + 1) % N;
        if self.tail == self.head {
            self.head = (self.head + 1) % N;
        }
    }

    fn pop(&mut self) -> Option<T> {
        if self.head == self.tail {
            return None;
        }
        let item = self.data[self.head].take();
        self.head = (self.head + 1) % N;
        item
    }
}

fn main() {
    // Uses default N = 64
    let mut rb: RingBuffer<String> = RingBuffer::new();
    rb.push(String::from("first"));
    rb.push(String::from("second"));
    println!("{:?}", rb.pop()); // Some("first")

    // Custom size
    let mut small: RingBuffer<i32, 4> = RingBuffer::new();
    small.push(1);
    small.push(2);
    small.push(3);
    small.push(4);
    small.push(5); // overwrites 1
    println!("{:?}", small.pop()); // Some(2)
}
```

## Current Limitations

Const generics are powerful but still have rough edges:

**Supported const types (as of stable Rust):**
- Integer types (`usize`, `i32`, `u8`, etc.)
- `bool`
- `char`

**Not yet supported in stable:**
- Custom types as const parameters
- Complex const expressions in type positions (e.g., `Matrix<{N+1}>`)
- Full const generic arithmetic

```rust
// This does NOT work yet (on stable):
// fn double_array<T: Copy + Default, const N: usize>(arr: [T; N]) -> [T; {N * 2}] {
//     let mut result = [T::default(); N * 2];
//     for i in 0..N {
//         result[i] = arr[i];
//         result[i + N] = arr[i];
//     }
//     result
// }

// Workaround: pass both sizes explicitly
fn double_array<T: Copy + Default, const N: usize, const M: usize>(
    arr: [T; N],
) -> [T; M] {
    assert_eq!(M, N * 2, "M must be 2 * N");
    let mut result = [T::default(); M];
    for i in 0..N {
        result[i] = arr[i];
        result[i + N] = arr[i];
    }
    result
}

fn main() {
    let doubled: [i32; 6] = double_array([1, 2, 3]);
    println!("{:?}", doubled); // [1, 2, 3, 1, 2, 3]
}
```

Not ideal — the caller has to specify `M` and the runtime assert replaces a compile-time check. This will improve as const generics mature.

## Practical Use Case: Compile-Time Validated IDs

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
struct Id<const PREFIX: char> {
    value: u64,
}

impl<const PREFIX: char> Id<PREFIX> {
    fn new(value: u64) -> Self {
        Id { value }
    }
}

impl<const PREFIX: char> std::fmt::Display for Id<PREFIX> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}-{}", PREFIX, self.value)
    }
}

type UserId = Id<'U'>;
type OrderId = Id<'O'>;
type ProductId = Id<'P'>;

fn process_order(order_id: OrderId, user_id: UserId) {
    println!("Processing order {} for user {}", order_id, user_id);
}

fn main() {
    let user = UserId::new(42);
    let order = OrderId::new(1001);
    let product = ProductId::new(555);

    process_order(order, user);

    // Type safe — can't mix them up:
    // process_order(user, order); // ERROR: expected Id<'O'>, found Id<'U'>
    // process_order(product, user); // ERROR: expected Id<'O'>, found Id<'P'>

    println!("{}, {}, {}", user, order, product);
    // U-42, O-1001, P-555
}
```

Same underlying struct, different types based on a const char parameter. Zero runtime cost. Full type safety.

## Key Takeaways

Const generics let you parameterize types by values, not just types. They enable compile-time dimensional analysis (matrix math), stack-allocated fixed-size containers, and type-level value encoding. They're monomorphized like regular generics — zero runtime cost. Current limitations include restricted const expression support, but the basics (`usize`, `bool`, `char` parameters) are stable and production-ready.

Think of const generics as moving information from runtime to compile time. Every value you encode in the type system is one fewer runtime check you need.

Next and final — GATs (Generic Associated Types), the long-awaited feature that completes Rust's trait system.
