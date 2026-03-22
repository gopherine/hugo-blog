---
title: "Lesson 15: Vec, HashMap, and HashSet — The collections you'll use daily"
author: Atharva Pandey
series: "Rust from Scratch"
lesson: 15
keywords: [Rust, rust, Vec, HashMap, HashSet, collections, vector, hash map, hash set]
tags: [Rust tutorial, rust, beginner]
date: '2024-03-30T11:15:00.000Z'
---

In my experience, about 80% of all data structures in real programs are either lists or key-value maps. Rust nails both of them. `Vec` and `HashMap` are fast, safe, and ergonomic — and once you know these two plus `HashSet`, you can build almost anything.

## Vec — The Dynamic Array

`Vec<T>` is Rust's growable array. It stores elements contiguously on the heap, like `ArrayList` in Java or `std::vector` in C++.

```rust
fn main() {
    // Creating vectors
    let mut v1: Vec<i32> = Vec::new();
    let v2 = vec![1, 2, 3, 4, 5];       // vec! macro
    let v3 = vec![0; 10];                // ten zeros
    let v4: Vec<i32> = (1..=5).collect(); // from iterator

    println!("{:?}", v2);
    println!("{:?}", v3);
    println!("{:?}", v4);

    // Adding elements
    v1.push(10);
    v1.push(20);
    v1.push(30);
    println!("{:?}", v1);
}
```

### Accessing Elements

```rust
fn main() {
    let v = vec![10, 20, 30, 40, 50];

    // Indexing — panics if out of bounds
    let third = v[2];
    println!("Third: {third}");

    // .get() — returns Option, no panic
    match v.get(2) {
        Some(val) => println!("Third: {val}"),
        None => println!("No third element"),
    }

    match v.get(99) {
        Some(val) => println!("99th: {val}"),
        None => println!("No 99th element"),
    }

    // First and last
    println!("First: {:?}", v.first());
    println!("Last: {:?}", v.last());
}
```

Use `.get()` when the index might be out of bounds. Use `[]` when you're certain the index is valid and an out-of-bounds access is a bug worth crashing for. I default to `.get()` in most code.

### Modifying a Vec

```rust
fn main() {
    let mut v = vec![1, 2, 3];

    // Push and pop
    v.push(4);
    let popped = v.pop();  // returns Option<T>
    println!("Popped: {:?}, Vec: {:?}", popped, v);

    // Insert and remove by index
    v.insert(1, 99);       // insert 99 at index 1
    println!("After insert: {:?}", v);

    v.remove(1);           // remove element at index 1
    println!("After remove: {:?}", v);

    // Extend from another collection
    v.extend([10, 20, 30]);
    println!("After extend: {:?}", v);

    // Retain only elements matching a condition
    v.retain(|&x| x > 5);
    println!("After retain >5: {:?}", v);

    // Dedup (remove consecutive duplicates — sort first for all duplicates)
    let mut dupes = vec![1, 1, 2, 3, 3, 3, 2, 1, 1];
    dupes.dedup();
    println!("Dedup: {:?}", dupes);  // [1, 2, 3, 2, 1]

    dupes.sort();
    dupes.dedup();
    println!("Sort + dedup: {:?}", dupes);  // [1, 2, 3]
}
```

### Iterating

```rust
fn main() {
    let v = vec![10, 20, 30, 40, 50];

    // Immutable iteration
    for item in &v {
        print!("{item} ");
    }
    println!();

    // Mutable iteration
    let mut v2 = vec![1, 2, 3, 4, 5];
    for item in &mut v2 {
        *item *= 2;
    }
    println!("Doubled: {:?}", v2);

    // Consuming iteration (moves the vec)
    let v3 = vec![String::from("a"), String::from("b"), String::from("c")];
    for item in v3 {
        println!("Consumed: {item}");
    }
    // v3 is gone now — it was consumed
}
```

The three iteration modes (`&v`, `&mut v`, `v`) correspond to borrowing, mutable borrowing, and ownership transfer. This maps directly to the ownership concepts from Lessons 7 and 8.

### Vec and Capacity

```rust
fn main() {
    let mut v = Vec::new();
    println!("len: {}, capacity: {}", v.len(), v.capacity());

    for i in 0..20 {
        v.push(i);
        println!("pushed {i}: len={}, cap={}", v.len(), v.capacity());
    }
}
```

Vec grows by doubling its capacity when it runs out of space. Each reallocation copies all elements to a new, larger buffer. If you know the approximate size upfront, use `Vec::with_capacity()` to avoid reallocations:

```rust
fn main() {
    let mut v = Vec::with_capacity(1000);
    for i in 0..1000 {
        v.push(i);  // no reallocations — capacity was pre-allocated
    }
    println!("len: {}, capacity: {}", v.len(), v.capacity());
}
```

This is a real performance optimization in hot paths. Not premature — just aware.

---

## HashMap — Key-Value Storage

```rust
use std::collections::HashMap;

fn main() {
    // Creating a HashMap
    let mut scores: HashMap<String, i32> = HashMap::new();

    // Inserting
    scores.insert(String::from("Alice"), 95);
    scores.insert(String::from("Bob"), 87);
    scores.insert(String::from("Charlie"), 92);

    println!("{:?}", scores);

    // Accessing
    let alice_score = scores.get("Alice");
    println!("Alice: {:?}", alice_score);  // Some(95)

    let unknown = scores.get("Dave");
    println!("Dave: {:?}", unknown);  // None

    // get returns Option<&V>
    if let Some(score) = scores.get("Bob") {
        println!("Bob scored {score}");
    }
}
```

`HashMap` is not in the prelude — you need `use std::collections::HashMap`.

### The Entry API

The entry API is how you handle "insert if missing, update if present" patterns. It's one of the most well-designed APIs in the standard library.

```rust
use std::collections::HashMap;

fn main() {
    let mut word_counts: HashMap<String, i32> = HashMap::new();
    let text = "the quick brown fox jumps over the lazy brown dog";

    for word in text.split_whitespace() {
        let count = word_counts.entry(word.to_string()).or_insert(0);
        *count += 1;
    }

    println!("{:#?}", word_counts);
}
```

`entry()` returns an `Entry` enum that lets you manipulate the slot for a key:

```rust
use std::collections::HashMap;

fn main() {
    let mut config: HashMap<String, String> = HashMap::new();

    // or_insert — insert if key doesn't exist
    config.entry("host".to_string()).or_insert("localhost".to_string());
    config.entry("host".to_string()).or_insert("should-not-appear".to_string());

    // or_insert_with — lazy version (closure only called if key missing)
    config.entry("port".to_string()).or_insert_with(|| {
        println!("Computing default port...");
        "8080".to_string()
    });

    // and_modify — modify existing value
    config.entry("host".to_string()).and_modify(|v| {
        v.push_str(":3000");
    });

    println!("{:#?}", config);
}
```

### Iterating Over HashMaps

```rust
use std::collections::HashMap;

fn main() {
    let mut scores = HashMap::new();
    scores.insert("Alice", 95);
    scores.insert("Bob", 87);
    scores.insert("Charlie", 92);

    // Iterate over key-value pairs
    for (name, score) in &scores {
        println!("{name}: {score}");
    }

    // Just keys
    for name in scores.keys() {
        println!("Player: {name}");
    }

    // Just values
    let total: i32 = scores.values().sum();
    let avg = total as f64 / scores.len() as f64;
    println!("Average score: {avg:.1}");
}
```

**Warning**: HashMap iteration order is not guaranteed. If you need ordered keys, use `BTreeMap`.

### Building a HashMap from Iterators

```rust
use std::collections::HashMap;

fn main() {
    // From a vector of tuples
    let data = vec![("one", 1), ("two", 2), ("three", 3)];
    let map: HashMap<&str, i32> = data.into_iter().collect();
    println!("{:?}", map);

    // Zip two vectors
    let keys = vec!["a", "b", "c"];
    let values = vec![1, 2, 3];
    let map: HashMap<&str, i32> = keys.into_iter().zip(values).collect();
    println!("{:?}", map);
}
```

---

## HashSet — Unique Values

A `HashSet` is a `HashMap` where you only care about the keys. It stores unique values with O(1) lookup.

```rust
use std::collections::HashSet;

fn main() {
    let mut languages: HashSet<String> = HashSet::new();

    languages.insert(String::from("Rust"));
    languages.insert(String::from("Go"));
    languages.insert(String::from("Python"));
    languages.insert(String::from("Rust"));  // duplicate — ignored

    println!("Languages: {:?}", languages);
    println!("Count: {}", languages.len());  // 3, not 4
    println!("Contains Rust: {}", languages.contains("Rust"));
}
```

### Set Operations

```rust
use std::collections::HashSet;

fn main() {
    let a: HashSet<i32> = [1, 2, 3, 4, 5].into_iter().collect();
    let b: HashSet<i32> = [3, 4, 5, 6, 7].into_iter().collect();

    // Union — elements in either set
    let union: HashSet<&i32> = a.union(&b).collect();
    println!("Union: {:?}", union);

    // Intersection — elements in both sets
    let intersection: HashSet<&i32> = a.intersection(&b).collect();
    println!("Intersection: {:?}", intersection);

    // Difference — elements in a but not b
    let diff: HashSet<&i32> = a.difference(&b).collect();
    println!("A - B: {:?}", diff);

    // Symmetric difference — elements in one but not both
    let sym_diff: HashSet<&i32> = a.symmetric_difference(&b).collect();
    println!("Sym diff: {:?}", sym_diff);

    // Subset/superset checks
    let c: HashSet<i32> = [1, 2].into_iter().collect();
    println!("c is subset of a: {}", c.is_subset(&a));
}
```

### Practical Use: Deduplication

```rust
use std::collections::HashSet;

fn unique_words(text: &str) -> Vec<&str> {
    let mut seen = HashSet::new();
    let mut result = Vec::new();

    for word in text.split_whitespace() {
        if seen.insert(word) {  // insert returns false if already present
            result.push(word);
        }
    }

    result
}

fn main() {
    let text = "the quick brown fox jumps over the lazy brown dog";
    let unique = unique_words(text);
    println!("Unique words (order preserved): {:?}", unique);
}
```

`HashSet::insert` returns `bool` — `true` if the value was new, `false` if it was already present. This makes the "check and insert" pattern a one-liner.

---

## A Practical Example: Student Grade Tracker

```rust
use std::collections::HashMap;

#[derive(Debug)]
struct GradeBook {
    grades: HashMap<String, Vec<f64>>,
}

impl GradeBook {
    fn new() -> Self {
        GradeBook {
            grades: HashMap::new(),
        }
    }

    fn add_grade(&mut self, student: &str, grade: f64) {
        self.grades
            .entry(student.to_string())
            .or_insert_with(Vec::new)
            .push(grade);
    }

    fn average(&self, student: &str) -> Option<f64> {
        self.grades.get(student).map(|grades| {
            let sum: f64 = grades.iter().sum();
            sum / grades.len() as f64
        })
    }

    fn top_students(&self, n: usize) -> Vec<(&str, f64)> {
        let mut averages: Vec<(&str, f64)> = self.grades
            .iter()
            .filter_map(|(name, grades)| {
                if grades.is_empty() {
                    None
                } else {
                    let avg = grades.iter().sum::<f64>() / grades.len() as f64;
                    Some((name.as_str(), avg))
                }
            })
            .collect();

        averages.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
        averages.truncate(n);
        averages
    }

    fn summary(&self) {
        println!("=== Grade Book ===");
        for (student, grades) in &self.grades {
            let avg = grades.iter().sum::<f64>() / grades.len() as f64;
            println!("  {student}: {:?} (avg: {avg:.1})", grades);
        }
    }
}

fn main() {
    let mut book = GradeBook::new();

    book.add_grade("Alice", 92.0);
    book.add_grade("Alice", 88.0);
    book.add_grade("Alice", 95.0);
    book.add_grade("Bob", 78.0);
    book.add_grade("Bob", 85.0);
    book.add_grade("Charlie", 90.0);
    book.add_grade("Charlie", 93.0);
    book.add_grade("Charlie", 91.0);

    book.summary();

    println!("\nTop 2 students:");
    for (name, avg) in book.top_students(2) {
        println!("  {name}: {avg:.1}");
    }
}
```

## When to Use What

| Collection | Use when... |
|-----------|-------------|
| `Vec<T>` | Ordered list, access by index, most common choice |
| `HashMap<K, V>` | Key-value lookup, counting, grouping |
| `HashSet<T>` | Unique values, membership testing, set operations |
| `BTreeMap<K, V>` | Sorted keys, range queries |
| `BTreeSet<T>` | Sorted unique values |
| `VecDeque<T>` | Queue (push/pop from both ends) |
| `LinkedList<T>` | Almost never — Vec is faster for nearly everything |

That last one is real advice. `LinkedList` has terrible cache performance on modern hardware. Unless you're doing O(1) insertion/removal in the middle (and you know the position), use `Vec`.

Next: error handling with `Result` and the `?` operator. This is where Rust's approach to errors really starts to pay off.
