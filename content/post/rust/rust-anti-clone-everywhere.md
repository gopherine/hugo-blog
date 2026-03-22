---
title: "Lesson 1: .clone() Everywhere — Hiding ownership problems"
author: Atharva Pandey
series: "Rust Anti-Patterns & Code Smells"
lesson: 1
keywords: [Rust, rust, anti-patterns, code quality, clone, ownership, borrowing, performance]
tags: [Rust tutorial, rust, anti-patterns, code-quality]
date: '2025-04-05T10:22:00.000Z'
---

I was reviewing a pull request last year from a developer who'd been writing Rust for about three months. The code compiled. The tests passed. Everything looked fine — until I ran `grep -c '\.clone()' src/` and got back a number that made me physically uncomfortable. Forty-seven clones across six files. The codebase was a data pipeline processing millions of events per hour, and this person had turned every ownership error into a `.clone()` call until the compiler stopped yelling.

Here's the thing: `.clone()` is not inherently evil. It's a legitimate tool. But when you reach for it every time the borrow checker pushes back, you're not solving the problem — you're paying to ignore it.

## The Smell

You know you've got a clone problem when your code looks like this:

```rust
fn process_order(order: Order, inventory: &mut Inventory) -> Receipt {
    let customer = order.customer.clone();
    let items = order.items.clone();
    let shipping_address = order.shipping_address.clone();

    validate_customer(&customer);

    let available_items = check_availability(&items, inventory);
    let total = calculate_total(&available_items);

    let receipt = Receipt {
        customer: customer.clone(), // cloning the clone!
        items: available_items.clone(),
        total,
        address: shipping_address.clone(),
    };

    update_inventory(inventory, &items);
    send_confirmation(&customer, &receipt);

    receipt
}
```

Count the clones. Seven. And most of them exist because the developer didn't think about borrowing. They hit a "value moved here" error, slapped `.clone()` on it, and moved on.

This pattern is especially common in three scenarios:

1. **New Rustaceans coming from GC'd languages.** In Python or Java, you don't think about who "owns" a string. You just pass it around. When Rust says you can't, `.clone()` feels like the natural escape hatch.

2. **Complex struct hierarchies.** When you've got nested structs with `String` fields, moving one piece means you can't use the parent anymore. Cloning the fields individually feels easier than restructuring.

3. **Closure captures.** The moment you try to use a value both inside and outside a closure, the borrow checker intervenes, and `.clone()` becomes the default answer.

## Why It's Actually Bad

"But wait," you say, "if it compiles and works, what's the problem?"

A few things.

**Performance death by a thousand cuts.** Each `.clone()` on a `String` allocates new heap memory and copies every byte. On a `Vec<T>`, it clones every element. On a `HashMap`, it rehashes everything. In a hot path, you're burning CPU and memory on copies that shouldn't exist. I've seen `.clone()` abuse turn a 2ms request into a 15ms request. On a single call that doesn't sound catastrophic — at 10,000 requests per second, you just lit money on fire.

**It hides design problems.** If you need seven clones to get a function to compile, your data flow is wrong. The ownership model is trying to tell you something about your architecture, and you're sticking your fingers in your ears.

**It makes refactoring harder.** When you clone everything, the compiler can't help you track data flow. One of Rust's superpowers is that the borrow checker acts as a design assistant — it surfaces when data is being shared in weird ways. Cloning neutralizes that superpower.

## The Fix

Let's rewrite that function properly.

### Step 1: Use references where you're just reading

Most of those clones exist because the function takes ownership of `Order` but then needs to pass pieces around. If the helper functions only need to *read* the data, pass references:

```rust
fn process_order(order: &Order, inventory: &mut Inventory) -> Receipt {
    validate_customer(&order.customer);

    let available_items = check_availability(&order.items, inventory);
    let total = calculate_total(&available_items);

    update_inventory(inventory, &order.items);
    send_confirmation(&order.customer, &order.shipping_address, total);

    Receipt {
        customer: order.customer.clone(), // one clone — we need an owned value for the receipt
        items: available_items,
        total,
        address: order.shipping_address.clone(),
    }
}
```

We went from seven clones to two. And those two are legitimate — the `Receipt` needs to own its data because it'll outlive the borrow of `order`.

### Step 2: Move instead of clone when you're done with the original

If `process_order` consumes the order (caller doesn't need it after), we can move fields out:

```rust
fn process_order(order: Order, inventory: &mut Inventory) -> Receipt {
    validate_customer(&order.customer);

    let available_items = check_availability(&order.items, inventory);
    let total = calculate_total(&available_items);

    update_inventory(inventory, &order.items);
    send_confirmation(&order.customer, &order.shipping_address, total);

    // Destructure to move fields out — zero clones
    Receipt {
        customer: order.customer,
        items: available_items,
        total,
        address: order.shipping_address,
    }
}
```

Zero clones. The trick is to do all your borrowing *before* you move the fields out. Read-then-move is the pattern.

### Step 3: Use `Cow` for "maybe owned, maybe borrowed"

Sometimes you genuinely don't know at compile time whether you'll need an owned value. That's what `Cow` (Clone on Write) is for:

```rust
use std::borrow::Cow;

fn normalize_name(name: &str) -> Cow<'_, str> {
    if name.contains('\t') || name.contains('\n') {
        // Only allocate if we actually need to modify
        Cow::Owned(name.replace(['\t', '\n'], " "))
    } else {
        // No allocation — just borrow
        Cow::Borrowed(name)
    }
}
```

This is elegant. You only pay for the allocation when you actually need to modify the string. Compare this to the clone-happy version:

```rust
// Bad: always allocates, even when unnecessary
fn normalize_name(name: &str) -> String {
    let owned = name.to_string(); // unnecessary clone
    if owned.contains('\t') || owned.contains('\n') {
        owned.replace(['\t', '\n'], " ")
    } else {
        owned // already paid for the allocation
    }
}
```

### Step 4: Use `Rc` or `Arc` for shared ownership

When multiple parts of your code genuinely need to own the same data, use reference-counted pointers instead of cloning the data itself:

```rust
use std::sync::Arc;

struct Config {
    database_url: String,
    api_key: String,
    // ... 20 more fields
}

// Bad: cloning a big struct for every handler
fn setup_handlers(config: Config) {
    let handler_a = HandlerA { config: config.clone() };
    let handler_b = HandlerB { config: config.clone() };
    let handler_c = HandlerC { config: config };
}

// Good: share ownership via Arc — cloning Arc is just incrementing a counter
fn setup_handlers(config: Config) {
    let config = Arc::new(config);
    let handler_a = HandlerA { config: Arc::clone(&config) };
    let handler_b = HandlerB { config: Arc::clone(&config) };
    let handler_c = HandlerC { config };
}
```

Cloning an `Arc` is an atomic increment — constant time, no allocation, no copying. Cloning the `Config` struct copies every field, every `String`, everything.

## When Clone Is Actually Fine

I don't want to leave you with the impression that `.clone()` is always wrong. It's not. Here are cases where it's perfectly reasonable:

- **Small `Copy` types wrapped in newtypes.** Cloning an `i32` is free. If your newtype wraps a small value, clone away.
- **Prototyping.** When you're exploring an idea and don't care about performance yet, clone to get things working, then come back and optimize.
- **Infrequent operations.** If something runs once at startup, nobody cares about one extra string allocation.
- **When the alternative is `unsafe`.** If the only way to avoid a clone is reaching for `unsafe`, take the clone. Always.

The rule of thumb: if you're cloning in a hot path, or if you're cloning to make the compiler stop complaining without understanding *why* it's complaining, stop. Think about the ownership flow. The compiler is trying to teach you something.

## A Quick Diagnostic

Next time you're tempted to add `.clone()`, ask yourself these three questions:

1. **Can I pass a reference instead?** Most functions don't need to own data — they just need to read it.
2. **Am I done with this value?** If so, move it instead of cloning.
3. **Do multiple things need to own this?** Use `Rc`/`Arc`, not repeated deep copies.

If the answer to all three is "no, I really do need a fresh owned copy here," then clone. But in my experience, about 80% of clones I see in code reviews fail at least one of these questions.

The borrow checker isn't your enemy. It's a design tool. Stop fighting it with `.clone()` and start listening to what it's telling you about your data flow.
