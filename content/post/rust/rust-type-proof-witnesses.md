---
title: "Lesson 9: Proof Witnesses — Types as proofs"
author: Atharva Pandey
series: "Rust Type System Wizardry"
lesson: 9
keywords: [Rust, rust, type system, proof witnesses, types as proofs, type-level proofs, invariant enforcement]
tags: [Rust tutorial, rust, type-system, advanced]
date: '2025-09-19T13:22:00.000Z'
---

The moment this clicked for me was when I was reviewing a crate that had a function signature like `fn process(data: &[u8], _proof: NonEmpty<'_>)`. That second argument carried *no data*. It was zero-sized. But you could only construct it by proving — through code that the compiler checked — that the slice was non-empty. The proof existed at compile time. At runtime, it was nothing.

Types as proofs. Once you see it, you can't unsee it.

## The Core Idea

A proof witness is a value whose *existence* proves that some property holds. You can't construct the witness without the property being true, so if the witness exists, the property is guaranteed.

The simplest example:

```rust
use std::marker::PhantomData;

// This type can only be constructed if a slice is non-empty
pub struct NonEmpty<'a> {
    _lifetime: PhantomData<&'a ()>,
}

impl<'a> NonEmpty<'a> {
    // The ONLY way to construct NonEmpty is through this function
    pub fn verify(slice: &'a [u8]) -> Option<NonEmpty<'a>> {
        if slice.is_empty() {
            None
        } else {
            Some(NonEmpty { _lifetime: PhantomData })
        }
    }
}

// This function REQUIRES proof that the data is non-empty
fn first_byte(data: &[u8], _proof: NonEmpty<'_>) -> u8 {
    // We KNOW data is non-empty — the proof guarantees it
    // No bounds check needed. No unwrap. No panic.
    data[0]
}

fn main() {
    let data = vec![42, 17, 93];

    // We must verify before we can call first_byte
    if let Some(proof) = NonEmpty::verify(&data) {
        let byte = first_byte(&data, proof);
        println!("First byte: {}", byte);
    }

    // Can't just call first_byte without proof:
    // first_byte(&data, ???); // No way to construct NonEmpty without verify()
}
```

The `_proof` parameter is zero-sized — it adds nothing to the function call at runtime. But at compile time, it forces the caller to go through `verify()`, which checks the non-emptiness invariant. The proof is a compile-time gate.

## Why Not Just Check Inside the Function?

You could write `first_byte` to check for emptiness itself:

```rust
fn first_byte_checked(data: &[u8]) -> Option<u8> {
    data.first().copied()
}
```

This is fine for simple cases. But proof witnesses solve a different problem: they let you **check once and use many times**.

```rust
use std::marker::PhantomData;

pub struct NonEmpty<'a>(PhantomData<&'a ()>);

impl<'a> NonEmpty<'a> {
    pub fn verify(slice: &'a [u8]) -> Option<NonEmpty<'a>> {
        if slice.is_empty() { None } else { Some(NonEmpty(PhantomData)) }
    }
}

fn first_byte(data: &[u8], _: &NonEmpty<'_>) -> u8 { data[0] }
fn last_byte(data: &[u8], _: &NonEmpty<'_>) -> u8 { data[data.len() - 1] }
fn middle_byte(data: &[u8], _: &NonEmpty<'_>) -> u8 { data[data.len() / 2] }

fn process(data: &[u8]) {
    let Some(proof) = NonEmpty::verify(data) else {
        println!("Empty data, skipping");
        return;
    };

    // Check ONCE, use three times — no repeated bounds checks
    let first = first_byte(data, &proof);
    let last = last_byte(data, &proof);
    let mid = middle_byte(data, &proof);
    println!("First: {}, Middle: {}, Last: {}", first, mid, last);
}
```

One validation, multiple uses. The proof witness carries the result of the validation to every call site.

## Proof Witnesses for Validation

This pattern is incredibly useful for validated data:

```rust
use std::marker::PhantomData;

// A proof that a string is a valid email address
pub struct ValidEmail<'a> {
    email: &'a str,
}

impl<'a> ValidEmail<'a> {
    pub fn validate(input: &'a str) -> Result<ValidEmail<'a>, &'static str> {
        // Real validation logic
        if input.contains('@') && input.contains('.') && input.len() > 5 {
            Ok(ValidEmail { email: input })
        } else {
            Err("invalid email format")
        }
    }

    pub fn as_str(&self) -> &'a str {
        self.email
    }
}

// A proof that a number is in a valid range
pub struct InRange<const MIN: i32, const MAX: i32> {
    value: i32,
}

impl<const MIN: i32, const MAX: i32> InRange<MIN, MAX> {
    pub fn new(value: i32) -> Option<Self> {
        if value >= MIN && value <= MAX {
            Some(InRange { value })
        } else {
            None
        }
    }

    pub fn get(&self) -> i32 {
        self.value
    }
}

// Functions that REQUIRE valid inputs
fn send_email(to: &ValidEmail<'_>, body: &str) {
    println!("Sending to {}: {}", to.as_str(), body);
    // No need to re-validate — the type guarantees it
}

fn set_volume(level: InRange<0, 100>) {
    println!("Volume set to {}", level.get());
    // No need to bounds-check — the type guarantees it
}

fn main() {
    // Must validate before calling
    let email = ValidEmail::validate("user@example.com").unwrap();
    send_email(&email, "Hello!");

    let volume = InRange::<0, 100>::new(75).unwrap();
    set_volume(volume);

    // These won't compile without going through validation:
    // send_email(&"not-an-email", "oops");
    // set_volume(150);
}
```

This is the "parse, don't validate" principle in action. Instead of validating data at every use site, you validate it *once* at the boundary and carry the proof forward through the type system.

## The Unconstructable Witness

For even stronger guarantees, make the witness impossible to construct outside your module:

```rust
mod auth {
    pub struct Authenticated {
        // Private field — can't be constructed outside this module
        _private: (),
    }

    pub struct Credentials {
        pub username: String,
        pub password: String,
    }

    pub fn login(creds: &Credentials) -> Result<Authenticated, String> {
        if creds.username == "admin" && creds.password == "secret" {
            Ok(Authenticated { _private: () })
        } else {
            Err("invalid credentials".to_string())
        }
    }
}

// This function can ONLY be called after successful authentication
fn admin_panel(_proof: &auth::Authenticated) {
    println!("Welcome to the admin panel");
    // We KNOW the user is authenticated
    // No need to check credentials again
}

fn main() {
    let creds = auth::Credentials {
        username: "admin".to_string(),
        password: "secret".to_string(),
    };

    match auth::login(&creds) {
        Ok(proof) => admin_panel(&proof),
        Err(e) => println!("Login failed: {}", e),
    }

    // Can't forge authentication:
    // let fake = auth::Authenticated { _private: () }; // ERROR: private field
    // admin_panel(&fake);
}
```

The private field `_private: ()` is the seal. Nobody outside the `auth` module can construct an `Authenticated` value. The only way to get one is through `login()`. So if you have an `Authenticated` witness, you *know* the user went through the login process.

## Combining Proofs

Proof witnesses compose beautifully:

```rust
mod proofs {
    pub struct NonEmpty(());
    pub struct Sorted(());
    pub struct Deduped(());

    impl NonEmpty {
        pub fn check(data: &[i32]) -> Option<NonEmpty> {
            if data.is_empty() { None } else { Some(NonEmpty(())) }
        }
    }

    impl Sorted {
        pub fn check(data: &[i32]) -> Option<Sorted> {
            if data.windows(2).all(|w| w[0] <= w[1]) {
                Some(Sorted(()))
            } else {
                None
            }
        }
    }

    impl Deduped {
        pub fn check(data: &[i32]) -> Option<Deduped> {
            if data.windows(2).all(|w| w[0] != w[1]) {
                Some(Deduped(()))
            } else {
                None
            }
        }
    }
}

// Binary search requires sorted + non-empty
fn binary_search(
    data: &[i32],
    target: i32,
    _non_empty: &proofs::NonEmpty,
    _sorted: &proofs::Sorted,
) -> Option<usize> {
    data.binary_search(&target).ok()
}

// Unique median requires sorted + deduped + non-empty
fn unique_median(
    data: &[i32],
    _non_empty: &proofs::NonEmpty,
    _sorted: &proofs::Sorted,
    _deduped: &proofs::Deduped,
) -> i32 {
    data[data.len() / 2]
}

fn main() {
    let data = vec![1, 3, 5, 7, 9];

    let ne = proofs::NonEmpty::check(&data).expect("non-empty");
    let sorted = proofs::Sorted::check(&data).expect("sorted");
    let deduped = proofs::Deduped::check(&data).expect("deduped");

    let idx = binary_search(&data, 5, &ne, &sorted);
    println!("Found at: {:?}", idx);

    let med = unique_median(&data, &ne, &sorted, &deduped);
    println!("Median: {}", med);
}
```

Each proof is independent. You combine them as needed. `binary_search` needs `NonEmpty + Sorted`. `unique_median` needs all three. The caller must provide all required proofs, and each proof requires a separate validation step.

## Phantom Proofs for Zero-Cost

If your proof witnesses carry data (like `ValidEmail` carrying the email string), they have runtime cost. But pure phantom proofs are truly zero-cost:

```rust
use std::marker::PhantomData;

// Zero-sized proof — exists only in the type system
pub struct Proof<P>(PhantomData<P>);

// Property markers
pub struct IsPositive;
pub struct IsEven;
pub struct IsPrime;

impl Proof<IsPositive> {
    pub fn check(n: i32) -> Option<Self> {
        if n > 0 { Some(Proof(PhantomData)) } else { None }
    }
}

impl Proof<IsEven> {
    pub fn check(n: i32) -> Option<Self> {
        if n % 2 == 0 { Some(Proof(PhantomData)) } else { None }
    }
}

fn sqrt_positive(n: f64, _: &Proof<IsPositive>) -> f64 {
    n.sqrt()  // Safe — we know n > 0
}

fn half_even(n: i32, _: &Proof<IsEven>) -> i32 {
    n / 2  // Exact division — we know n is even
}

fn main() {
    let n = 16;

    if let (Some(pos), Some(even)) = (
        Proof::<IsPositive>::check(n),
        Proof::<IsEven>::check(n),
    ) {
        let root = sqrt_positive(n as f64, &pos);
        let half = half_even(n, &even);
        println!("sqrt({}) = {}, {}/2 = {}", n, root, n, half);
    }
}
```

`Proof<IsPositive>` is zero-sized. In the compiled binary, it doesn't exist. The function call `sqrt_positive(n as f64, &pos)` compiles to the same code as `n.sqrt()`. The proof is a compile-time-only concept.

## Proof Witnesses in APIs

This pattern is especially valuable at API boundaries:

```rust
mod api {
    pub struct RateLimitToken {
        remaining: u32,
    }

    pub struct ApiKey(String);

    pub struct AuthenticatedClient {
        key: ApiKey,
        _private: (),
    }

    impl AuthenticatedClient {
        pub fn connect(api_key: &str) -> Result<Self, String> {
            // Validate API key format, check with auth server, etc.
            if api_key.starts_with("sk_") {
                Ok(AuthenticatedClient {
                    key: ApiKey(api_key.to_string()),
                    _private: (),
                })
            } else {
                Err("Invalid API key format".to_string())
            }
        }

        pub fn check_rate_limit(&self) -> Option<RateLimitToken> {
            // Check rate limit
            Some(RateLimitToken { remaining: 99 })
        }
    }

    // This function requires BOTH authentication AND rate limit clearance
    pub fn make_request(
        client: &AuthenticatedClient,
        _rate_limit: RateLimitToken,
        endpoint: &str,
    ) -> String {
        format!("Response from {}", endpoint)
    }
}

fn main() {
    let client = api::AuthenticatedClient::connect("sk_live_abc123").unwrap();
    let token = client.check_rate_limit().unwrap();
    let response = api::make_request(&client, token, "/users");
    println!("{}", response);

    // Can't skip auth:
    // api::make_request(???, token, "/users");

    // Can't skip rate limit:
    // api::make_request(&client, ???, "/users");
}
```

Each step in the process produces a witness. The final function requires all witnesses. You literally cannot call `make_request` without going through authentication and rate limiting. The compiler enforces the workflow.

## Relationship to the Curry-Howard Correspondence

What we're doing here has deep roots in mathematical logic. The Curry-Howard correspondence says that types are propositions and values are proofs. If you can construct a value of type `T`, you've proved the proposition that `T` represents.

- `NonEmpty` is the proposition "this collection has at least one element"
- A value of type `NonEmpty` is a *proof* of that proposition
- If you can't construct a `NonEmpty`, the proposition is false (the collection is empty)

We're not doing full dependent type theory — Rust's type system isn't powerful enough for that (see Lesson 10). But the basic idea carries over: making illegal states unrepresentable by encoding invariants as types.

## When to Use Proof Witnesses

I reach for proof witnesses when:

1. **Validation happens at a boundary** and results are used deep in the call stack — pass the proof down instead of re-validating at every level.

2. **Multiple functions share the same precondition** — validate once, pass the proof to all of them.

3. **Security-critical code** — authentication, authorization, rate limiting. The type system enforces that checks happened.

4. **Algorithmic preconditions** — sortedness, non-emptiness, uniqueness. The proof prevents calling algorithms with invalid input.

The pattern is overkill for simple "check and proceed" cases. It shines when the distance between "where you check" and "where you use" is large — across function boundaries, module boundaries, or even crate boundaries.

## The Tradeoff

Proof witnesses add friction. Every call site needs to acquire proofs. The validation-at-the-boundary approach is more verbose than just checking inside each function.

But that friction is the point. It makes implicit assumptions explicit. It turns documentation ("this function assumes the input is sorted") into compiler-enforced contracts. And it catches bugs where someone adds a new call path that skips the validation.

I've found the best balance is: proof witnesses at module boundaries, plain assertions inside modules. The boundary is where bugs are most likely to creep in — that's where the compiler's help is most valuable.

Next — our final lesson — we'll push even further into dependent type tricks. Encoding constraints so deep in the type system that invalid programs become literally unwritable.
