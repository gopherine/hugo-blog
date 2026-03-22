---
title: "Lesson 3: Observer Pattern — Channels and callbacks in Rust"
author: Atharva Pandey
series: "Rust Design Patterns"
lesson: 3
keywords: [Rust, rust, design patterns, observer pattern, channels, callbacks, event-driven, mpsc]
tags: [Rust tutorial, rust, design-patterns, architecture]
date: '2025-10-05T11:45:00.000Z'
---

The Observer pattern is where Rust's ownership model gets *really* opinionated. In C# or Java, Observer is simple — maintain a list of listeners, call a method on each when something happens. But "a list of mutable references to objects that can be called at any time" is basically everything Rust's borrow checker exists to prevent. So how do you do event-driven programming in Rust? You have more options than you'd think, and some of them are better than what OOP languages offer.

## The Problem with Traditional Observer in Rust

Let me show you what you *can't* do, so you understand why Rust's approach is different:

```rust
// This is what Observer looks like in Java/C#, ported naively.
// It DOES NOT compile.

trait Listener {
    fn on_event(&mut self, data: &str);
}

struct EventEmitter {
    listeners: Vec<&mut dyn Listener>, // Nope. Can't hold mutable refs.
}
```

You can't store mutable references in a struct because that would mean the `EventEmitter` holds exclusive access to every listener *forever*. No one else could use those listeners. The borrow checker is right to reject this — in a language without these checks, this pattern is the source of use-after-free bugs, dangling pointers, and iterator invalidation when a listener unsubscribes itself during notification.

So what do we do instead?

## Approach 1: Owned Trait Objects

The simplest Rust observer uses owned trait objects — `Box<dyn Listener>`:

```rust
pub trait EventListener {
    fn on_event(&mut self, event: &Event);
}

pub struct Event {
    pub kind: String,
    pub payload: String,
}

pub struct EventBus {
    listeners: Vec<Box<dyn EventListener>>,
}

impl EventBus {
    pub fn new() -> Self {
        Self {
            listeners: Vec::new(),
        }
    }

    pub fn subscribe(&mut self, listener: Box<dyn EventListener>) {
        self.listeners.push(listener);
    }

    pub fn emit(&mut self, event: &Event) {
        for listener in &mut self.listeners {
            listener.on_event(event);
        }
    }
}

// Example listeners
struct Logger;

impl EventListener for Logger {
    fn on_event(&mut self, event: &Event) {
        println!("[LOG] {}: {}", event.kind, event.payload);
    }
}

struct MetricsCollector {
    event_count: usize,
}

impl EventListener for MetricsCollector {
    fn on_event(&mut self, event: &Event) {
        self.event_count += 1;
        println!("[METRICS] Total events: {}", self.event_count);
    }
}

fn main() {
    let mut bus = EventBus::new();
    bus.subscribe(Box::new(Logger));
    bus.subscribe(Box::new(MetricsCollector { event_count: 0 }));

    bus.emit(&Event {
        kind: "user.signup".into(),
        payload: "user_123".into(),
    });
}
```

This works, but the `EventBus` *owns* the listeners. You can't access `MetricsCollector` from outside to read its count. The listeners are consumed by the bus.

## Approach 2: Callback Closures

For many use cases, closures are more ergonomic than defining listener structs:

```rust
type Callback = Box<dyn FnMut(&Event)>;

pub struct CallbackBus {
    listeners: Vec<(String, Callback)>,
}

impl CallbackBus {
    pub fn new() -> Self {
        Self {
            listeners: Vec::new(),
        }
    }

    pub fn on(&mut self, event_kind: &str, callback: impl FnMut(&Event) + 'static) {
        self.listeners
            .push((event_kind.to_string(), Box::new(callback)));
    }

    pub fn emit(&mut self, event: &Event) {
        for (kind, callback) in &mut self.listeners {
            if kind == &event_kind || kind == "*" {
                callback(event);
            }
        }
    }
}
```

Wait — that won't compile. I used `event_kind` instead of `event.kind`. Let me fix that, because this is exactly the kind of mistake you make when you're thinking in terms of JavaScript event emitters:

```rust
pub struct CallbackBus {
    listeners: Vec<(String, Box<dyn FnMut(&Event)>)>,
}

impl CallbackBus {
    pub fn new() -> Self {
        Self {
            listeners: Vec::new(),
        }
    }

    pub fn on(&mut self, event_kind: &str, callback: impl FnMut(&Event) + 'static) {
        self.listeners
            .push((event_kind.to_string(), Box::new(callback)));
    }

    pub fn emit(&mut self, event: &Event) {
        for (kind, callback) in &mut self.listeners {
            if *kind == event.kind || kind == "*" {
                callback(event);
            }
        }
    }
}

fn main() {
    let mut bus = CallbackBus::new();

    bus.on("user.signup", |event| {
        println!("New user: {}", event.payload);
    });

    let mut count = 0u64;
    bus.on("*", move |_event| {
        count += 1;
        println!("Events processed: {}", count);
    });

    bus.emit(&Event {
        kind: "user.signup".into(),
        payload: "user_456".into(),
    });
}
```

The `move` keyword on the second closure moves `count` into the closure's environment. The closure owns it. This is Rust's answer to "how do I give a callback its own state?" — you capture it.

## Approach 3: Channels (The Rust Way)

Here's where Rust really shines. Instead of callbacks — which create complex ownership relationships — use channels. This is arguably the most Rust-idiomatic approach to Observer:

```rust
use std::sync::mpsc;
use std::thread;

#[derive(Debug, Clone)]
pub enum SystemEvent {
    UserSignup { user_id: String },
    OrderPlaced { order_id: String, total: f64 },
    Error { message: String },
}

pub struct EventPublisher {
    subscribers: Vec<mpsc::Sender<SystemEvent>>,
}

impl EventPublisher {
    pub fn new() -> Self {
        Self {
            subscribers: Vec::new(),
        }
    }

    pub fn subscribe(&mut self) -> mpsc::Receiver<SystemEvent> {
        let (tx, rx) = mpsc::channel();
        self.subscribers.push(tx);
        rx
    }

    pub fn publish(&self, event: SystemEvent) {
        // Remove dead subscribers (receiver dropped)
        self.subscribers.iter().for_each(|tx| {
            let _ = tx.send(event.clone());
        });
    }
}

fn main() {
    let mut publisher = EventPublisher::new();

    // Each subscriber gets its own channel
    let analytics_rx = publisher.subscribe();
    let notifications_rx = publisher.subscribe();

    // Subscribers run in their own threads
    let analytics = thread::spawn(move || {
        while let Ok(event) = analytics_rx.recv() {
            match &event {
                SystemEvent::UserSignup { user_id } => {
                    println!("[Analytics] Tracking signup: {}", user_id);
                }
                SystemEvent::OrderPlaced { order_id, total } => {
                    println!("[Analytics] Revenue: ${:.2} from {}", total, order_id);
                }
                _ => {}
            }
        }
        println!("[Analytics] Shutting down");
    });

    let notifications = thread::spawn(move || {
        while let Ok(event) = notifications_rx.recv() {
            if let SystemEvent::Error { message } = &event {
                println!("[Alert] ERROR: {}", message);
            }
        }
        println!("[Notifications] Shutting down");
    });

    // Publish some events
    publisher.publish(SystemEvent::UserSignup {
        user_id: "usr_abc".into(),
    });
    publisher.publish(SystemEvent::OrderPlaced {
        order_id: "ord_123".into(),
        total: 99.99,
    });
    publisher.publish(SystemEvent::Error {
        message: "Payment gateway timeout".into(),
    });

    // Drop publisher to close channels
    drop(publisher);

    analytics.join().unwrap();
    notifications.join().unwrap();
}
```

This is fundamentally different from the callback approach, and it's better in almost every way:

1. **No shared mutable state.** Each subscriber owns its receiver. The publisher owns the senders. No `Rc<RefCell<>>` gymnastics.
2. **Natural concurrency.** Subscribers process events in their own threads at their own pace.
3. **Backpressure.** If a subscriber is slow, the channel buffers events. Swap to `sync_channel` for bounded buffers.
4. **Clean shutdown.** Drop the publisher, channels close, subscribers exit their loops.

## Approach 4: Async Channels with Tokio

For async code, `tokio::sync::broadcast` is the go-to. It's like `mpsc` but every subscriber gets every message:

```rust
use tokio::sync::broadcast;

#[derive(Debug, Clone)]
pub enum AppEvent {
    CacheInvalidated { key: String },
    ConfigReloaded,
}

pub struct AsyncEventBus {
    sender: broadcast::Sender<AppEvent>,
}

impl AsyncEventBus {
    pub fn new(capacity: usize) -> Self {
        let (sender, _) = broadcast::channel(capacity);
        Self { sender }
    }

    pub fn subscribe(&self) -> broadcast::Receiver<AppEvent> {
        self.sender.subscribe()
    }

    pub fn publish(&self, event: AppEvent) {
        // Returns Err if no active receivers — that's fine
        let _ = self.sender.send(event);
    }
}

async fn cache_listener(mut rx: broadcast::Receiver<AppEvent>) {
    loop {
        match rx.recv().await {
            Ok(AppEvent::CacheInvalidated { key }) => {
                println!("Refreshing cache for key: {}", key);
            }
            Ok(AppEvent::ConfigReloaded) => {
                println!("Reloading cache configuration");
            }
            Err(broadcast::error::RecvError::Closed) => break,
            Err(broadcast::error::RecvError::Lagged(n)) => {
                println!("Warning: missed {} events", n);
            }
        }
    }
}
```

The `broadcast` channel even tells you when a slow subscriber misses events (`RecvError::Lagged`). That's observability built into the pattern — something you'd have to build yourself in most languages.

## Shared State with Arc and Mutex

Sometimes you genuinely need shared state between the publisher and subscribers. Maybe you want to unsubscribe dynamically, or check how many subscribers exist. Here's the pattern:

```rust
use std::sync::{Arc, Mutex};
use std::collections::HashMap;

type ListenerId = u64;
type Handler = Box<dyn Fn(&str) + Send + Sync>;

pub struct SharedEventBus {
    next_id: Mutex<ListenerId>,
    handlers: Arc<Mutex<HashMap<ListenerId, Handler>>>,
}

impl SharedEventBus {
    pub fn new() -> Self {
        Self {
            next_id: Mutex::new(0),
            handlers: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub fn subscribe(&self, handler: impl Fn(&str) + Send + Sync + 'static) -> ListenerId {
        let mut id = self.next_id.lock().unwrap();
        let listener_id = *id;
        *id += 1;

        self.handlers
            .lock()
            .unwrap()
            .insert(listener_id, Box::new(handler));

        listener_id
    }

    pub fn unsubscribe(&self, id: ListenerId) -> bool {
        self.handlers.lock().unwrap().remove(&id).is_some()
    }

    pub fn emit(&self, message: &str) {
        let handlers = self.handlers.lock().unwrap();
        for handler in handlers.values() {
            handler(message);
        }
    }
}
```

Note the `Fn` (not `FnMut`) — since we're behind a `Mutex`, we can't guarantee exclusive access to each handler. If your handlers need mutable state, they'll need their own interior mutability (`Mutex`, `AtomicU64`, etc).

## Which Approach to Pick

Here's my rule of thumb:

- **Single-threaded, simple events**: Callback closures. Least ceremony.
- **Multi-threaded, decoupled systems**: Channels. Always channels.
- **Async runtime**: `tokio::sync::broadcast` or `watch` (for latest-value semantics).
- **Dynamic subscribe/unsubscribe**: `Arc<Mutex<HashMap>>` with IDs.
- **Never**: `Rc<RefCell<Vec<>>>` spaghetti. Just... don't.

The Observer pattern in Rust pushes you toward message-passing over shared state, and that's a *good thing*. Your code ends up more concurrent, more testable, and less prone to the notification-ordering bugs that plague callback-heavy codebases.
