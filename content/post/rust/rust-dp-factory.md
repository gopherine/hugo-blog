---
title: "Lesson 6: Factory Patterns — When constructors aren't enough"
author: Atharva Pandey
series: "Rust Design Patterns"
lesson: 6
keywords: [Rust, rust, design patterns, factory pattern, constructor, associated functions, builder]
tags: [Rust tutorial, rust, design-patterns, architecture]
date: '2025-10-11T10:00:00.000Z'
---

Here's a hot take: most "Factory pattern" usage in Java and C# exists purely to work around limitations of constructors. Constructors can't have descriptive names. They can't return a different subtype. They can't fail gracefully. So you wrap them in a static method and call it a Factory.

Rust doesn't have constructors at all. Every struct is constructed directly, and associated functions already do what Factory Method does in OOP. So do you even need the Factory pattern in Rust?

Yes — but the reasons are different, and the implementations are cleaner.

## Rust's Built-In "Factory": Associated Functions

Before we get into patterns, let's acknowledge what Rust gives you for free. `new()` is just a convention — it's a regular associated function:

```rust
pub struct Connection {
    host: String,
    port: u16,
    tls: bool,
    timeout_ms: u64,
}

impl Connection {
    // The "default" constructor
    pub fn new(host: impl Into<String>, port: u16) -> Self {
        Self {
            host: host.into(),
            port,
            tls: false,
            timeout_ms: 5000,
        }
    }

    // Named constructors — this IS Factory Method
    pub fn secure(host: impl Into<String>, port: u16) -> Self {
        Self {
            host: host.into(),
            port,
            tls: true,
            timeout_ms: 5000,
        }
    }

    pub fn with_timeout(host: impl Into<String>, port: u16, timeout_ms: u64) -> Self {
        Self {
            host: host.into(),
            port,
            tls: false,
            timeout_ms,
        }
    }
}
```

Named associated functions like `Connection::secure()` are already Factory Methods. No pattern needed — the language supports it natively. In Java, you'd need a separate `ConnectionFactory` class because constructors must be named after the class. Rust doesn't have that limitation.

## Fallible Construction

The most common reason to use a factory-style approach in Rust is when construction can fail:

```rust
use std::net::IpAddr;

pub struct DatabaseConfig {
    host: IpAddr,
    port: u16,
    database: String,
    pool_size: usize,
}

impl DatabaseConfig {
    pub fn from_url(url: &str) -> Result<Self, ConfigError> {
        let parts: Vec<&str> = url.split("://").collect();
        if parts.len() != 2 {
            return Err(ConfigError::InvalidFormat(
                "Expected format: scheme://host:port/database".into(),
            ));
        }

        let remainder = parts[1];
        let segments: Vec<&str> = remainder.split('/').collect();
        if segments.len() != 2 {
            return Err(ConfigError::InvalidFormat(
                "Expected host:port/database".into(),
            ));
        }

        let host_port: Vec<&str> = segments[0].split(':').collect();
        if host_port.len() != 2 {
            return Err(ConfigError::InvalidFormat(
                "Expected host:port".into(),
            ));
        }

        let host: IpAddr = host_port[0]
            .parse()
            .map_err(|e| ConfigError::InvalidHost(format!("{}", e)))?;

        let port: u16 = host_port[1]
            .parse()
            .map_err(|_| ConfigError::InvalidPort)?;

        Ok(Self {
            host,
            port,
            database: segments[1].to_string(),
            pool_size: 10,
        })
    }

    pub fn from_env() -> Result<Self, ConfigError> {
        let host: IpAddr = std::env::var("DB_HOST")
            .map_err(|_| ConfigError::MissingEnv("DB_HOST"))?
            .parse()
            .map_err(|e| ConfigError::InvalidHost(format!("{}", e)))?;

        let port: u16 = std::env::var("DB_PORT")
            .map_err(|_| ConfigError::MissingEnv("DB_PORT"))?
            .parse()
            .map_err(|_| ConfigError::InvalidPort)?;

        let database = std::env::var("DB_NAME")
            .map_err(|_| ConfigError::MissingEnv("DB_NAME"))?;

        Ok(Self {
            host,
            port,
            database,
            pool_size: 10,
        })
    }
}

#[derive(Debug)]
pub enum ConfigError {
    InvalidFormat(String),
    InvalidHost(String),
    InvalidPort,
    MissingEnv(&'static str),
}
```

`from_url` and `from_env` are factory methods that validate input and return `Result`. This is idiomatic Rust — you'll see `from_*` prefixes everywhere in the standard library (`PathBuf::from`, `String::from_utf8`, `TcpStream::from_std`).

## Abstract Factory: Trait-Based Construction

When you need to create *families* of related objects — the classic Abstract Factory — Rust uses traits:

```rust
pub trait UIFactory {
    type Button: Widget;
    type TextField: Widget;
    type Checkbox: Widget;

    fn create_button(&self, label: &str) -> Self::Button;
    fn create_text_field(&self, placeholder: &str) -> Self::TextField;
    fn create_checkbox(&self, label: &str, checked: bool) -> Self::Checkbox;
}

pub trait Widget {
    fn render(&self) -> String;
    fn width(&self) -> u32;
    fn height(&self) -> u32;
}

// --- Platform-specific implementations ---

pub struct WebFactory;

pub struct WebButton {
    label: String,
}

impl Widget for WebButton {
    fn render(&self) -> String {
        format!("<button class=\"btn\">{}</button>", self.label)
    }
    fn width(&self) -> u32 { 120 }
    fn height(&self) -> u32 { 40 }
}

pub struct WebTextField {
    placeholder: String,
}

impl Widget for WebTextField {
    fn render(&self) -> String {
        format!("<input type=\"text\" placeholder=\"{}\" />", self.placeholder)
    }
    fn width(&self) -> u32 { 200 }
    fn height(&self) -> u32 { 36 }
}

pub struct WebCheckbox {
    label: String,
    checked: bool,
}

impl Widget for WebCheckbox {
    fn render(&self) -> String {
        let checked = if self.checked { " checked" } else { "" };
        format!(
            "<label><input type=\"checkbox\"{}> {}</label>",
            checked, self.label
        )
    }
    fn width(&self) -> u32 { 150 }
    fn height(&self) -> u32 { 24 }
}

impl UIFactory for WebFactory {
    type Button = WebButton;
    type TextField = WebTextField;
    type Checkbox = WebCheckbox;

    fn create_button(&self, label: &str) -> WebButton {
        WebButton {
            label: label.to_string(),
        }
    }

    fn create_text_field(&self, placeholder: &str) -> WebTextField {
        WebTextField {
            placeholder: placeholder.to_string(),
        }
    }

    fn create_checkbox(&self, label: &str, checked: bool) -> WebCheckbox {
        WebCheckbox {
            label: label.to_string(),
            checked,
        }
    }
}
```

The key is associated types. `UIFactory::Button` is a *specific* type — not `Box<dyn Widget>`. The compiler knows exactly which widget type each factory produces. This means no heap allocation, no dynamic dispatch, full type safety.

Consumer code is generic over the factory:

```rust
fn build_login_form<F: UIFactory>(factory: &F) -> Vec<String> {
    let username = factory.create_text_field("Username");
    let password = factory.create_text_field("Password");
    let remember = factory.create_checkbox("Remember me", false);
    let submit = factory.create_button("Log In");

    vec![
        username.render(),
        password.render(),
        remember.render(),
        submit.render(),
    ]
}
```

Swap `WebFactory` for `TerminalFactory` and the same form renders as TUI widgets. Zero code changes in `build_login_form`.

## Registration-Based Factory

Sometimes you need to register creators at runtime — plugins, file format handlers, protocol parsers. Here's the pattern:

```rust
use std::collections::HashMap;

pub trait Parser: Send + Sync {
    fn parse(&self, data: &[u8]) -> Result<Document, ParseError>;
    fn supported_extensions(&self) -> &[&str];
}

pub struct Document {
    pub format: String,
    pub content: String,
}

#[derive(Debug)]
pub struct ParseError(pub String);

type ParserConstructor = Box<dyn Fn() -> Box<dyn Parser> + Send + Sync>;

pub struct ParserRegistry {
    factories: HashMap<String, ParserConstructor>,
}

impl ParserRegistry {
    pub fn new() -> Self {
        Self {
            factories: HashMap::new(),
        }
    }

    pub fn register<F>(&mut self, extension: &str, constructor: F)
    where
        F: Fn() -> Box<dyn Parser> + Send + Sync + 'static,
    {
        self.factories
            .insert(extension.to_lowercase(), Box::new(constructor));
    }

    pub fn create_parser(&self, extension: &str) -> Option<Box<dyn Parser>> {
        self.factories
            .get(&extension.to_lowercase())
            .map(|constructor| constructor())
    }

    pub fn parse_file(&self, path: &str, data: &[u8]) -> Result<Document, ParseError> {
        let extension = path
            .rsplit('.')
            .next()
            .ok_or_else(|| ParseError("No file extension".into()))?;

        let parser = self
            .create_parser(extension)
            .ok_or_else(|| ParseError(format!("No parser for .{}", extension)))?;

        parser.parse(data)
    }
}

// Usage
fn setup_registry() -> ParserRegistry {
    let mut registry = ParserRegistry::new();

    registry.register("json", || Box::new(JsonParser));
    registry.register("yaml", || Box::new(YamlParser));
    registry.register("yml", || Box::new(YamlParser));
    registry.register("toml", || Box::new(TomlParser));

    registry
}
```

The factory stores constructor *functions*, not instances. Each call to `create_parser` produces a fresh parser. This is important for parsers that accumulate state during parsing — you don't want shared mutable state between parse operations.

## The Enum Factory

For a closed set of variants, enums combine the factory and the product:

```rust
#[derive(Debug, Clone)]
pub enum LogLevel {
    Debug,
    Info,
    Warn,
    Error,
}

#[derive(Debug)]
pub enum Logger {
    Console { level: LogLevel },
    File { path: String, level: LogLevel },
    Remote { endpoint: String, level: LogLevel },
    Null,
}

impl Logger {
    pub fn from_config(config: &str) -> Result<Self, String> {
        let parts: Vec<&str> = config.splitn(2, ':').collect();
        match parts[0] {
            "console" => Ok(Logger::Console {
                level: LogLevel::Info,
            }),
            "file" => {
                let path = parts
                    .get(1)
                    .ok_or("file logger requires a path")?;
                Ok(Logger::File {
                    path: path.to_string(),
                    level: LogLevel::Info,
                })
            }
            "remote" => {
                let endpoint = parts
                    .get(1)
                    .ok_or("remote logger requires an endpoint")?;
                Ok(Logger::Remote {
                    endpoint: endpoint.to_string(),
                    level: LogLevel::Info,
                })
            }
            "null" | "none" => Ok(Logger::Null),
            other => Err(format!("unknown logger type: {}", other)),
        }
    }

    pub fn log(&self, message: &str) {
        match self {
            Logger::Console { level } => {
                println!("[{:?}] {}", level, message);
            }
            Logger::File { path, level } => {
                println!("(Would write to {}) [{:?}] {}", path, level, message);
            }
            Logger::Remote { endpoint, level } => {
                println!("(Would send to {}) [{:?}] {}", endpoint, level, message);
            }
            Logger::Null => {}
        }
    }
}
```

No trait, no dynamic dispatch, no heap allocation. The enum *is* the polymorphism. `from_config` is the factory method. Pattern matching replaces virtual dispatch. For a known set of variants, this is the most Rust-idiomatic approach.

## When to Use What

**Named associated functions** (`new`, `from_*`, `with_*`) — always. This is your default. If construction is straightforward, this is all you need.

**Trait-based abstract factory** — when you have families of related types that vary together. UI toolkits, platform abstractions, test/production splits.

**Registration-based factory** — when types are open-ended. Plugin systems, file format handlers, protocol negotiation.

**Enum factory** — when variants are known at compile time and you want zero overhead. Config parsing, command dispatch, serialization formats.

The Factory pattern in Rust is less of a "pattern" and more of an idiom. Rust's associated functions, traits with associated types, and enums already provide the building blocks. You don't need a `FactoryFactory` — you just need to pick the right tool for your use case.
