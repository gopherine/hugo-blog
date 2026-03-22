---
title: "Lesson 9: War Stories — Lessons from real Rust deployments"
author: Atharva Pandey
series: "Rust in Production — Real-World Architecture"
lesson: 9
keywords: [Rust, rust, architecture, production, war stories, debugging, incidents, deployment]
tags: [Rust tutorial, rust, architecture, production]
date: '2025-11-02T15:08:00.000Z'
---

Every language looks great in blog posts. Production is where the truth comes out. I've been running Rust services in production for a few years now, and while I'm convinced it's the right tool for certain problems, I've also hit situations where Rust did something I didn't expect, or where its strengths became weaknesses in surprising ways.

These are real stories. Some names and details are changed, but the bugs and the lessons are exactly as they happened.

## The Memory Leak That Wasn't

We had a Rust service whose memory usage climbed steadily — about 50MB per hour. In any other language, you'd say "memory leak" and start looking for unclosed connections or growing caches. In Rust, memory leaks are supposed to be impossible, right?

Wrong. Rust prevents *use-after-free* and *double-free*. It does not prevent memory leaks. In fact, `std::mem::forget` explicitly exists to leak memory on purpose. And there are subtler ways.

Our culprit was a `tokio::sync::mpsc` channel that was growing without bound. A background worker received tasks through a bounded channel, but the processing was slower than the sending. The sender never checked for backpressure — it just called `.send().await` which blocked until space was available, and we had set the buffer to 100,000.

```rust
// The problematic code
let (tx, mut rx) = tokio::sync::mpsc::channel::<Task>(100_000);

// Producer — fires as fast as events arrive
tokio::spawn(async move {
    while let Some(event) = event_stream.next().await {
        let task = Task::from_event(event);
        tx.send(task).await.unwrap();  // blocks when full, but buffer is huge
    }
});

// Consumer — processes one at a time, slowly
tokio::spawn(async move {
    while let Some(task) = rx.recv().await {
        process_task(task).await;  // takes 50-200ms each
    }
});
```

The channel buffer was acting as an unbounded queue in practice. 100,000 `Task` objects at ~500 bytes each = 50MB sitting in the channel, growing until the service OOM'd.

The fix was embarrassingly simple:

```rust
// Fixed: small buffer + explicit backpressure handling
let (tx, mut rx) = tokio::sync::mpsc::channel::<Task>(100);

tokio::spawn(async move {
    while let Some(event) = event_stream.next().await {
        let task = Task::from_event(event);
        match tx.try_send(task) {
            Ok(()) => {}
            Err(tokio::sync::mpsc::error::TrySendError::Full(task)) => {
                tracing::warn!("task queue full, dropping task {}", task.id);
                metrics::counter!("tasks.dropped").increment(1);
                // Or: push to a dead letter queue, or block with timeout
            }
            Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => {
                tracing::error!("task consumer has shut down");
                break;
            }
        }
    }
});
```

**Lesson:** Rust's ownership model prevents use-after-free, not resource exhaustion. Bounded channels only bound you if the bound is small enough to matter. Treat large channel buffers as unbounded queues in disguise.

## The Deadlock in Async Code

This one was brutal to debug. We had a service that would occasionally stop responding to requests. No crash, no error log, no panic. It just... stopped. CPU at 0%. Memory stable. Health checks passing (they were on a separate thread). But HTTP requests would hang forever.

After two days of staring at tracing output, I found it. Two async tasks held `Mutex` locks across `.await` points:

```rust
// DON'T DO THIS
use std::sync::Mutex;

pub struct OrderService {
    cache: Mutex<HashMap<OrderId, Order>>,
    repo: Arc<dyn OrderRepository>,
}

impl OrderService {
    pub async fn get_or_fetch(&self, id: OrderId) -> Result<Order, Error> {
        // Lock the mutex
        let mut cache = self.cache.lock().unwrap();

        if let Some(order) = cache.get(&id) {
            return Ok(order.clone());
        }

        // THIS IS THE BUG: .await while holding the lock
        let order = self.repo.find_by_id(id).await?;

        cache.insert(id, order.clone());
        Ok(order)
    }
}
```

`std::sync::Mutex` is not async-aware. When task A holds the lock and hits `.await`, it yields to the executor. Task B tries to acquire the lock, blocks (not yields — *blocks*), and if it's on the same executor thread, it prevents task A from ever resuming to release the lock.

Deadlock. Silent. No error message. The service just stops.

The fix:

```rust
// Option 1: Use tokio::sync::Mutex (async-aware)
use tokio::sync::Mutex;

pub struct OrderService {
    cache: Mutex<HashMap<OrderId, Order>>,
    repo: Arc<dyn OrderRepository>,
}

impl OrderService {
    pub async fn get_or_fetch(&self, id: OrderId) -> Result<Order, Error> {
        let mut cache = self.cache.lock().await;  // yields instead of blocking

        if let Some(order) = cache.get(&id) {
            return Ok(order.clone());
        }

        let order = self.repo.find_by_id(id).await?;
        cache.insert(id, order.clone());
        Ok(order)
    }
}

// Option 2 (better): Don't hold any lock across await points
impl OrderService {
    pub async fn get_or_fetch(&self, id: OrderId) -> Result<Order, Error> {
        // Check cache — lock is dropped immediately
        {
            let cache = self.cache.lock().unwrap();
            if let Some(order) = cache.get(&id) {
                return Ok(order.clone());
            }
        }  // lock dropped here

        // Fetch without holding any lock
        let order = self.repo.find_by_id(id).await?;

        // Re-acquire lock to insert
        {
            let mut cache = self.cache.lock().unwrap();
            cache.insert(id, order.clone());
        }

        Ok(order)
    }
}
```

Option 2 is better because `std::sync::Mutex` is faster than `tokio::sync::Mutex` when you're not holding it across await points. The brief window where two tasks might both fetch the same order is usually acceptable.

**Lesson:** Never hold `std::sync::Mutex` across an `.await`. Clippy has a lint for this (`await_holding_lock`), but it's not enabled by default. Add it to your clippy config.

```toml
# clippy.toml
await-holding-lock-timeout = 0
```

## The Serialization Surprise

We had a field migration where we changed a domain type from a plain `String` to a newtype wrapper:

```rust
// Before
#[derive(Serialize, Deserialize)]
struct Event {
    user_id: String,
    action: String,
}

// After
#[derive(Serialize, Deserialize)]
struct Event {
    user_id: UserId,  // newtype wrapper
    action: Action,   // enum
}

#[derive(Serialize, Deserialize)]
struct UserId(String);

#[derive(Serialize, Deserialize)]
enum Action {
    Login,
    Logout,
    Purchase,
}
```

Seemed safe. Both serialize to the same JSON shape, right?

Nope. `UserId(String)` serializes by default as `{"user_id": "abc123"}` — but `Action::Login` serializes as `"Login"`, not `"login"`. And our consumers expected lowercase. Worse, the old events in our event store had `"action": "login"` (lowercase strings), but the new code tried to deserialize them as `Action::Login` (capitalized enum variant) and failed.

We needed:

```rust
#[derive(Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
enum Action {
    Login,
    Logout,
    Purchase,
}
```

And for the newtype to serialize transparently:

```rust
#[derive(Serialize, Deserialize)]
#[serde(transparent)]
struct UserId(String);
```

**Lesson:** When you change types in serialized data, test the serialization format explicitly. Don't assume structural equivalence means serialization equivalence.

```rust
#[test]
fn event_serialization_backwards_compatible() {
    // This is what existing events look like in the store
    let old_format = r#"{"user_id":"abc123","action":"login"}"#;

    // New code MUST be able to parse old format
    let event: Event = serde_json::from_str(old_format).unwrap();
    assert_eq!(event.user_id.0, "abc123");
    assert_eq!(event.action, Action::Login);

    // And re-serialize to the same format
    let reserialized = serde_json::to_string(&event).unwrap();
    assert_eq!(reserialized, old_format);
}
```

## The Compile Time That Killed Deployment

Our CI pipeline took 3 minutes to build and test. Then we added `diesel` for database access, `tonic` for gRPC, and `opentelemetry` for tracing. The build time went to 14 minutes. Our deployment pipeline — build, test, build release, push Docker image — took 28 minutes.

This wasn't a technical crisis, but it was a productivity crisis. Engineers would push a fix and wait half an hour to see if it deployed. Hotfix turnaround went from 10 minutes to 40.

What we did:

**1. Cargo caching in CI.** This alone cut 8 minutes off.

```yaml
- uses: actions/cache@v3
  with:
    path: |
      ~/.cargo/registry
      ~/.cargo/git
      target/
    key: ${{ runner.os }}-cargo-${{ hashFiles('**/Cargo.lock') }}
```

**2. `cargo-chef` for Docker layer caching.** Dependencies are built in a separate layer that only changes when `Cargo.toml` or `Cargo.lock` change.

```dockerfile
FROM rust:1.75 AS chef
RUN cargo install cargo-chef
WORKDIR /app

FROM chef AS planner
COPY . .
RUN cargo chef prepare --recipe-path recipe.json

FROM chef AS builder
COPY --from=planner /app/recipe.json recipe.json
RUN cargo chef cook --release --recipe-path recipe.json
COPY . .
RUN cargo build --release

FROM debian:bookworm-slim
COPY --from=builder /app/target/release/myservice /usr/local/bin/
CMD ["myservice"]
```

**3. Splitting the workspace** (see Lesson 5). Changed code only recompiles the affected crates.

**4. Using `lld` as the linker.** Drop-in replacement that cuts link time by 50-80%.

```toml
# .cargo/config.toml
[target.x86_64-unknown-linux-gnu]
linker = "clang"
rustflags = ["-C", "link-arg=-fuse-ld=lld"]
```

End result: CI build time dropped from 14 minutes to 4. Deployment pipeline from 28 minutes to 11. Still not great, but manageable.

**Lesson:** Track your compile times from day one. Once they're slow, it's much harder to fix because your dependency graph is already tangled.

## The Panic in Production

We had exactly one panic in production across all our Rust services over 18 months. It was an `unwrap()` on a `None` value that "could never happen."

```rust
fn get_primary_warehouse(product: &Product) -> &Warehouse {
    product.warehouses.iter()
        .find(|w| w.is_primary)
        .unwrap()  // "every product has a primary warehouse"
}
```

Except one product didn't. A data migration had created products with empty warehouse lists. The invariant was true in the code but not enforced in the data.

The service panicked, tokio caught it, and the task died. But because this was called from a request handler, the client got a connection reset — no error message, no status code, just a dropped connection. Our monitoring didn't catch it for 45 minutes because the health check endpoint didn't go through this code path.

Fixes:
1. Replace `unwrap()` with proper error handling:
```rust
fn get_primary_warehouse(product: &Product) -> Result<&Warehouse, DomainError> {
    product.warehouses.iter()
        .find(|w| w.is_primary)
        .ok_or(DomainError::NoPrimaryWarehouse(product.id))
}
```

2. Add a panic handler that returns a 500 instead of dropping connections:
```rust
use std::panic::AssertUnwindSafe;
use futures::FutureExt;

pub async fn catch_panics(
    request: Request,
    next: Next,
) -> Response {
    let response = AssertUnwindSafe(next.run(request))
        .catch_unwind()
        .await;

    match response {
        Ok(response) => response,
        Err(panic) => {
            let msg = if let Some(s) = panic.downcast_ref::<&str>() {
                s.to_string()
            } else if let Some(s) = panic.downcast_ref::<String>() {
                s.clone()
            } else {
                "unknown panic".to_string()
            };

            tracing::error!("handler panicked: {}", msg);
            StatusCode::INTERNAL_SERVER_ERROR.into_response()
        }
    }
}
```

3. CI lint: zero `unwrap()` calls outside of tests.

```bash
# scripts/check-unwrap.sh
if grep -rn '\.unwrap()' crates/*/src/ --include='*.rs' | grep -v '#\[cfg(test)\]' | grep -v '// SAFETY:'; then
    echo "ERROR: unwrap() found outside tests without SAFETY comment"
    exit 1
fi
```

**Lesson:** `unwrap()` is `panic!()` in disguise. Every `unwrap()` in production code is a bet that a certain condition will never occur. Sometimes you lose that bet at 3 AM.

## The Trait Object Performance Cliff

We had a data pipeline processing 2 million events per second. It used trait objects for flexibility:

```rust
pub trait Transform: Send + Sync {
    fn apply(&self, event: &mut Event) -> Result<(), TransformError>;
}

pub struct Pipeline {
    transforms: Vec<Box<dyn Transform>>,
}

impl Pipeline {
    pub fn process(&self, event: &mut Event) -> Result<(), PipelineError> {
        for transform in &self.transforms {
            transform.apply(event)?;
        }
        Ok(())
    }
}
```

Performance was fine — until we added more transforms. At 12 transforms per pipeline, we noticed throughput had dropped 30%. The culprit: vtable lookups and the fact that trait objects prevent inlining.

The fix was an enum dispatch pattern:

```rust
pub enum TransformStep {
    Enrich(EnrichTransform),
    Filter(FilterTransform),
    Rename(RenameTransform),
    Aggregate(AggregateTransform),
    Deduplicate(DeduplicateTransform),
}

impl TransformStep {
    #[inline]
    pub fn apply(&self, event: &mut Event) -> Result<(), TransformError> {
        match self {
            Self::Enrich(t) => t.apply(event),
            Self::Filter(t) => t.apply(event),
            Self::Rename(t) => t.apply(event),
            Self::Aggregate(t) => t.apply(event),
            Self::Deduplicate(t) => t.apply(event),
        }
    }
}

pub struct Pipeline {
    transforms: Vec<TransformStep>,
}
```

The match-based dispatch is static — the compiler can inline the transform implementations and optimize the entire pipeline as one unit. Throughput went back up.

**Lesson:** Trait objects are great for flexibility and testing. They're not great for hot loops. If you're processing millions of items, consider enum dispatch for the critical path and keep trait objects for configuration and testing.

## The Takeaway

Rust won't save you from logic bugs, architectural mistakes, or operational negligence. It *will* eliminate entire categories of memory safety issues and data race conditions. The bugs you're left with are higher-level — business logic errors, integration problems, performance tuning.

That's a good trade. The bugs are harder to find (no segfault to point you to the line), but they're fewer, and they're the kind that matter in any language.

Final lesson: when *not* to use Rust — because knowing when to reach for a different tool is the mark of a senior engineer.
