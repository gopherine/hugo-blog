---
title: "Lesson 11: Structs — Modeling your domain"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 11
keywords: [Rust, rust, structs, struct, data modeling, tuple struct, named fields]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-23T20:15:00.000Z'
---

I worked on a Go codebase once where someone had passed around a `map[string]interface{}` for user data. It was fine until someone misspelled "email" as "emial" and we spent half a day tracking down why emails weren't sending. Structs are how you prevent this entire category of mistake — named fields with typed data, checked at compile time.

## Defining a Struct

```rust
struct User {
    name: String,
    email: String,
    age: u32,
    active: bool,
}

fn main() {
    let user = User {
        name: String::from("Alice"),
        email: String::from("alice@example.com"),
        age: 30,
        active: true,
    };

    println!("{} ({}) - age {}", user.name, user.email, user.age);
}
```

Struct names are `PascalCase`. Field names are `snake_case`. These aren't suggestions — the compiler warns you if you deviate.

Every field must be initialized. No partial construction. No null fields. If you forget a field, the compiler tells you.

## Field Access and Mutation

Access fields with dot notation. Mutation requires the entire variable to be `mut`:

```rust
struct Point {
    x: f64,
    y: f64,
}

fn main() {
    let mut p = Point { x: 1.0, y: 2.0 };

    println!("({}, {})", p.x, p.y);

    p.x = 3.0;
    p.y = 4.0;
    println!("({}, {})", p.x, p.y);
}
```

You can't make individual fields mutable — it's all or nothing. If the variable is `mut`, all fields are mutable. If it's not, none are. This is a deliberate simplification. If you want finer-grained control, you'll use interior mutability patterns (advanced topic for later).

## Field Init Shorthand

When a variable has the same name as a struct field, you can skip the redundancy:

```rust
struct User {
    name: String,
    email: String,
    age: u32,
}

fn create_user(name: String, email: String) -> User {
    User {
        name,       // shorthand for name: name
        email,      // shorthand for email: email
        age: 0,
    }
}

fn main() {
    let user = create_user(
        String::from("Bob"),
        String::from("bob@example.com"),
    );
    println!("{}: {}", user.name, user.email);
}
```

Small thing, but you'll appreciate it — especially with larger structs.

## Struct Update Syntax

Create a new struct from an existing one, overriding specific fields:

```rust
struct Config {
    host: String,
    port: u16,
    max_connections: u32,
    timeout_secs: u64,
}

fn main() {
    let default_config = Config {
        host: String::from("localhost"),
        port: 8080,
        max_connections: 100,
        timeout_secs: 30,
    };

    let production = Config {
        host: String::from("0.0.0.0"),
        port: 443,
        ..default_config  // take remaining fields from default_config
    };

    println!("{}:{}", production.host, production.port);
    println!("max_conn: {}", production.max_connections);  // 100, from default
}
```

The `..default_config` syntax copies (or moves) the remaining fields. Be careful with this — if any moved field is a `String` or other non-Copy type, the source struct is partially moved and can't be used as a whole anymore:

```rust
struct User {
    name: String,
    age: u32,
}

fn main() {
    let user1 = User {
        name: String::from("Alice"),
        age: 30,
    };

    let user2 = User {
        age: 25,
        ..user1  // name is moved from user1
    };

    // println!("{}", user1.name);  // ERROR: name was moved
    println!("{}", user1.age);      // OK: u32 is Copy
    println!("{} {}", user2.name, user2.age);
}
```

## Tuple Structs

When you want a named type but don't need field names:

```rust
struct Color(u8, u8, u8);
struct Meters(f64);
struct Seconds(f64);

fn main() {
    let red = Color(255, 0, 0);
    println!("R:{} G:{} B:{}", red.0, red.1, red.2);

    let distance = Meters(100.0);
    let time = Seconds(9.58);

    // This won't compile — different types even though both wrap f64:
    // let speed = distance + time;  // ERROR: can't add Meters and Seconds

    println!("{}m in {}s", distance.0, time.0);
}
```

Tuple structs are fantastic for the **newtype pattern** — wrapping a primitive to give it domain meaning. `Meters(f64)` and `Seconds(f64)` are different types even though they both contain an `f64`. The compiler won't let you accidentally mix them up. This alone has prevented real bugs in production code I've worked on.

## Unit Structs

Structs with no fields:

```rust
struct Marker;

fn main() {
    let _m = Marker;
}
```

These seem useless, but they're important for trait implementations and type-level programming. You'll see them when we get to traits.

## Deriving Common Traits

Raw structs don't support printing, comparing, or cloning. You opt into these with `derive`:

```rust
#[derive(Debug, Clone, PartialEq)]
struct Point {
    x: f64,
    y: f64,
}

fn main() {
    let p1 = Point { x: 1.0, y: 2.0 };
    let p2 = p1.clone();
    let p3 = Point { x: 3.0, y: 4.0 };

    println!("{:?}", p1);           // Debug formatting
    println!("{:#?}", p1);          // Pretty debug formatting
    println!("equal: {}", p1 == p2); // PartialEq comparison
    println!("equal: {}", p1 == p3);
}
```

The most common derives:
- **`Debug`** — enables `{:?}` formatting. Add this to everything. Always.
- **`Clone`** — enables `.clone()` for explicit deep copies
- **`PartialEq`** / **`Eq`** — enables `==` comparison
- **`Hash`** — enables use as HashMap keys
- **`Default`** — enables `Type::default()` for zero/empty values

```rust
#[derive(Debug, Default)]
struct GameState {
    score: u32,
    level: u32,
    lives: u32,
    paused: bool,
}

fn main() {
    let state = GameState {
        lives: 3,
        ..GameState::default()  // all other fields get defaults (0, false)
    };

    println!("{:#?}", state);
}
```

## Structs and Ownership

Structs own their data. When a struct is dropped, all its fields are dropped:

```rust
#[derive(Debug)]
struct Document {
    title: String,
    body: String,
    word_count: usize,
}

fn create_document(title: &str, body: &str) -> Document {
    let word_count = body.split_whitespace().count();
    Document {
        title: title.to_string(),
        body: body.to_string(),
        word_count,
    }
}

fn print_summary(doc: &Document) {
    println!("'{}' — {} words", doc.title, doc.word_count);
}

fn main() {
    let doc = create_document(
        "Rust Guide",
        "Rust is a systems programming language focused on safety",
    );

    print_summary(&doc);     // borrow — doc still exists
    print_summary(&doc);     // can borrow multiple times
    println!("{:?}", doc);   // still valid
}
```

Notice `print_summary` takes `&Document` — it borrows the document. The function can read all the fields but doesn't take ownership. The `main` function retains ownership and can keep using the document.

## A Practical Example: Building a Todo App Model

```rust
#[derive(Debug, Clone)]
struct Todo {
    id: u32,
    title: String,
    completed: bool,
}

#[derive(Debug)]
struct TodoList {
    items: Vec<Todo>,
    next_id: u32,
}

impl TodoList {
    fn new() -> Self {
        TodoList {
            items: Vec::new(),
            next_id: 1,
        }
    }

    fn add(&mut self, title: &str) -> u32 {
        let id = self.next_id;
        self.items.push(Todo {
            id,
            title: title.to_string(),
            completed: false,
        });
        self.next_id += 1;
        id
    }

    fn complete(&mut self, id: u32) -> bool {
        for item in &mut self.items {
            if item.id == id {
                item.completed = true;
                return true;
            }
        }
        false
    }

    fn pending(&self) -> Vec<&Todo> {
        self.items.iter().filter(|t| !t.completed).collect()
    }

    fn summary(&self) -> String {
        let total = self.items.len();
        let done = self.items.iter().filter(|t| t.completed).count();
        format!("{done}/{total} completed")
    }
}

fn main() {
    let mut list = TodoList::new();

    list.add("Learn ownership");
    list.add("Learn borrowing");
    list.add("Learn structs");
    list.add("Build something cool");

    list.complete(1);
    list.complete(2);

    println!("Status: {}", list.summary());
    println!("\nPending:");
    for todo in list.pending() {
        println!("  [ ] {}: {}", todo.id, todo.title);
    }

    println!("\nAll items: {:#?}", list.items);
}
```

We're using `impl` blocks here to add methods to the struct — that's technically Lesson 14's content, but it's impossible to show practical structs without methods. For now, just notice the pattern: `&self` for reading, `&mut self` for modifying, `Self` for the struct's own type.

## Design Tips

**Keep structs focused.** A struct with 15 fields is a code smell. Break it into smaller, composable structs.

**Use `String` for owned fields, `&str` for borrowed fields** (when lifetimes make sense — we'll cover that later). For beginners, just use `String` in structs. You can optimize later.

**Derive `Debug` on everything.** You'll thank yourself when debugging. There's essentially no reason not to.

**Prefer construction functions.** Instead of letting callers construct your struct directly, provide a `new()` function that ensures invariants. We'll formalize this in Lesson 14.

Next up: enums — arguably the most powerful feature in Rust's type system.
