---
title: "Lesson 7: Type-Level Programming — Computing with types"
author: Atharva Pandey
series: "Rust Type System Wizardry"
lesson: 7
keywords: [Rust, rust, type system, type-level programming, type-level integers, const generics, compile-time computation]
tags: [Rust tutorial, rust, type-system, advanced]
date: '2025-09-13T11:15:00.000Z'
---

There's a moment in every Rust programmer's journey where they look at `typenum` or some const-generics trick and think: "Wait, we're doing *math* at compile time? With the *type checker*?" Yes. Yes we are. And it's not a curiosity — it's the foundation for things like fixed-size matrices, compile-time dimension checking, and provably correct buffer sizes.

I first encountered type-level integers when I needed a matrix library that could guarantee at compile time that you couldn't multiply a 3x4 matrix by a 2x5 matrix. The dimensions had to match, and I wanted the compiler — not a runtime assertion — to enforce it.

## Const Generics: The Modern Approach

Rust stabilized const generics for integer types in 1.51. This is the straightforward way to do type-level integers:

```rust
struct Matrix<const ROWS: usize, const COLS: usize> {
    data: [[f64; COLS]; ROWS],
}

impl<const ROWS: usize, const COLS: usize> Matrix<ROWS, COLS> {
    fn new() -> Self {
        Matrix {
            data: [[0.0; COLS]; ROWS],
        }
    }

    fn get(&self, row: usize, col: usize) -> f64 {
        self.data[row][col]
    }

    fn set(&mut self, row: usize, col: usize, value: f64) {
        self.data[row][col] = value;
    }
}

// Matrix multiplication: (M x N) * (N x P) = (M x P)
// The inner dimensions MUST match — enforced at compile time!
impl<const M: usize, const N: usize> Matrix<M, N> {
    fn multiply<const P: usize>(&self, other: &Matrix<N, P>) -> Matrix<M, P> {
        let mut result = Matrix::<M, P>::new();
        for i in 0..M {
            for j in 0..P {
                let mut sum = 0.0;
                for k in 0..N {
                    sum += self.data[i][k] * other.data[k][j];
                }
                result.data[i][j] = sum;
            }
        }
        result
    }
}

fn main() {
    let a = Matrix::<2, 3>::new(); // 2x3 matrix
    let b = Matrix::<3, 4>::new(); // 3x4 matrix
    let c = a.multiply(&b);       // 2x4 matrix — compiles!

    let d = Matrix::<5, 2>::new(); // 5x2 matrix
    // let e = a.multiply(&d);     // COMPILE ERROR: expected Matrix<3, _>, got Matrix<5, 2>
    // The inner dimension 3 doesn't match 5
}
```

The `N` in `multiply`'s signature is shared between `self` (`Matrix<M, N>`) and `other` (`Matrix<N, P>`). If the inner dimensions don't match, the types don't unify, and the compiler rejects it. No runtime check needed.

## Const Generic Expressions (Nightly)

On nightly, you can do arithmetic with const generics:

```rust
#![feature(generic_const_exprs)]

fn concat_arrays<const M: usize, const N: usize>(
    a: [i32; M],
    b: [i32; N],
) -> [i32; M + N]
where
    [(); M + N]:,  // Required where clause for const expressions
{
    let mut result = [0i32; M + N];
    result[..M].copy_from_slice(&a);
    result[M..].copy_from_slice(&b);
    result
}

fn main() {
    let a = [1, 2, 3];
    let b = [4, 5];
    let c = concat_arrays(a, b);
    println!("{:?}", c); // [1, 2, 3, 4, 5]
    // c has type [i32; 5] — computed at compile time!
}
```

The return type `[i32; M + N]` involves compile-time arithmetic. The compiler computes the output size from the input sizes. This is genuine type-level computation.

This feature is still unstable, but it's the direction Rust is heading for type-level math.

## The Old Way: Typenum

Before const generics, the Rust ecosystem used the `typenum` crate to encode integers at the type level. It's still used in places where const generics aren't expressive enough:

```rust
// Conceptual implementation of typenum's approach
// (simplified — the real crate is more complex)

// Peano numbers at the type level
struct Zero;
struct Succ<N>(std::marker::PhantomData<N>);

// Type aliases for convenience
type One = Succ<Zero>;
type Two = Succ<One>;
type Three = Succ<Two>;

// Type-level addition
trait Add<Rhs> {
    type Output;
}

// 0 + N = N
impl<N> Add<N> for Zero {
    type Output = N;
}

// (S(M)) + N = S(M + N)
impl<M, N> Add<N> for Succ<M>
where
    M: Add<N>,
{
    type Output = Succ<M::Output>;
}

// Now we can compute at the type level:
// Two + Three = Succ<Succ<Zero>> + Succ<Succ<Succ<Zero>>>
// = Succ<Succ<Succ<Succ<Succ<Zero>>>>>
// = Five!

type Five = <Two as Add<Three>>::Output;
```

This is Peano arithmetic implemented in Rust's type system. Each number is either `Zero` or the successor of another number. Addition is defined recursively through trait implementations. The compiler evaluates it during type checking.

It's wild. The type checker is literally performing mathematical induction.

## Type-Level Booleans

We can extend this to other types of computation:

```rust
use std::marker::PhantomData;

// Type-level booleans
struct True;
struct False;

// Type-level if-then-else
trait If<Then, Else> {
    type Output;
}

impl<Then, Else> If<Then, Else> for True {
    type Output = Then;
}

impl<Then, Else> If<Then, Else> for False {
    type Output = Else;
}

// Type-level comparison
struct Zero;
struct Succ<N>(PhantomData<N>);

trait IsZero {
    type Output;
}

impl IsZero for Zero {
    type Output = True;
}

impl<N> IsZero for Succ<N> {
    type Output = False;
}

// Type-level equality
trait Equal<Rhs> {
    type Output;
}

impl Equal<Zero> for Zero {
    type Output = True;
}

impl<N> Equal<Succ<N>> for Zero {
    type Output = False;
}

impl<N> Equal<Zero> for Succ<N> {
    type Output = False;
}

impl<M, N> Equal<Succ<N>> for Succ<M>
where
    M: Equal<N>,
{
    type Output = <M as Equal<N>>::Output;
}
```

Now we can ask "is type-level number A equal to type-level number B?" at compile time. The answer is a type — `True` or `False` — and we can use `If` to branch on it.

## Practical Application: Type-Safe Units

Let's build a dimension-checked unit system:

```rust
use std::marker::PhantomData;
use std::ops::{Mul, Div};

// Dimensions as type-level signed integers
// Using const generics for each base dimension
#[derive(Debug, Clone, Copy)]
struct Quantity<const LENGTH: i32, const MASS: i32, const TIME: i32> {
    value: f64,
}

// Base units
type Meters = Quantity<1, 0, 0>;       // length^1
type Kilograms = Quantity<0, 1, 0>;    // mass^1
type Seconds = Quantity<0, 0, 1>;      // time^1

// Derived units
type MetersPerSecond = Quantity<1, 0, -1>;    // length/time
type MetersPerSecondSq = Quantity<1, 0, -2>;  // length/time^2
type Newtons = Quantity<1, 1, -2>;            // mass * length / time^2
type Joules = Quantity<2, 1, -2>;             // mass * length^2 / time^2

// Dimensionless (scalar)
type Scalar = Quantity<0, 0, 0>;

impl<const L: i32, const M: i32, const T: i32> Quantity<L, M, T> {
    fn new(value: f64) -> Self {
        Quantity { value }
    }
}

// Addition — only works for same dimensions
impl<const L: i32, const M: i32, const T: i32> std::ops::Add for Quantity<L, M, T> {
    type Output = Self;
    fn add(self, rhs: Self) -> Self {
        Quantity { value: self.value + rhs.value }
    }
}

// Scalar multiplication
impl<const L: i32, const M: i32, const T: i32> Quantity<L, M, T> {
    fn scale(self, factor: f64) -> Self {
        Quantity { value: self.value * factor }
    }
}

fn main() {
    let distance = Meters::new(100.0);
    let time = Seconds::new(9.58);

    // Can add same units
    let total_distance = distance + Meters::new(50.0);
    println!("Total: {} m", total_distance.value);

    // Can't add different units — compile error!
    // let nonsense = distance + time;
    // ERROR: expected Quantity<1, 0, 0>, found Quantity<0, 0, 1>

    let mass = Kilograms::new(70.0);
    let accel = MetersPerSecondSq::new(9.81);
    // We'd need generic_const_exprs for multiplication to work at the type level
    // On nightly, force = mass * accel would give us Newtons automatically
}
```

On stable Rust, you can't do the dimension arithmetic in `Mul`/`Div` impls because that requires `generic_const_exprs`. But the addition check works perfectly — you can only add quantities with the same dimensions.

On nightly with `generic_const_exprs`, you could write:

```rust
#![feature(generic_const_exprs)]

impl<const L1: i32, const M1: i32, const T1: i32,
     const L2: i32, const M2: i32, const T2: i32>
    std::ops::Mul<Quantity<L2, M2, T2>> for Quantity<L1, M1, T1>
where
    [(); (L1 + L2) as usize]:,
    [(); (M1 + M2) as usize]:,
    [(); (T1 + T2) as usize]:,
{
    type Output = Quantity<{L1 + L2}, {M1 + M2}, {T1 + T2}>;

    fn mul(self, rhs: Quantity<L2, M2, T2>) -> Self::Output {
        Quantity { value: self.value * rhs.value }
    }
}
```

Multiplying `Kilograms` (mass=1) by `MetersPerSecondSq` (length=1, time=-2) would give you `Quantity<1, 1, -2>` — which is `Newtons`. The compiler does the dimensional analysis for you.

## Compile-Time State Machines with Type Integers

You can use const generics to encode state machines with numbered states:

```rust
struct Machine<const STATE: u8>;

impl Machine<0> {
    fn new() -> Self { Machine }

    fn initialize(self) -> Machine<1> {
        println!("Initializing...");
        Machine
    }
}

impl Machine<1> {
    fn configure(self) -> Machine<2> {
        println!("Configuring...");
        Machine
    }
}

impl Machine<2> {
    fn run(self) -> Machine<3> {
        println!("Running...");
        Machine
    }
}

impl Machine<3> {
    fn shutdown(self) -> Machine<0> {
        println!("Shutting down...");
        Machine
    }
}

fn main() {
    let m = Machine::new(); // State 0
    let m = m.initialize(); // State 1
    let m = m.configure();  // State 2
    let m = m.run();        // State 3
    let _m = m.shutdown();  // Back to State 0

    // Can't skip states:
    // Machine::new().run(); // ERROR: no method `run` on Machine<0>
}
```

This is simpler than the ZST-based typestate from Lesson 2 and works well when your states are naturally ordered.

## Type-Level Lists

For more advanced type-level programming, you can build type-level data structures:

```rust
use std::marker::PhantomData;

// Type-level list (HList)
struct Nil;
struct Cons<Head, Tail>(PhantomData<(Head, Tail)>);

// Type-level length
trait Length {
    const VALUE: usize;
}

impl Length for Nil {
    const VALUE: usize = 0;
}

impl<H, T: Length> Length for Cons<H, T> {
    const VALUE: usize = 1 + T::VALUE;
}

// Type-level contains check
trait Contains<T> {
    const RESULT: bool;
}

impl<T> Contains<T> for Nil {
    const RESULT: bool = false;
}

impl<T, Tail: Contains<T>> Contains<T> for Cons<T, Tail> {
    const RESULT: bool = true;
}

// For non-matching heads, recurse into tail
// (This requires specialization on nightly for full generality,
// but you can work around it with different wrapper types)

fn main() {
    type MyList = Cons<i32, Cons<String, Cons<f64, Nil>>>;
    println!("Length: {}", <MyList as Length>::VALUE); // 3
}
```

Heterogeneous lists (HLists) are the backbone of libraries like `frunk`. Each element can be a different type, and the list's type encodes what's in it and in what order.

## When Type-Level Programming Makes Sense

I'll be direct: most Rust code doesn't need type-level programming. But when it does, it *really* does.

**Good use cases:**
- **Matrix/vector dimensions**: Catch mismatched dimensions at compile time
- **Physical units**: Prevent adding meters to kilograms
- **Buffer sizes**: Guarantee that output buffers are large enough
- **Protocol steps**: Ensure steps happen in order (as we saw)
- **Permission systems**: Encode required capabilities in types

**Bad use cases:**
- **Anything that depends on runtime input**: If the size comes from a config file, const generics can't help
- **Highly dynamic dimensions**: If your matrix dimensions change frequently, the type-level encoding adds friction
- **When const generics expressions aren't expressive enough**: On stable, you're limited in what arithmetic you can do

The sweet spot is when you have quantities that are known at compile time and where mixing them up would be catastrophic. Dimensional analysis, buffer math, cryptographic parameter sizes — these are where type-level integers earn their keep.

## The Trajectory

Const generics are still evolving. The current limitations — no arithmetic on stable, limited types (only integers and `bool`), no `const` trait bounds — will be lifted over time. The `generic_const_exprs` feature will eventually stabilize, and with it, type-level Rust will become dramatically more powerful.

But even today, with stable const generics, you can build compile-time-checked dimensions, fixed-size buffers, and indexed state machines. That's already a massive improvement over the "just use `assert!`" approach.

Next up: sealed traits — how to create extension points that can't be extended.
