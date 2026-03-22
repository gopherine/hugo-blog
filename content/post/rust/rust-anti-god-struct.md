---
title: "Lesson 4: God Structs — When types do too much"
author: Atharva Pandey
series: "Rust Anti-Patterns & Code Smells"
lesson: 4
keywords: [Rust, rust, anti-patterns, code quality, god struct, single responsibility, decomposition, refactoring]
tags: [Rust tutorial, rust, anti-patterns, code-quality]
date: '2025-04-11T16:10:00.000Z'
---

I once opened a file called `app.rs` and found a struct with forty-two fields. Forty-two. It held the database connection, the HTTP client, the cache handle, the logger, the config, the metrics collector, the rate limiter, the auth provider, the feature flags, the email sender, the template engine, and about thirty other things I've blocked from memory. Every function in the codebase took `&self` on this monster. Need to send an email? You need the god struct. Parse a config value? God struct. Log a message? Believe it or not, god struct.

The worst part wasn't the struct itself — it was that testing anything required constructing the entire universe. Want to test the email formatter? Better mock up a database connection, an HTTP client, and a rate limiter too, because the formatter method lives on the same struct that holds all of them.

## The Smell

A god struct is any struct that accumulates responsibilities until it becomes the gravity well of your entire application. Here's a realistic example:

```rust
struct Application {
    // Database
    db_pool: PgPool,
    redis: RedisClient,

    // HTTP
    http_client: reqwest::Client,
    base_url: String,
    api_key: String,

    // Auth
    jwt_secret: Vec<u8>,
    session_store: HashMap<String, Session>,
    oauth_config: OAuthConfig,

    // Business logic config
    max_retries: u32,
    timeout_ms: u64,
    feature_flags: HashMap<String, bool>,

    // Observability
    metrics: PrometheusRegistry,
    logger: slog::Logger,
    tracer: opentelemetry::Tracer,

    // Notification
    email_client: SmtpClient,
    sms_provider: TwilioClient,
    push_service: FcmClient,
}

impl Application {
    fn authenticate_user(&self, token: &str) -> Result<User> { /* ... */ }
    fn send_welcome_email(&self, user: &User) -> Result<()> { /* ... */ }
    fn process_payment(&self, order: &Order) -> Result<Receipt> { /* ... */ }
    fn fetch_product_catalog(&self) -> Result<Vec<Product>> { /* ... */ }
    fn update_metrics(&self, event: &str) { /* ... */ }
    fn check_feature_flag(&self, flag: &str) -> bool { /* ... */ }
    fn send_push_notification(&self, user_id: &str, msg: &str) -> Result<()> { /* ... */ }
    // ... 40 more methods
}
```

This struct does everything. Authentication, payment processing, notifications, metrics, feature flags — it's the application equivalent of that one coworker who insists on being in every meeting.

## Why It's Actually Bad

**Borrow checker fights.** This is the Rust-specific reason god structs are especially painful. If you need mutable access to the session store while also reading the JWT secret, you're borrowing `&mut self` and `&self` simultaneously — which Rust won't allow. So you start cloning fields or restructuring method calls to work around the borrow checker, adding complexity to compensate for bad design.

```rust
impl Application {
    fn login(&mut self, credentials: &Credentials) -> Result<Session> {
        // Need &self.jwt_secret (immutable borrow)
        let user = self.authenticate_user(&credentials.token)?;

        // Need &mut self.session_store (mutable borrow)
        // ERROR: can't borrow *self as mutable because it's also borrowed as immutable
        let session = Session::new(&user);
        self.session_store.insert(session.id.clone(), session.clone());

        Ok(session)
    }
}
```

You'll end up doing awkward dances like cloning the JWT secret first, or extracting the session store into a local variable, or splitting the method into two calls. All of which are symptoms of the struct doing too much.

**Impossible to test in isolation.** Every method requires constructing the full struct. Want to test `send_welcome_email`? You need a real or mocked database pool, HTTP client, JWT secret, metrics registry, and everything else. Your test setup becomes fifty lines of mock construction before you write a single assertion.

**Compilation time.** Rust recompiles every method on a struct when any of its fields or methods change. A god struct means changing anything recompiles everything. On a large codebase, this adds up fast.

**Parallelism bottleneck.** If the struct is behind an `Arc<Mutex<Application>>`, every operation locks the entire application state. You can't process a payment while checking a feature flag because they both need the lock on the same struct.

## The Fix

### Step 1: Identify responsibilities

Group the fields by what they do. In our example:

- **Auth:** jwt_secret, session_store, oauth_config
- **Notifications:** email_client, sms_provider, push_service
- **Data access:** db_pool, redis
- **HTTP:** http_client, base_url, api_key
- **Observability:** metrics, logger, tracer
- **Config:** max_retries, timeout_ms, feature_flags

Each group is a candidate for its own struct.

### Step 2: Extract focused structs

```rust
struct AuthService {
    jwt_secret: Vec<u8>,
    session_store: HashMap<String, Session>,
    oauth_config: OAuthConfig,
}

impl AuthService {
    fn authenticate(&self, token: &str) -> Result<User> { /* ... */ }
    fn create_session(&mut self, user: &User) -> Session { /* ... */ }
    fn validate_session(&self, session_id: &str) -> Option<&Session> { /* ... */ }
}

struct NotificationService {
    email: SmtpClient,
    sms: TwilioClient,
    push: FcmClient,
}

impl NotificationService {
    fn send_email(&self, to: &str, subject: &str, body: &str) -> Result<()> { /* ... */ }
    fn send_sms(&self, to: &str, message: &str) -> Result<()> { /* ... */ }
    fn send_push(&self, user_id: &str, message: &str) -> Result<()> { /* ... */ }
}

struct DataStore {
    db: PgPool,
    cache: RedisClient,
}

impl DataStore {
    fn get_user(&self, id: &str) -> Result<User> { /* ... */ }
    fn save_order(&self, order: &Order) -> Result<()> { /* ... */ }
    fn get_cached<T: DeserializeOwned>(&self, key: &str) -> Option<T> { /* ... */ }
}
```

Each struct now has a clear responsibility and a small surface area. You can test `AuthService` without constructing a notification client. You can mock `DataStore` without knowing anything about authentication.

### Step 3: Compose via function parameters, not a mega-struct

Instead of putting everything on one struct and passing `&self`, pass only what each function needs:

```rust
fn handle_signup(
    auth: &mut AuthService,
    data: &DataStore,
    notifications: &NotificationService,
    credentials: &Credentials,
) -> Result<SignupResponse> {
    let user = auth.authenticate(&credentials.token)?;
    let session = auth.create_session(&user);
    data.save_user(&user)?;
    notifications.send_email(&user.email, "Welcome!", "Thanks for signing up")?;

    Ok(SignupResponse { session_id: session.id })
}
```

Notice how the borrow checker is happy now. We borrow `auth` mutably and `data` and `notifications` immutably — no conflict, because they're separate values.

### Step 4: Use traits for abstraction boundaries

If you want to swap implementations (real vs mock, different providers), define traits:

```rust
trait Authenticator {
    fn authenticate(&self, token: &str) -> Result<User>;
}

trait Notifier {
    fn send_email(&self, to: &str, subject: &str, body: &str) -> Result<()>;
}

trait UserStore {
    fn get_user(&self, id: &str) -> Result<User>;
    fn save_user(&self, user: &User) -> Result<()>;
}

fn handle_signup(
    auth: &impl Authenticator,
    store: &impl UserStore,
    notifier: &impl Notifier,
    credentials: &Credentials,
) -> Result<SignupResponse> {
    let user = auth.authenticate(&credentials.token)?;
    store.save_user(&user)?;
    notifier.send_email(&user.email, "Welcome!", "Thanks for signing up")?;

    Ok(SignupResponse { user_id: user.id })
}
```

Testing becomes trivial:

```rust
#[test]
fn test_signup_sends_welcome_email() {
    let auth = MockAuth::returning(User { id: "123".into(), email: "test@example.com".into() });
    let store = MockStore::new();
    let notifier = MockNotifier::new();

    handle_signup(&auth, &store, &notifier, &test_credentials()).unwrap();

    assert!(notifier.email_sent_to("test@example.com"));
}
```

No database. No HTTP client. No metrics. Just the pieces you actually need.

### Step 5: If you need a top-level container, make it thin

Sometimes you do need a struct that holds all the services together — for dependency injection at the application entry point. That's fine, but keep it thin:

```rust
struct App {
    auth: AuthService,
    data: DataStore,
    notifications: NotificationService,
    config: AppConfig,
}

impl App {
    fn new(config: AppConfig) -> Result<Self> {
        Ok(App {
            auth: AuthService::new(&config)?,
            data: DataStore::new(&config)?,
            notifications: NotificationService::new(&config)?,
            config,
        })
    }
}
```

This struct doesn't have methods that do business logic. It's a container. The actual work happens in standalone functions or on the individual service structs. The `App` struct is a composition root, not a god object.

## The Diagnostic

Here's how I evaluate whether a struct has gone too far:

- **More than 7-8 fields?** It's probably doing too much. (This isn't a hard rule, but it's a useful smell test.)
- **Methods that only touch 2-3 of the struct's fields?** Those methods should live on a smaller struct that only has those fields.
- **Need `&mut self` in one method while `&self` in another, and they conflict?** Your struct is mixing concerns that should be separately borrowable.
- **Test setup requires more than 10 lines of construction?** The struct has too many dependencies.

God structs happen gradually. Nobody writes a 42-field struct on day one. It starts with 5 fields, then someone adds "just one more," and over six months it metastasizes. The fix is noticing the trajectory early and splitting before the struct becomes load-bearing infrastructure that's terrifying to refactor.

Split early. Split often. Your future self — and the borrow checker — will thank you.
