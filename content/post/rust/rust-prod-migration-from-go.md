---
title: "Lesson 8: Migrating Services from Go/Python/Java to Rust — When and how"
author: Atharva Pandey
series: "Rust in Production — Real-World Architecture"
lesson: 8
keywords: [Rust, rust, architecture, production, migration, Go, Python, Java, rewrite, strangler fig]
tags: [Rust tutorial, rust, architecture, production]
date: '2025-10-30T09:33:00.000Z'
---

I've been involved in three Rust migrations. One from Python, one from Go, one from Java. Two were successes. One was a disaster that got cancelled six months in after burning a quarter of the team's roadmap capacity.

The failed one wasn't a technical failure — the Rust code was fine. It failed because we rewrote the wrong service, at the wrong time, for the wrong reasons. "Rust is faster" was the entire justification. Nobody had measured whether speed was actually the bottleneck.

Let me save you that lesson.

## When to Migrate (and When Not To)

Migrate to Rust when:

- **Latency or throughput is a measured bottleneck** and your current language is the limiting factor. Not the database. Not the network. The language runtime.
- **Memory usage is unsustainable.** A Java service using 4GB of heap for what Rust does in 200MB — and you're running hundreds of instances.
- **You need predictable performance.** GC pauses in Java/Go are killing your p99 latency. You need sub-millisecond consistency.
- **Safety-critical code.** Financial calculations, data pipeline correctness, anything where a race condition or null pointer means real money lost.
- **You're already rewriting anyway.** If the service needs a major refactor, adding a language change is incremental effort.

Don't migrate when:

- **"Rust is fast" is the only reason.** Go is fast enough for most web services. Python with the right architecture is fast enough for most web services.
- **Your team doesn't know Rust.** Training 8 engineers while maintaining a production service is brutal. Start with a new, non-critical service.
- **The service is stable and rarely changes.** Don't rewrite something that works. Seriously. I don't care how ugly the Python code is.
- **Time-to-market matters more than performance.** Rust's compile-time guarantees have a development speed cost. If you need to ship features every two weeks and performance is "good enough," stay where you are.

## The Strangler Fig Pattern

Don't rewrite everything at once. You'll be maintaining two systems for months with no value delivered. Instead, strangle the old system piece by piece.

```
                    ┌──────────────┐
                    │   Load       │
                    │   Balancer   │
                    └──────┬───────┘
                           │
                    ┌──────┴───────┐
              ┌─────┤   Router     ├─────┐
              │     └──────────────┘     │
              ▼                          ▼
    ┌──────────────┐          ┌──────────────┐
    │  Go Service  │          │ Rust Service  │
    │  (shrinking) │          │  (growing)    │
    │              │          │               │
    │  /users ✓    │          │  /orders ✓    │
    │  /reports ✓  │          │  /inventory ✓ │
    │              │          │  /payments ✓  │
    └──────┬───────┘          └──────┬───────┘
           │                         │
           └─────────┬───────────────┘
                     │
              ┌──────┴───────┐
              │   Database   │
              │  (shared)    │
              └──────────────┘
```

The router sends traffic endpoint by endpoint. Start with the lowest-risk, highest-value endpoint. Get it running in Rust. Monitor it for a week. Then move the next one.

Here's a simple reverse proxy router in Go that manages the cutover:

```go
// This stays in your Go service during migration
func routeHandler(goMux, rustProxy http.Handler) http.HandlerFunc {
    // Endpoints that have been migrated to Rust
    rustEndpoints := map[string]bool{
        "/api/v1/orders":    true,
        "/api/v1/inventory": true,
        "/api/v1/payments":  true,
    }

    return func(w http.ResponseWriter, r *http.Request) {
        for prefix := range rustEndpoints {
            if strings.HasPrefix(r.URL.Path, prefix) {
                rustProxy.ServeHTTP(w, r)
                return
            }
        }
        goMux.ServeHTTP(w, r)
    }
}
```

Or do it at the load balancer level with nginx path-based routing:

```nginx
upstream go_service {
    server go-app:8080;
}

upstream rust_service {
    server rust-app:8080;
}

server {
    # Migrated endpoints go to Rust
    location /api/v1/orders {
        proxy_pass http://rust_service;
    }

    location /api/v1/inventory {
        proxy_pass http://rust_service;
    }

    # Everything else stays on Go
    location / {
        proxy_pass http://go_service;
    }
}
```

## Translating Go Patterns to Rust

I've migrated the most services from Go to Rust, so let me be specific about the translation.

### Error Handling

Go:
```go
func GetUser(id string) (*User, error) {
    user, err := db.FindUser(id)
    if err != nil {
        return nil, fmt.Errorf("finding user %s: %w", id, err)
    }
    if user == nil {
        return nil, ErrNotFound
    }
    return user, nil
}
```

Rust:
```rust
pub async fn get_user(id: &UserId) -> Result<User, UserError> {
    let user = db.find_user(id)
        .await
        .map_err(|e| UserError::Database(format!("finding user {}: {}", id, e)))?;

    user.ok_or(UserError::NotFound(*id))
}
```

The `?` operator replaces `if err != nil`. The `Option`/`Result` types replace the `(value, error)` tuple. You lose the ability to forget to check errors — Rust makes it a compile error.

### Concurrency

Go:
```go
func processOrders(ctx context.Context, orders []Order) error {
    g, ctx := errgroup.WithContext(ctx)

    for _, order := range orders {
        order := order  // capture for goroutine
        g.Go(func() error {
            return processOrder(ctx, order)
        })
    }

    return g.Wait()
}
```

Rust:
```rust
use futures::future::try_join_all;

pub async fn process_orders(orders: Vec<Order>) -> Result<(), ProcessError> {
    let futures: Vec<_> = orders
        .into_iter()
        .map(|order| async move {
            process_order(&order).await
        })
        .collect();

    try_join_all(futures).await?;
    Ok(())
}
```

Or with `tokio::JoinSet` for more control:

```rust
use tokio::task::JoinSet;

pub async fn process_orders_with_limit(
    orders: Vec<Order>,
    concurrency: usize,
) -> Result<Vec<ProcessResult>, ProcessError> {
    let semaphore = Arc::new(tokio::sync::Semaphore::new(concurrency));
    let mut set = JoinSet::new();

    for order in orders {
        let permit = semaphore.clone().acquire_owned().await.unwrap();
        set.spawn(async move {
            let result = process_order(&order).await;
            drop(permit);
            result
        });
    }

    let mut results = Vec::new();
    while let Some(result) = set.join_next().await {
        results.push(result??);
    }

    Ok(results)
}
```

### Interfaces → Traits

Go:
```go
type UserRepository interface {
    FindByID(ctx context.Context, id string) (*User, error)
    Save(ctx context.Context, user *User) error
}
```

Rust:
```rust
#[async_trait]
pub trait UserRepository: Send + Sync {
    async fn find_by_id(&self, id: &UserId) -> Result<Option<User>, RepoError>;
    async fn save(&self, user: &User) -> Result<(), RepoError>;
}
```

Almost the same — but Rust's traits give you generics, associated types, and default implementations. Much more powerful than Go interfaces, at the cost of more syntax.

### Channels → Tokio Channels

Go:
```go
ch := make(chan Order, 100)

go func() {
    for order := range ch {
        process(order)
    }
}()

ch <- newOrder
close(ch)
```

Rust:
```rust
let (tx, mut rx) = tokio::sync::mpsc::channel::<Order>(100);

tokio::spawn(async move {
    while let Some(order) = rx.recv().await {
        process(&order).await;
    }
});

tx.send(new_order).await?;
drop(tx); // closing the channel
```

## Translating Python Patterns

Python migrations are usually more dramatic — you're going from dynamic typing to static typing, from interpreted to compiled. The payoff is bigger, but so is the effort.

### Data Classes → Structs

Python:
```python
@dataclass
class User:
    id: str
    email: str
    name: str
    role: str = "user"
    created_at: datetime = field(default_factory=datetime.utcnow)
```

Rust:
```rust
#[derive(Debug, Clone)]
pub struct User {
    id: UserId,
    email: Email,        // validated, not just a string
    name: String,
    role: Role,          // enum, not just a string
    created_at: DateTime<Utc>,
}
```

The Rust version forces you to make decisions the Python version deferred — what's a valid email? What roles exist? These decisions surface bugs that Python would discover at runtime (or not at all).

### Exception Handling → Result Types

Python:
```python
def transfer_funds(from_id, to_id, amount):
    try:
        from_account = get_account(from_id)
        to_account = get_account(to_id)

        if from_account.balance < amount:
            raise InsufficientFundsError(from_id, amount)

        from_account.balance -= amount
        to_account.balance += amount

        save_account(from_account)
        save_account(to_account)

    except AccountNotFoundError as e:
        logger.error(f"Account not found: {e}")
        raise
    except DatabaseError as e:
        logger.error(f"DB error during transfer: {e}")
        raise TransferError(f"Failed to transfer: {e}")
```

Rust:
```rust
pub async fn transfer_funds(
    from_id: AccountId,
    to_id: AccountId,
    amount: Money,
) -> Result<TransferReceipt, TransferError> {
    let mut from = self.repo.find_account(from_id).await?
        .ok_or(TransferError::AccountNotFound(from_id))?;

    let mut to = self.repo.find_account(to_id).await?
        .ok_or(TransferError::AccountNotFound(to_id))?;

    from.debit(amount)?;   // returns Err if insufficient funds
    to.credit(amount)?;    // returns Err on overflow

    self.repo.save_both(&from, &to).await?;

    Ok(TransferReceipt {
        from_id,
        to_id,
        amount,
        timestamp: Utc::now(),
    })
}
```

Every error is explicit. No uncaught exceptions. No mystery crashes at 3 AM because someone called a function without a try/except.

## The Migration Checklist

Here's the process I follow for every migration, regardless of the source language:

### Phase 1: Measure (1-2 weeks)
- Profile the existing service. What's actually slow? What uses the most memory?
- Identify the specific endpoints or code paths that benefit from Rust.
- Write down success criteria. "P99 latency under 10ms." "Memory usage under 500MB." Concrete numbers.

### Phase 2: Scaffold (1 week)
- Set up the Rust project structure (see Lesson 1).
- Define domain types and ports (see Lessons 2-3).
- Get CI/CD working: build, test, Docker image, deployment.
- Deploy a health check endpoint. Prove the infrastructure works before writing business logic.

### Phase 3: Parallel Implementation (2-4 weeks per endpoint)
- Implement one endpoint at a time in Rust.
- Run both old and new implementations in parallel.
- Compare responses — they should be byte-identical for the same inputs.
- Shadow traffic: send production requests to both, compare, but only return the original response.

```rust
// Shadow testing middleware
pub async fn shadow_test(
    State(state): State<AppState>,
    request: Request,
) -> Response {
    let body_bytes = request.body_bytes().await;
    let cloned_request = rebuild_request(&request, &body_bytes);

    // Send to new implementation, don't wait
    let new_impl = state.new_service.clone();
    tokio::spawn(async move {
        match new_impl.handle(cloned_request).await {
            Ok(new_response) => {
                // Compare with expected response
                // Log differences for analysis
                tracing::info!(
                    match_status = %responses_match,
                    "shadow test result"
                );
            }
            Err(e) => {
                tracing::error!("shadow test failed: {}", e);
            }
        }
    });

    // Return response from old implementation
    state.old_service.handle(request).await
}
```

### Phase 4: Cutover (1 week per endpoint)
- Route 1% of traffic to Rust. Monitor.
- Route 10%. Monitor.
- Route 50%. Monitor.
- Route 100%. Keep the old service running for a week as rollback.
- Decommission the old endpoint.

### Phase 5: Cleanup
- Remove the routing logic.
- Archive the old code.
- Update documentation.
- Celebrate — responsibly.

## What Goes Wrong

Every migration I've seen hit at least one of these:

**The "just one more thing" trap.** You're migrating the order service and someone says "while we're at it, let's also fix the pricing logic." No. Migrate first, improve later. A migration should produce *identical behavior* in a different language, not new features.

**Underestimating the ecosystem gap.** That Python library for parsing CSV with 47 different encodings? There might not be a Rust equivalent. You'll end up writing it yourself or finding a less mature crate. Budget time for this.

**Team velocity drop.** Engineers who are fluent in Go will be slow in Rust for the first 2-3 months. If you're migrating during a critical feature push, you'll feel the pain. Time it right.

**Shared database mutations.** If both the old and new service write to the same database, you need to be incredibly careful about race conditions and schema changes. Prefer having one service own writes and the other only read.

## The Honest Math

A migration is worth it when:

```
(Performance gain × Number of instances × Monthly cost per instance × Months)
+ (Reduction in incidents × Cost per incident)
+ (Developer productivity gain from better type safety)
> (Migration effort in engineer-months × Cost per engineer-month)
+ (Velocity loss during learning curve)
```

Most of these numbers are estimates. That's fine. The point is to make the case explicitly rather than going on vibes.

In my experience: migrating a hot-path data pipeline from Python to Rust is almost always worth it. Migrating a CRUD web service from Go to Rust almost never is. Everything in between requires the math.

Coming up next: real war stories from Rust deployments — the bugs, the surprises, and the lessons that only come from running Rust in production.
