---
title: "Lesson 12: Operator Overloading with Traits — Making your types feel native"
author: Atharva Pandey
series: "Rust Traits & Generics Masterclass"
lesson: 12
keywords: [Rust, rust, traits, generics, operator overloading, Add, Mul, Index, ops]
tags: [Rust tutorial, rust, traits, generics]
date: '2024-07-04T20:15:00.000Z'
---

I was building a linear algebra library and got tired of writing `vector_a.add(&vector_b)` everywhere. It looked ugly. It read poorly. Math should look like math — `a + b`, not `a.add(&b)`. In Rust, operator overloading isn't some dark magic — it's just trait implementation. Every operator maps to a trait in `std::ops`, and implementing that trait makes the operator work on your type.

## The `Add` Trait

The `+` operator desugars to a call to `Add::add`:

```rust
use std::ops::Add;

#[derive(Debug, Clone, Copy)]
struct Vec2 {
    x: f64,
    y: f64,
}

impl Vec2 {
    fn new(x: f64, y: f64) -> Self {
        Vec2 { x, y }
    }
}

impl Add for Vec2 {
    type Output = Vec2;

    fn add(self, rhs: Vec2) -> Vec2 {
        Vec2 {
            x: self.x + rhs.x,
            y: self.y + rhs.y,
        }
    }
}

fn main() {
    let a = Vec2::new(1.0, 2.0);
    let b = Vec2::new(3.0, 4.0);
    let c = a + b;
    println!("{:?}", c); // Vec2 { x: 4.0, y: 6.0 }

    // a and b are still usable because Vec2 is Copy
    println!("a = {:?}, b = {:?}", a, b);
}
```

Notice `type Output = Vec2` — the result type of the addition. It doesn't have to be the same as the input types. You could add a `Vec2` and a `f64` and get back a `Vec2`:

```rust
use std::ops::Add;

#[derive(Debug, Clone, Copy)]
struct Vec2 {
    x: f64,
    y: f64,
}

impl Vec2 {
    fn new(x: f64, y: f64) -> Self {
        Vec2 { x, y }
    }
}

// Vec2 + Vec2
impl Add for Vec2 {
    type Output = Vec2;
    fn add(self, rhs: Vec2) -> Vec2 {
        Vec2 { x: self.x + rhs.x, y: self.y + rhs.y }
    }
}

// Vec2 + f64 (scalar add)
impl Add<f64> for Vec2 {
    type Output = Vec2;
    fn add(self, scalar: f64) -> Vec2 {
        Vec2 { x: self.x + scalar, y: self.y + scalar }
    }
}

fn main() {
    let v = Vec2::new(1.0, 2.0);
    let shifted = v + 10.0;
    println!("{:?}", shifted); // Vec2 { x: 11.0, y: 12.0 }
}
```

## All the Arithmetic Operators

Here's the full set for a `Vec2`:

```rust
use std::ops::{Add, Sub, Mul, Neg};
use std::fmt;

#[derive(Debug, Clone, Copy)]
struct Vec2 {
    x: f64,
    y: f64,
}

impl Vec2 {
    fn new(x: f64, y: f64) -> Self { Vec2 { x, y } }

    fn magnitude(&self) -> f64 {
        (self.x * self.x + self.y * self.y).sqrt()
    }

    fn dot(&self, other: &Vec2) -> f64 {
        self.x * other.x + self.y * other.y
    }
}

impl fmt::Display for Vec2 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "({:.2}, {:.2})", self.x, self.y)
    }
}

impl Add for Vec2 {
    type Output = Vec2;
    fn add(self, rhs: Vec2) -> Vec2 {
        Vec2 { x: self.x + rhs.x, y: self.y + rhs.y }
    }
}

impl Sub for Vec2 {
    type Output = Vec2;
    fn sub(self, rhs: Vec2) -> Vec2 {
        Vec2 { x: self.x - rhs.x, y: self.y - rhs.y }
    }
}

// Scalar multiplication: Vec2 * f64
impl Mul<f64> for Vec2 {
    type Output = Vec2;
    fn mul(self, scalar: f64) -> Vec2 {
        Vec2 { x: self.x * scalar, y: self.y * scalar }
    }
}

// Also: f64 * Vec2
impl Mul<Vec2> for f64 {
    type Output = Vec2;
    fn mul(self, vec: Vec2) -> Vec2 {
        Vec2 { x: self * vec.x, y: self * vec.y }
    }
}

impl Neg for Vec2 {
    type Output = Vec2;
    fn neg(self) -> Vec2 {
        Vec2 { x: -self.x, y: -self.y }
    }
}

fn main() {
    let a = Vec2::new(3.0, 4.0);
    let b = Vec2::new(1.0, 2.0);

    println!("a + b = {}", a + b);
    println!("a - b = {}", a - b);
    println!("a * 2 = {}", a * 2.0);
    println!("2 * a = {}", 2.0 * a);
    println!("-a = {}", -a);
    println!("|a| = {:.2}", a.magnitude());
    println!("a · b = {:.2}", a.dot(&b));
}
```

The `Mul<Vec2> for f64` impl is the reverse direction — it lets you write `2.0 * vec` as well as `vec * 2.0`. Without it, only `vec * 2.0` works. Feels unnatural without both.

## Comparison Operators

`PartialEq` powers `==` and `!=`. `PartialOrd` powers `<`, `>`, `<=`, `>=`:

```rust
use std::cmp::Ordering;

#[derive(Debug)]
struct SemVer {
    major: u32,
    minor: u32,
    patch: u32,
}

impl SemVer {
    fn new(major: u32, minor: u32, patch: u32) -> Self {
        SemVer { major, minor, patch }
    }
}

impl PartialEq for SemVer {
    fn eq(&self, other: &Self) -> bool {
        self.major == other.major
            && self.minor == other.minor
            && self.patch == other.patch
    }
}

impl Eq for SemVer {}

impl PartialOrd for SemVer {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for SemVer {
    fn cmp(&self, other: &Self) -> Ordering {
        self.major.cmp(&other.major)
            .then(self.minor.cmp(&other.minor))
            .then(self.patch.cmp(&other.patch))
    }
}

impl std::fmt::Display for SemVer {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}.{}.{}", self.major, self.minor, self.patch)
    }
}

fn main() {
    let v1 = SemVer::new(1, 9, 0);
    let v2 = SemVer::new(2, 0, 0);
    let v3 = SemVer::new(1, 9, 0);

    println!("{} == {} → {}", v1, v3, v1 == v3); // true
    println!("{} < {} → {}", v1, v2, v1 < v2);   // true
    println!("{} > {} → {}", v2, v1, v2 > v1);   // true

    let mut versions = vec![
        SemVer::new(2, 1, 0),
        SemVer::new(1, 0, 0),
        SemVer::new(1, 9, 5),
    ];
    versions.sort();
    for v in &versions {
        println!("{}", v);
    }
}
```

The `.then()` chaining on `Ordering` is beautiful for multi-field comparisons.

## Index and IndexMut

The `[]` operator:

```rust
use std::ops::{Index, IndexMut};

#[derive(Debug)]
struct Matrix {
    data: Vec<Vec<f64>>,
    rows: usize,
    cols: usize,
}

impl Matrix {
    fn new(rows: usize, cols: usize) -> Self {
        Matrix {
            data: vec![vec![0.0; cols]; rows],
            rows,
            cols,
        }
    }

    fn identity(size: usize) -> Self {
        let mut m = Matrix::new(size, size);
        for i in 0..size {
            m[i][i] = 1.0;
        }
        m
    }
}

// matrix[row] returns &Vec<f64>, then [col] indexes into that
impl Index<usize> for Matrix {
    type Output = Vec<f64>;

    fn index(&self, row: usize) -> &Vec<f64> {
        &self.data[row]
    }
}

impl IndexMut<usize> for Matrix {
    fn index_mut(&mut self, row: usize) -> &mut Vec<f64> {
        &mut self.data[row]
    }
}

impl std::fmt::Display for Matrix {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        for row in &self.data {
            let formatted: Vec<String> = row.iter().map(|v| format!("{:6.2}", v)).collect();
            writeln!(f, "| {} |", formatted.join(" "))?;
        }
        Ok(())
    }
}

fn main() {
    let mut m = Matrix::new(3, 3);
    m[0][0] = 1.0;
    m[0][1] = 2.0;
    m[1][1] = 5.0;
    m[2][0] = 3.0;
    m[2][2] = 9.0;

    println!("Custom matrix:");
    println!("{}", m);

    println!("Identity 3x3:");
    println!("{}", Matrix::identity(3));

    // Reading
    println!("m[1][1] = {}", m[1][1]);
}
```

## AddAssign and Friends

The compound assignment operators (`+=`, `-=`, `*=`):

```rust
use std::ops::{Add, AddAssign, Mul, MulAssign};

#[derive(Debug, Clone, Copy)]
struct Money {
    cents: i64,
}

impl Money {
    fn new(dollars: i64, cents: i64) -> Self {
        Money {
            cents: dollars * 100 + cents,
        }
    }

    fn dollars(&self) -> f64 {
        self.cents as f64 / 100.0
    }
}

impl std::fmt::Display for Money {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        if self.cents < 0 {
            write!(f, "-${:.2}", (-self.cents) as f64 / 100.0)
        } else {
            write!(f, "${:.2}", self.dollars())
        }
    }
}

impl Add for Money {
    type Output = Money;
    fn add(self, rhs: Money) -> Money {
        Money { cents: self.cents + rhs.cents }
    }
}

impl AddAssign for Money {
    fn add_assign(&mut self, rhs: Money) {
        self.cents += rhs.cents;
    }
}

impl Mul<i64> for Money {
    type Output = Money;
    fn mul(self, quantity: i64) -> Money {
        Money { cents: self.cents * quantity }
    }
}

impl MulAssign<i64> for Money {
    fn mul_assign(&mut self, quantity: i64) {
        self.cents *= quantity;
    }
}

fn main() {
    let coffee = Money::new(4, 50);
    let pastry = Money::new(3, 75);

    let total = coffee + pastry;
    println!("Total: {}", total);

    let mut tab = Money::new(0, 0);
    tab += coffee;
    tab += pastry;
    tab += Money::new(2, 0); // tip
    println!("Tab: {}", tab);

    let bulk = coffee * 10;
    println!("10 coffees: {}", bulk);

    let mut order = coffee;
    order *= 3;
    println!("3 coffees: {}", order);
}
```

## Deref and DerefMut

Not strictly an operator, but `Deref` controls the `*` dereference operator and enables dot-operator method resolution:

```rust
use std::ops::Deref;

struct NonEmpty<T> {
    first: T,
    rest: Vec<T>,
}

impl<T> NonEmpty<T> {
    fn new(first: T) -> Self {
        NonEmpty { first, rest: Vec::new() }
    }

    fn push(&mut self, item: T) {
        self.rest.push(item);
    }

    fn first(&self) -> &T {
        &self.first
    }

    fn to_vec(&self) -> Vec<&T> {
        let mut v = vec![&self.first];
        v.extend(self.rest.iter());
        v
    }
}

impl<T: std::fmt::Debug> std::fmt::Debug for NonEmpty<T> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "NonEmpty({:?}, {:?})", self.first, self.rest)
    }
}

fn main() {
    let mut ne = NonEmpty::new(1);
    ne.push(2);
    ne.push(3);

    println!("First: {:?}", ne.first());
    println!("All: {:?}", ne.to_vec());
    println!("{:?}", ne);
}
```

## When Not to Overload

Operator overloading is powerful but easy to abuse. My guidelines:

**Do overload when:**
- The operation has a clear mathematical meaning (vectors, matrices, money)
- Users would be confused if the operator *didn't* work
- The semantics are unsurprising — `a + b` should be commutative if possible

**Don't overload when:**
- The operation is a stretch (using `+` for string concatenation on non-string types)
- The behavior would surprise someone reading the code
- You'd need to explain what `*` means for your type

```rust
// DON'T do this:
// impl Add for Database {
//     fn add(self, rhs: Database) -> Database { self.merge(rhs) }
// }
// "database1 + database2" is not clear

// DO write a named method instead:
// fn merge(self, other: Database) -> Database { ... }
```

## The Complete Operator Trait Map

| Operator | Trait | Method |
|----------|-------|--------|
| `+` | `Add` | `add` |
| `-` | `Sub` | `sub` |
| `*` | `Mul` | `mul` |
| `/` | `Div` | `div` |
| `%` | `Rem` | `rem` |
| `-x` | `Neg` | `neg` |
| `!x` | `Not` | `not` |
| `&` | `BitAnd` | `bitand` |
| `\|` | `BitOr` | `bitor` |
| `^` | `BitXor` | `bitxor` |
| `<<` | `Shl` | `shl` |
| `>>` | `Shr` | `shr` |
| `+=` | `AddAssign` | `add_assign` |
| `[]` | `Index` | `index` |
| `[]=` | `IndexMut` | `index_mut` |
| `*x` | `Deref` | `deref` |
| `==` | `PartialEq` | `eq` |
| `<` | `PartialOrd` | `partial_cmp` |

## Key Takeaways

Every Rust operator maps to a trait in `std::ops`. Implement the trait, get the operator. Use `type Output` to control return types. Implement both directions (`Vec2 * f64` and `f64 * Vec2`) for natural-feeling math. Don't overload operators when the semantics aren't obvious.

Next — monomorphization, where we look under the hood at how generics become fast machine code.
