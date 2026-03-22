---
title: "Lesson 4: Secret Management — zeroize and secure memory"
author: Atharva Pandey
series: "Rust Security in Production"
lesson: 4
keywords: [Rust, rust, security, secrets, zeroize, secure memory, key management, vault]
tags: [Rust tutorial, rust, security]
date: '2025-05-07T08:23:00.000Z'
---

A while back I was debugging a crash in production and pulled a core dump from the server. Sitting right there in the heap, in plain text, was a database connection string with credentials. The service had loaded the secret from Vault on startup, stored it in a regular `String`, and that `String` stayed in memory for the entire process lifetime. When it crashed, the secret got written to disk in the core dump.

This is the kind of vulnerability that doesn't show up in any code review unless you're specifically looking for it. The code works perfectly. It passes every test. And it leaks secrets in at least four different ways.

## Why Regular Memory Isn't Safe for Secrets

When you store a secret in a `String` or `Vec<u8>`, several things can go wrong:

**1. The compiler might optimize away your zeroing.** If you write `secret.clear()` and then the variable goes out of scope, the compiler may decide that zeroing is a dead store — nobody reads the zeroed value, so why bother? It removes the zeroing entirely. Your secret stays in memory.

**2. The memory might get paged to swap.** The OS can write any page of your process's memory to disk if it needs RAM. Your secrets might be sitting in a swap file long after your process exits.

**3. Core dumps include everything.** If your process crashes or you send it `SIGABRT`, the core dump contains all of heap and stack memory. Secrets included.

**4. Copying is invisible.** When you clone a `String`, you now have the secret in two places. When the allocator reallocs a growing `Vec`, the old memory isn't zeroed — it's just marked as free. The secret sits there until something else overwrites it.

## The `zeroize` Crate

`zeroize` is the foundation for secure secret handling in Rust. It provides a `Zeroize` trait that securely overwrites memory on drop, using techniques that prevent the compiler from optimizing away the zeroing.

```toml
[dependencies]
zeroize = { version = "1", features = ["derive"] }
```

### Basic Usage

```rust
use zeroize::{Zeroize, ZeroizeOnDrop};

// Derive ZeroizeOnDrop to automatically zeroize when the value is dropped
#[derive(ZeroizeOnDrop)]
struct DatabaseCredentials {
    #[zeroize]
    username: String,
    #[zeroize]
    password: String,
    host: String, // Not secret — doesn't need zeroizing
}

fn connect_to_database() {
    let creds = DatabaseCredentials {
        username: "admin".to_string(),
        password: "super_secret_password".to_string(),
        host: "db.example.com".to_string(),
    };

    // Use the credentials...
    establish_connection(&creds);

    // When `creds` goes out of scope, the username and password
    // are securely zeroed before the memory is freed.
    // The host field is dropped normally.
}

fn establish_connection(creds: &DatabaseCredentials) {
    println!("Connecting to {} as {}", creds.host, creds.username);
    // ... actual connection logic
}
```

### Manual Zeroization

Sometimes you need to zeroize before the value is dropped — for example, after you've derived a session key and no longer need the master key:

```rust
use zeroize::Zeroize;

fn derive_session_key(master_key: &mut [u8; 32]) -> [u8; 32] {
    let session_key = perform_kdf(master_key);

    // Explicitly zeroize the master key now that we're done with it.
    // Don't wait for drop — minimize the exposure window.
    master_key.zeroize();

    session_key
}

fn perform_kdf(key: &[u8; 32]) -> [u8; 32] {
    // Simplified — use a real KDF like HKDF in production
    let mut derived = [0u8; 32];
    for (i, byte) in key.iter().enumerate() {
        derived[i] = byte.wrapping_add(1);
    }
    derived
}
```

### The `Zeroizing` Wrapper

For quick wrapping without defining custom types, `zeroize` provides the `Zeroizing<T>` smart pointer:

```rust
use zeroize::Zeroizing;

fn load_api_key() -> Zeroizing<String> {
    let key = std::env::var("API_KEY").expect("API_KEY not set");
    Zeroizing::new(key)
}

fn make_authenticated_request(api_key: &Zeroizing<String>) {
    // Use Deref — it behaves like a &String / &str
    println!("Using key: {}...{}", &api_key[..4], &api_key[api_key.len()-4..]);
    // ... make the request
}

fn main() {
    let key = load_api_key();
    make_authenticated_request(&key);
    // key is zeroized when it goes out of scope
}
```

## Building a Secret Store

In production, you typically load secrets once at startup and access them throughout the application. Here's a pattern I've used that combines `zeroize` with proper access control:

```rust
use std::sync::Arc;
use zeroize::{Zeroize, ZeroizeOnDrop};

#[derive(ZeroizeOnDrop)]
struct SecretValue {
    #[zeroize]
    value: Vec<u8>,
}

impl SecretValue {
    fn new(value: Vec<u8>) -> Self {
        SecretValue { value }
    }

    fn expose(&self) -> &[u8] {
        &self.value
    }
}

// Don't implement Debug, Display, or Serialize for secrets
// This prevents accidental logging
impl std::fmt::Debug for SecretValue {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("[REDACTED]")
    }
}

pub struct SecretStore {
    secrets: std::collections::HashMap<String, Arc<SecretValue>>,
}

impl SecretStore {
    pub fn new() -> Self {
        SecretStore {
            secrets: std::collections::HashMap::new(),
        }
    }

    pub fn insert(&mut self, name: String, value: Vec<u8>) {
        self.secrets.insert(name, Arc::new(SecretValue::new(value)));
    }

    pub fn get(&self, name: &str) -> Option<Arc<SecretValue>> {
        self.secrets.get(name).cloned()
    }

    /// Rotate a secret — the old value is zeroized when the last
    /// reference is dropped.
    pub fn rotate(&mut self, name: &str, new_value: Vec<u8>) -> bool {
        if self.secrets.contains_key(name) {
            self.secrets
                .insert(name.to_string(), Arc::new(SecretValue::new(new_value)));
            true
        } else {
            false
        }
    }
}

fn main() {
    let mut store = SecretStore::new();
    store.insert("db_password".to_string(), b"hunter2".to_vec());
    store.insert("api_key".to_string(), b"sk-1234567890".to_vec());

    // Access a secret
    if let Some(secret) = store.get("db_password") {
        let password = std::str::from_utf8(secret.expose()).unwrap();
        println!("Password length: {}", password.len());
        // Don't print the actual password!

        // This prints "[REDACTED]" thanks to our Debug impl
        println!("Secret debug: {:?}", secret);
    }

    // Rotate a secret
    store.rotate("api_key", b"sk-new-key-9999".to_vec());
    // Old key is zeroized when Arc refcount hits zero
}
```

The `Arc` wrapper lets you hand out references to secrets that remain valid even during rotation. The old secret gets zeroized when the last reference is dropped.

## Preventing Accidental Logging

One of the most common ways secrets leak is through logging. Someone adds `debug!("{:?}", config)` and suddenly API keys show up in Splunk.

```rust
use std::fmt;
use zeroize::ZeroizeOnDrop;

/// A string wrapper that redacts itself in all formatting contexts
#[derive(Clone, ZeroizeOnDrop)]
pub struct RedactedString {
    #[zeroize]
    inner: String,
}

impl RedactedString {
    pub fn new(value: String) -> Self {
        RedactedString { inner: value }
    }

    /// Explicitly expose the secret. This is the ONLY way to get the value.
    /// The name is intentionally verbose to make it grep-able.
    pub fn expose_secret(&self) -> &str {
        &self.inner
    }
}

impl fmt::Debug for RedactedString {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("RedactedString([REDACTED])")
    }
}

impl fmt::Display for RedactedString {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("[REDACTED]")
    }
}

// Don't implement Serialize — secrets should never be serialized
// Don't implement PartialEq with &str — use constant-time comparison

#[derive(Debug)]
struct AppConfig {
    database_url: String,
    database_password: RedactedString,
    api_key: RedactedString,
    log_level: String,
}

fn main() {
    let config = AppConfig {
        database_url: "postgres://db.example.com:5432/myapp".to_string(),
        database_password: RedactedString::new("super_secret".to_string()),
        api_key: RedactedString::new("sk-1234567890abcdef".to_string()),
        log_level: "info".to_string(),
    };

    // Safe to log — secrets are redacted
    println!("Config: {:?}", config);
    // Output: AppConfig { database_url: "postgres://...", database_password: RedactedString([REDACTED]), api_key: RedactedString([REDACTED]), log_level: "info" }

    // To actually use the secret:
    let password = config.database_password.expose_secret();
    println!("Password length: {}", password.len());
}
```

The `expose_secret()` method name is intentionally verbose and distinct. You can grep your codebase for `expose_secret` to find every place a secret is unwrapped.

## Loading Secrets From Environment Variables

The twelve-factor app approach of using environment variables works, but has caveats. Environment variables are readable by anyone who can `cat /proc/$PID/environ` on Linux.

```rust
use zeroize::Zeroizing;
use std::collections::HashMap;

pub struct EnvSecrets {
    secrets: HashMap<String, Zeroizing<String>>,
}

impl EnvSecrets {
    pub fn load(keys: &[&str]) -> Result<Self, String> {
        let mut secrets = HashMap::new();

        for &key in keys {
            let value = std::env::var(key)
                .map_err(|_| format!("missing required secret: {}", key))?;

            // Clear the environment variable after reading it.
            // This reduces the exposure window — the secret was in the env
            // only during startup.
            std::env::remove_var(key);

            secrets.insert(key.to_string(), Zeroizing::new(value));
        }

        Ok(EnvSecrets { secrets })
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        self.secrets.get(key).map(|s| s.as_str())
    }
}

fn main() -> Result<(), String> {
    // In a real app, these would be set by your orchestrator
    std::env::set_var("DB_PASSWORD", "secret123");
    std::env::set_var("API_KEY", "sk-abcdef");

    let secrets = EnvSecrets::load(&["DB_PASSWORD", "API_KEY"])?;

    // Environment variables are now cleared
    assert!(std::env::var("DB_PASSWORD").is_err());
    assert!(std::env::var("API_KEY").is_err());

    // Access secrets through the store
    println!("DB password length: {}", secrets.get("DB_PASSWORD").unwrap().len());

    Ok(())
}
```

Removing environment variables after reading them is a small defense-in-depth measure. It won't stop someone with kernel access, but it reduces the number of places the secret exists.

## Locking Memory Pages

For the most sensitive secrets (cryptographic keys, master secrets), you may want to prevent the OS from paging the memory to swap. The `memsec` or `secrecy` crates can help, but you can also use `mlock` directly:

```rust
use std::io;

/// Lock a memory region to prevent it from being swapped to disk.
/// Requires appropriate privileges (CAP_IPC_LOCK on Linux).
#[cfg(unix)]
fn mlock_slice(data: &[u8]) -> io::Result<()> {
    let result = unsafe {
        libc::mlock(data.as_ptr() as *const libc::c_void, data.len())
    };
    if result == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

#[cfg(unix)]
fn munlock_slice(data: &[u8]) -> io::Result<()> {
    let result = unsafe {
        libc::munlock(data.as_ptr() as *const libc::c_void, data.len())
    };
    if result == 0 {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

/// A key buffer that's locked in memory and zeroized on drop
struct LockedKey {
    key: Vec<u8>,
}

impl LockedKey {
    #[cfg(unix)]
    fn new(key_data: Vec<u8>) -> io::Result<Self> {
        mlock_slice(&key_data)?;
        Ok(LockedKey { key: key_data })
    }

    fn as_bytes(&self) -> &[u8] {
        &self.key
    }
}

impl Drop for LockedKey {
    fn drop(&mut self) {
        // Zeroize first
        use zeroize::Zeroize;
        self.key.zeroize();

        // Then unlock
        #[cfg(unix)]
        {
            let _ = munlock_slice(&self.key);
        }
    }
}
```

Fair warning: `mlock` has limits. Most systems restrict the amount of memory a process can lock (check `ulimit -l`). For a few keys, it's fine. For megabytes of data, you'll hit the limit.

## The `secrecy` Crate

If you want a batteries-included solution, the `secrecy` crate (from the RustCrypto team) provides a `Secret<T>` wrapper that combines zeroization with controlled exposure:

```toml
[dependencies]
secrecy = { version = "0.10", features = ["serde"] }
```

```rust
use secrecy::{ExposeSecret, SecretString, SecretVec};

struct DatabaseConfig {
    host: String,
    port: u16,
    username: String,
    password: SecretString,
}

impl DatabaseConfig {
    fn connection_string(&self) -> SecretString {
        let conn = format!(
            "postgres://{}:{}@{}:{}/mydb",
            self.username,
            self.password.expose_secret(),
            self.host,
            self.port,
        );
        SecretString::from(conn)
    }
}

fn main() {
    let config = DatabaseConfig {
        host: "localhost".to_string(),
        port: 5432,
        username: "app".to_string(),
        password: SecretString::from("hunter2".to_string()),
    };

    // Safe to debug-print — the password is redacted
    // SecretString doesn't implement Display or Debug with the actual value

    // To use the password:
    let conn_string = config.connection_string();
    let exposed = conn_string.expose_secret();
    println!("Connection string length: {}", exposed.len());
    // When conn_string goes out of scope, the memory is zeroized
}
```

## Practical Checklist

Here's what I enforce on every service I ship:

1. **All secrets use `SecretString` or `Zeroizing<String>`.** Regular `String` for secrets is a code review rejection.

2. **No `Debug` or `Display` that exposes secret values.** Either derive nothing, or implement custom formatters that redact.

3. **No `Serialize` on secret types.** If a secret needs to be sent over the wire, it goes through an explicit `expose_secret()` call at the serialization boundary.

4. **Secrets are loaded once at startup and cleared from the environment.** The `EnvSecrets` pattern above, or better yet, fetched from a vault.

5. **Core dumps are disabled in production.** On Linux: `ulimit -c 0` or `prctl(PR_SET_DUMPABLE, 0)` in your startup code.

6. **Key material uses `mlock` where possible.** Prevents paging to swap.

7. **Secret rotation doesn't leak the old secret.** The `Arc<SecretValue>` pattern ensures old secrets are zeroized when the last reference drops.

These aren't exotic security hardening measures. They're table stakes for any service handling sensitive data. Rust makes most of them easy — the type system naturally guides you toward the right patterns. You just have to choose to use them.
