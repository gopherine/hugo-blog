---
title: "Lesson 15: When You're Fighting the Borrow Checker — Restructure, Don't Hack"
author: Atharva Pandey
series: "Rust Ownership & Lifetimes Masterclass"
lesson: 15
keywords: [Rust, rust, ownership, lifetimes, borrow checker, design patterns, refactoring]
tags: [Rust tutorial, rust, ownership, lifetimes]
date: '2024-06-18T12:38:00.000Z'
---

Every Rust developer has been there. You're implementing something that feels simple. It works in your head. But the borrow checker says no. You try different approaches. More errors. You start adding `.clone()` everywhere. You wrap things in `Rc<RefCell<...>>`. You consider `unsafe`.

Stop. Take a breath. The borrow checker isn't wrong — your data flow is confused. And there's almost always a clean restructuring that makes the error disappear.

This lesson is a field guide. Real patterns that fight the borrow checker, and the restructurings that fix them.

## Pattern 1: Mutating a Collection While Iterating

The classic. You want to remove items from a vector based on some condition:

```rust
fn main() {
    let mut items = vec![1, 2, 3, 4, 5, 6];

    // WON'T COMPILE: iterating and mutating simultaneously
    // for item in &items {
    //     if *item % 2 == 0 {
    //         items.retain(|x| x != item);
    //     }
    // }
}
```

**The fix:** separate the reading and writing phases.

```rust
fn main() {
    let mut items = vec![1, 2, 3, 4, 5, 6];

    // Option 1: retain (single pass, best option here)
    items.retain(|item| item % 2 != 0);
    println!("{:?}", items);  // [1, 3, 5]

    // Option 2: collect indices first, then remove
    let mut items = vec![1, 2, 3, 4, 5, 6];
    let to_remove: Vec<usize> = items.iter()
        .enumerate()
        .filter(|(_, item)| *item % 2 == 0)
        .map(|(i, _)| i)
        .collect();

    // Remove in reverse order to keep indices valid
    for i in to_remove.into_iter().rev() {
        items.remove(i);
    }
    println!("{:?}", items);  // [1, 3, 5]

    // Option 3: build a new collection
    let items = vec![1, 2, 3, 4, 5, 6];
    let items: Vec<i32> = items.into_iter().filter(|x| x % 2 != 0).collect();
    println!("{:?}", items);  // [1, 3, 5]
}
```

## Pattern 2: Two Mutable Borrows into the Same Struct

You have a struct with multiple fields and you want to mutate two of them in the same function call:

```rust
struct Game {
    players: Vec<String>,
    scores: Vec<u32>,
}

impl Game {
    // WON'T COMPILE:
    // fn update(&mut self) {
    //     for player in &self.players {  // borrows self
    //         self.add_score(player, 10);  // borrows self mutably
    //     }
    // }

    fn add_score(&mut self, player: &str, score: u32) {
        if let Some(idx) = self.players.iter().position(|p| p == player) {
            self.scores[idx] += score;
        }
    }
}
```

**The fix:** borrow fields directly instead of borrowing self.

```rust
struct Game {
    players: Vec<String>,
    scores: Vec<u32>,
}

impl Game {
    fn update(&mut self) {
        // Borrow individual fields — the compiler can see they don't overlap
        let players = &self.players;
        let scores = &mut self.scores;

        for (i, _player) in players.iter().enumerate() {
            scores[i] += 10;
        }
    }
}

fn main() {
    let mut game = Game {
        players: vec!["Alice".into(), "Bob".into()],
        scores: vec![0, 0],
    };

    game.update();
    println!("{:?}", game.scores);  // [10, 10]
}
```

The compiler can see that `&self.players` and `&mut self.scores` borrow different fields. It allows this. But `&self` (entire struct) + `&mut self` (entire struct) conflict.

## Pattern 3: Returning a Reference to Something You Just Created

```rust
struct Cache {
    entries: std::collections::HashMap<String, String>,
}

impl Cache {
    // WON'T COMPILE:
    // fn get_or_insert(&mut self, key: &str) -> &String {
    //     if let Some(val) = self.entries.get(key) {
    //         return val;  // borrows self immutably
    //     }
    //     self.entries.insert(key.to_string(), "default".to_string());
    //     // borrows self mutably — conflicts!
    //     self.entries.get(key).unwrap()
    // }
}
```

**The fix:** use the entry API, or restructure the control flow.

```rust
use std::collections::HashMap;

struct Cache {
    entries: HashMap<String, String>,
}

impl Cache {
    fn get_or_insert(&mut self, key: &str) -> &String {
        self.entries
            .entry(key.to_string())
            .or_insert_with(|| "default".to_string())
    }
}

fn main() {
    let mut cache = Cache {
        entries: HashMap::new(),
    };

    let val = cache.get_or_insert("foo");
    println!("{}", val);  // "default"
}
```

The `entry` API exists precisely because this pattern is so common. It handles the "check and insert" atomically from a borrow-checker perspective.

## Pattern 4: Closures Capturing &mut self

```rust
struct Processor {
    data: Vec<i32>,
    log: Vec<String>,
}

impl Processor {
    // WON'T COMPILE:
    // fn process(&mut self) {
    //     self.data.iter().for_each(|item| {
    //         self.log.push(format!("Processed {}", item));
    //         // Closure borrows self for log, iterator borrows self for data
    //     });
    // }
}
```

**The fix:** destructure self into separate borrows before the closure.

```rust
struct Processor {
    data: Vec<i32>,
    log: Vec<String>,
}

impl Processor {
    fn process(&mut self) {
        let data = &self.data;
        let log = &mut self.log;

        data.iter().for_each(|item| {
            log.push(format!("Processed {}", item));
        });
    }
}

fn main() {
    let mut p = Processor {
        data: vec![1, 2, 3],
        log: Vec::new(),
    };

    p.process();
    println!("{:?}", p.log);
    // ["Processed 1", "Processed 2", "Processed 3"]
}
```

## Pattern 5: The "Temporary Doesn't Live Long Enough"

```rust
fn main() {
    // WON'T COMPILE:
    // let r;
    // {
    //     let s = String::from("hello");
    //     r = &s;
    // }
    // println!("{}", r);

    // Fix: extend the lifetime by moving the binding
    let s = String::from("hello");
    let r = &s;
    println!("{}", r);
}
```

This is usually obvious, but it shows up in subtle ways:

```rust
// Subtle version:
fn get_name() -> String {
    String::from("Atharva")
}

fn main() {
    // WON'T COMPILE:
    // let name_ref: &str = &get_name();
    // do_something_slow();
    // println!("{}", name_ref);
    // Actually this DOES compile now due to temporary lifetime extension
    // But be careful — the temporary lives until end of the statement or block

    // Clearer:
    let name = get_name();
    let name_ref: &str = &name;
    println!("{}", name_ref);
}
```

## Pattern 6: Swapping Values in a Data Structure

```rust
fn main() {
    let mut v = vec![String::from("hello"), String::from("world")];

    // WON'T COMPILE — two mutable borrows
    // let a = &mut v[0];
    // let b = &mut v[1];
    // std::mem::swap(a, b);

    // Fix: use the dedicated swap method
    v.swap(0, 1);
    println!("{:?}", v);  // ["world", "hello"]

    // Or split_at_mut for non-adjacent work
    let (left, right) = v.split_at_mut(1);
    std::mem::swap(&mut left[0], &mut right[0]);
    println!("{:?}", v);  // ["hello", "world"]
}
```

`split_at_mut` is the key tool for getting two `&mut` references into different parts of a slice. The compiler can verify the slices don't overlap.

## Pattern 7: State Machines with Borrowed Data

```rust
// The naive approach — lifetimes everywhere
struct Parser<'a> {
    input: &'a str,
    pos: usize,
}

// What if the parser needs to live longer than the input?
// Or what if you want to store parsers in a collection?

// Better: own the data
struct Parser {
    input: String,
    pos: usize,
}

impl Parser {
    fn remaining(&self) -> &str {
        &self.input[self.pos..]
    }

    fn advance(&mut self, n: usize) {
        self.pos += n;
    }
}
```

When in doubt, own the data. You can always create temporary borrows through methods.

## The Clone Escape Hatch

Sometimes `.clone()` is the right answer. Don't feel guilty about it:

```rust
fn main() {
    let mut map = std::collections::HashMap::new();
    map.insert("a", vec![1, 2, 3]);
    map.insert("b", vec![4, 5, 6]);

    // Need to read "a" while modifying "b"
    // The borrow checker can't see through HashMap::get
    let a_data = map.get("a").unwrap().clone();  // clone to avoid the borrow

    if let Some(b_data) = map.get_mut("b") {
        for val in &a_data {
            b_data.push(*val);
        }
    }

    println!("{:?}", map.get("b"));  // Some([4, 5, 6, 1, 2, 3])
}
```

Is this zero-cost? No. Is it correct, readable, and maintainable? Yes. Clone is better than unsafe. Clone is better than `Rc<RefCell<...>>` when the data is small. Clone is a tool in your toolbox — use it when the alternatives are worse.

## The Decision Framework

When the borrow checker rejects your code:

1. **Can you split the borrows?** Borrow individual fields instead of the whole struct.
2. **Can you separate read and write phases?** Collect data first, mutate second.
3. **Can you use an API designed for this?** `entry`, `split_at_mut`, `retain`, `drain`.
4. **Can you restructure the data?** Own instead of borrow. Use indices instead of references.
5. **Is a clone acceptable?** Small data, infrequent operation — just clone it.
6. **Do you genuinely need shared mutable state?** `RefCell` or `Mutex` — but this should be rare.
7. **Is this fundamentally a self-referential struct?** Store indices, not references.

Notice that `unsafe` isn't on this list. In application code, you almost never need it. If you think you do, you're probably missing a safer restructuring.

## The Mindset Shift

The borrow checker isn't a constraint to work around. It's a design feedback mechanism. When it rejects your code, it's telling you one of these things:

- Your function is doing too much (read AND write in one operation)
- Your struct is too coupled (single struct managing unrelated concerns)
- Your data flow is ambiguous (unclear who owns what)
- You're conflating ownership with access (borrowing when you should own, or vice versa)

Listen to it. Restructure your code. You'll end up with smaller functions, more cohesive types, and clearer data flow. Not because the borrow checker forced you — because those are genuinely better designs.

After five years of Rust, I almost never fight the borrow checker. Not because I memorized all the rules. Because I design data flow first and write code second. The borrow checker just confirms I got it right.

That's the whole ownership and lifetimes story. From stack and heap to Pin and Unpin, from moves to weak references — it's all consequences of one idea: every value has one owner. Everything else follows.
