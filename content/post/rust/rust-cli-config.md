---
title: "Lesson 3: Configuration Files and Environment Variables — Config that scales"
author: Atharva Pandey
series: "Rust CLI & Tooling"
lesson: 3
keywords: [Rust, rust, CLI, config, configuration, environment variables, TOML, serde, dotenv]
tags: [Rust tutorial, rust, cli]
date: '2024-09-05T11:08:00.000Z'
---

I shipped a CLI tool with 23 flags once. Twenty-three. The `--help` output scrolled past two terminal screens. Users hated it, nobody could remember the flags, and every deployment script was a wall of backslash-continued command lines. That's when I learned: if your tool has more than about eight flags, you need a configuration file.

## The Configuration Hierarchy

Every serious CLI tool follows the same precedence order:

1. Command-line flags (highest priority)
2. Environment variables
3. Project-local config file (`.myapp.toml` in the current directory)
4. User config file (`~/.config/myapp/config.toml`)
5. System config file (`/etc/myapp/config.toml`)
6. Compiled-in defaults (lowest priority)

Each layer overrides the one below it. This lets users set defaults in their home directory, override them per-project, and override those on a per-invocation basis with flags. kubectl, git, docker — they all work this way.

## The Dependencies

```toml
[package]
name = "configdemo"
version = "0.1.0"
edition = "2021"

[dependencies]
clap = { version = "4", features = ["derive", "env"] }
serde = { version = "1", features = ["derive"] }
toml = "0.8"
directories = "5"
```

`serde` for deserialization, `toml` for config file parsing, `directories` for finding the right config directory on each OS.

## The Config Struct

Start with a struct that represents your entire configuration. This struct does double duty — it's both the shape of your config file and the internal representation your app uses:

```rust
use serde::Deserialize;
use std::path::PathBuf;

#[derive(Debug, Deserialize, Clone)]
#[serde(default)]
pub struct AppConfig {
    pub server: ServerConfig,
    pub database: DatabaseConfig,
    pub logging: LogConfig,
}

#[derive(Debug, Deserialize, Clone)]
#[serde(default)]
pub struct ServerConfig {
    pub host: String,
    pub port: u16,
    pub workers: usize,
    pub timeout_secs: u64,
}

#[derive(Debug, Deserialize, Clone)]
#[serde(default)]
pub struct DatabaseConfig {
    pub url: String,
    pub max_connections: u32,
    pub ssl: bool,
}

#[derive(Debug, Deserialize, Clone)]
#[serde(default)]
pub struct LogConfig {
    pub level: String,
    pub file: Option<PathBuf>,
    pub json: bool,
}

impl Default for AppConfig {
    fn default() -> Self {
        Self {
            server: ServerConfig::default(),
            database: DatabaseConfig::default(),
            logging: LogConfig::default(),
        }
    }
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".to_string(),
            port: 8080,
            workers: num_cpus(),
            timeout_secs: 30,
        }
    }
}

impl Default for DatabaseConfig {
    fn default() -> Self {
        Self {
            url: "postgres://localhost/myapp".to_string(),
            max_connections: 10,
            ssl: false,
        }
    }
}

impl Default for LogConfig {
    fn default() -> Self {
        Self {
            level: "info".to_string(),
            file: None,
            json: false,
        }
    }
}

fn num_cpus() -> usize {
    std::thread::available_parallelism()
        .map(|n| n.get())
        .unwrap_or(4)
}
```

The `#[serde(default)]` attribute is critical. It means partial config files work — users only need to specify the values they want to change. Everything else falls back to the `Default` impl.

The corresponding TOML config file:

```toml
[server]
host = "0.0.0.0"
port = 9090
workers = 8

[database]
url = "postgres://prod-host/myapp"
max_connections = 50
ssl = true

[logging]
level = "debug"
file = "/var/log/myapp.log"
json = true
```

## Loading Config With the Full Hierarchy

Here's the function that loads config from multiple sources and merges them:

```rust
use std::fs;
use std::path::{Path, PathBuf};

fn find_config_files(explicit_path: &Option<PathBuf>) -> Vec<PathBuf> {
    let mut paths = Vec::new();

    // System config
    let system_path = PathBuf::from("/etc/myapp/config.toml");
    if system_path.exists() {
        paths.push(system_path);
    }

    // User config
    if let Some(config_dir) = directories::ProjectDirs::from("com", "myapp", "myapp") {
        let user_path = config_dir.config_dir().join("config.toml");
        if user_path.exists() {
            paths.push(user_path);
        }
    }

    // Project-local config
    let local_path = PathBuf::from(".myapp.toml");
    if local_path.exists() {
        paths.push(local_path);
    }

    // Explicit config file (highest priority file)
    if let Some(p) = explicit_path {
        paths.push(p.clone());
    }

    paths
}

fn load_config(explicit_path: &Option<PathBuf>) -> Result<AppConfig, Box<dyn std::error::Error>> {
    let mut config = AppConfig::default();
    let paths = find_config_files(explicit_path);

    for path in &paths {
        let content = fs::read_to_string(path).map_err(|e| {
            format!("Failed to read config file '{}': {}", path.display(), e)
        })?;

        let file_config: AppConfig = toml::from_str(&content).map_err(|e| {
            format!("Failed to parse '{}': {}", path.display(), e)
        })?;

        merge_config(&mut config, &file_config, &content);
    }

    // Environment variable overrides
    apply_env_overrides(&mut config);

    Ok(config)
}

fn merge_config(base: &mut AppConfig, overlay: &AppConfig, raw: &str) {
    // Only override fields that were actually specified in the file.
    // This is a simplified approach — for production, consider using
    // a crate like `figment` or `config` that handles this natively.
    let table: toml::Table = toml::from_str(raw).unwrap_or_default();

    if let Some(server) = table.get("server").and_then(|v| v.as_table()) {
        if server.contains_key("host") {
            base.server.host = overlay.server.host.clone();
        }
        if server.contains_key("port") {
            base.server.port = overlay.server.port;
        }
        if server.contains_key("workers") {
            base.server.workers = overlay.server.workers;
        }
        if server.contains_key("timeout_secs") {
            base.server.timeout_secs = overlay.server.timeout_secs;
        }
    }

    if let Some(db) = table.get("database").and_then(|v| v.as_table()) {
        if db.contains_key("url") {
            base.database.url = overlay.database.url.clone();
        }
        if db.contains_key("max_connections") {
            base.database.max_connections = overlay.database.max_connections;
        }
        if db.contains_key("ssl") {
            base.database.ssl = overlay.database.ssl;
        }
    }

    if let Some(log) = table.get("logging").and_then(|v| v.as_table()) {
        if log.contains_key("level") {
            base.logging.level = overlay.logging.level.clone();
        }
        if log.contains_key("file") {
            base.logging.file = overlay.logging.file.clone();
        }
        if log.contains_key("json") {
            base.logging.json = overlay.logging.json;
        }
    }
}

fn apply_env_overrides(config: &mut AppConfig) {
    if let Ok(host) = std::env::var("MYAPP_HOST") {
        config.server.host = host;
    }
    if let Ok(port) = std::env::var("MYAPP_PORT") {
        if let Ok(p) = port.parse() {
            config.server.port = p;
        }
    }
    if let Ok(url) = std::env::var("MYAPP_DATABASE_URL") {
        config.database.url = url;
    }
    if let Ok(level) = std::env::var("MYAPP_LOG_LEVEL") {
        config.logging.level = level;
    }
}
```

That merge function is admittedly verbose. In production, I'd reach for the `figment` crate which handles layered configuration natively. But it's worth understanding what's happening under the hood.

## The Cleaner Way: figment

If you don't want to write merge logic by hand, `figment` is purpose-built for this:

```toml
[dependencies]
figment = { version = "0.10", features = ["toml", "env"] }
serde = { version = "1", features = ["derive"] }
```

```rust
use figment::{Figment, providers::{Format, Toml, Env, Serialized}};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct Config {
    host: String,
    port: u16,
    database_url: String,
    log_level: String,
    workers: usize,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".to_string(),
            port: 8080,
            database_url: "postgres://localhost/myapp".to_string(),
            log_level: "info".to_string(),
            workers: 4,
        }
    }
}

fn load_config() -> Result<Config, figment::Error> {
    Figment::new()
        // Layer 1: compiled defaults
        .merge(Serialized::defaults(Config::default()))
        // Layer 2: user config
        .merge(Toml::file("~/.config/myapp/config.toml"))
        // Layer 3: project-local config
        .merge(Toml::file(".myapp.toml"))
        // Layer 4: environment variables (MYAPP_HOST, MYAPP_PORT, etc.)
        .merge(Env::prefixed("MYAPP_"))
        .extract()
}

fn main() {
    match load_config() {
        Ok(config) => println!("{:#?}", config),
        Err(e) => {
            eprintln!("Configuration error: {}", e);
            std::process::exit(1);
        }
    }
}
```

That's it. Four lines and you have layered configuration with sensible precedence. Missing files are silently skipped. Env vars are mapped automatically. Type conversion happens through serde.

## Connecting clap and Config

The trickiest part: CLI flags should override config file values. Here's the pattern that works:

```rust
use clap::Parser;
use serde::Deserialize;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(name = "myapp")]
struct Cli {
    /// Config file path
    #[arg(short, long)]
    config: Option<PathBuf>,

    /// Server host
    #[arg(long)]
    host: Option<String>,

    /// Server port
    #[arg(long)]
    port: Option<u16>,

    /// Log level
    #[arg(long)]
    log_level: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(default)]
struct Config {
    host: String,
    port: u16,
    log_level: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            host: "127.0.0.1".to_string(),
            port: 8080,
            log_level: "info".to_string(),
        }
    }
}

fn main() {
    let cli = Cli::parse();

    // Load config from file
    let mut config = if let Some(ref path) = cli.config {
        let content = std::fs::read_to_string(path)
            .unwrap_or_else(|e| {
                eprintln!("Failed to read config '{}': {}", path.display(), e);
                std::process::exit(1);
            });
        toml::from_str::<Config>(&content)
            .unwrap_or_else(|e| {
                eprintln!("Failed to parse config: {}", e);
                std::process::exit(1);
            })
    } else {
        Config::default()
    };

    // CLI flags override config file values
    if let Some(host) = cli.host {
        config.host = host;
    }
    if let Some(port) = cli.port {
        config.port = port;
    }
    if let Some(level) = cli.log_level {
        config.log_level = level;
    }

    println!("Final config: {:#?}", config);
}
```

The trick is using `Option<T>` for CLI fields that can also come from config. `None` means "not specified on the command line, use the config file value." If you used a plain `String` with a default value, you'd have no way to tell whether the user passed `--host 127.0.0.1` explicitly or whether it's just the default.

## Config File Init Command

Good CLI tools generate their own config files. Don't make users guess the format:

```rust
fn generate_default_config() -> String {
    r#"# myapp configuration
# See: https://myapp.dev/docs/config

[server]
# host = "127.0.0.1"
# port = 8080
# workers = 4        # defaults to number of CPU cores
# timeout_secs = 30

[database]
# url = "postgres://localhost/myapp"
# max_connections = 10
# ssl = false

[logging]
# level = "info"     # trace, debug, info, warn, error
# file = "/var/log/myapp.log"
# json = false
"#
    .to_string()
}

fn init_config(path: &std::path::Path) -> std::io::Result<()> {
    if path.exists() {
        eprintln!(
            "Config file '{}' already exists. Use --force to overwrite.",
            path.display()
        );
        std::process::exit(1);
    }

    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    std::fs::write(path, generate_default_config())?;
    eprintln!("Created config file: {}", path.display());
    Ok(())
}
```

Everything is commented out by default, with the default values shown. Users uncomment only what they want to change. This is how `gitconfig` and `ssh_config` work, and it's the expected UX.

## Secrets in Config

One thing to never put in a config file: secrets. API keys, database passwords, tokens — these should come from environment variables or a secrets manager. Your config file will end up in version control. Your `.env` file might too, despite `.gitignore`.

```rust
fn load_secrets(config: &mut Config) {
    // Required secret — fail loudly if missing
    config.database.url = std::env::var("DATABASE_URL")
        .unwrap_or_else(|_| {
            eprintln!("Error: DATABASE_URL environment variable is required");
            std::process::exit(1);
        });

    // Optional secret with fallback
    if let Ok(token) = std::env::var("API_TOKEN") {
        config.api_token = Some(token);
    }
}
```

Don't be clever with this. Environment variables for secrets. Config files for everything else. It's a solved problem — don't try to improve on it.

The config story in Rust is solid once you understand the layering. Next lesson — making your terminal output actually look good with colors and progress bars.
