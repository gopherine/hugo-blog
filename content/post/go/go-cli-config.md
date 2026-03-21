---
title: 'Lesson 2: Config and Env Handling — Viper, envconfig, or just os.Getenv?'
author: Atharva Pandey
series: "Go CLI & Tooling"
lesson: 2
keywords:
  - Go
  - golang
  - CLI
  - configuration
  - Viper
  - envconfig
  - os.Getenv
tags:
  - Go tutorial
  - golang
  - CLI
date: '2024-08-05T00:00:00.000Z'
---

Configuration is one of those problems that looks trivial until it is not. A single environment variable is three lines of code. A configuration file with overridable environment variables, sensible defaults, validation, and reload-on-signal is a project in itself. Knowing when to use each approach — raw `os.Getenv`, struct-based env decoding, or a full configuration library like Viper — is more about understanding the tradeoffs than about which library is "best."

I have used all three approaches in production. Each one earned its place in a specific context. The mistake is reaching for the heaviest tool by default.

## The Problem

The minimal approach breaks down faster than you expect:

```go
// WRONG — scattered os.Getenv calls with no validation or defaults
func startServer() error {
    host := os.Getenv("DB_HOST")
    portStr := os.Getenv("DB_PORT")
    user := os.Getenv("DB_USER")
    pass := os.Getenv("DB_PASSWORD")
    dbName := os.Getenv("DB_NAME")

    port, err := strconv.Atoi(portStr)
    if err != nil {
        port = 5432 // silent default
    }

    connStr := fmt.Sprintf("host=%s port=%d user=%s password=%s dbname=%s",
        host, port, user, pass, dbName)

    db, err := sql.Open("postgres", connStr)
    // ...
}
```

This has no validation — if `DB_HOST` is empty, you get a confusing error at connection time, not at startup. The default for port is silently applied. The required vs. optional distinction is invisible. And these env var reads are scattered throughout the codebase — there is no single place to see what configuration the application needs.

The second problem is that this approach does not support configuration files, which CLI tools often need for user-specific settings that should persist across invocations.

## The Idiomatic Way

For applications and services, struct-based configuration with a single parse-at-startup pattern is almost always the right answer. The configuration struct becomes your single source of truth:

```go
// config/config.go — a dedicated config struct parsed once at startup
package config

import (
    "fmt"
    "os"
    "strconv"
    "time"
)

type Config struct {
    Database DatabaseConfig
    Server   ServerConfig
    Log      LogConfig
}

type DatabaseConfig struct {
    Host         string
    Port         int
    User         string
    Password     string
    Name         string
    MaxConns     int
    ConnTimeout  time.Duration
}

type ServerConfig struct {
    Addr         string
    ReadTimeout  time.Duration
    WriteTimeout time.Duration
}

type LogConfig struct {
    Level  string
    Format string // "json" or "text"
}

func Load() (*Config, error) {
    cfg := &Config{
        Database: DatabaseConfig{
            Host:        getEnv("DB_HOST", "localhost"),
            Port:        getEnvInt("DB_PORT", 5432),
            User:        getEnv("DB_USER", "app"),
            Password:    mustGetEnv("DB_PASSWORD"), // required
            Name:        getEnv("DB_NAME", "appdb"),
            MaxConns:    getEnvInt("DB_MAX_CONNS", 25),
            ConnTimeout: getEnvDuration("DB_CONN_TIMEOUT", 5*time.Second),
        },
        Server: ServerConfig{
            Addr:         getEnv("SERVER_ADDR", ":8080"),
            ReadTimeout:  getEnvDuration("SERVER_READ_TIMEOUT", 30*time.Second),
            WriteTimeout: getEnvDuration("SERVER_WRITE_TIMEOUT", 30*time.Second),
        },
        Log: LogConfig{
            Level:  getEnv("LOG_LEVEL", "info"),
            Format: getEnv("LOG_FORMAT", "json"),
        },
    }
    return cfg, nil
}

func getEnv(key, fallback string) string {
    if v := os.Getenv(key); v != "" {
        return v
    }
    return fallback
}

func mustGetEnv(key string) string {
    v := os.Getenv(key)
    if v == "" {
        panic(fmt.Sprintf("required environment variable %q is not set", key))
    }
    return v
}

func getEnvInt(key string, fallback int) int {
    v := os.Getenv(key)
    if v == "" {
        return fallback
    }
    n, err := strconv.Atoi(v)
    if err != nil {
        panic(fmt.Sprintf("environment variable %q must be an integer, got %q", key, v))
    }
    return n
}

func getEnvDuration(key string, fallback time.Duration) time.Duration {
    v := os.Getenv(key)
    if v == "" {
        return fallback
    }
    d, err := time.ParseDuration(v)
    if err != nil {
        panic(fmt.Sprintf("environment variable %q must be a duration (e.g. 5s), got %q", key, v))
    }
    return d
}
```

For CLI tools that need user-specific config files, Viper's layered approach is appropriate. It merges defaults, config files, and environment variables in a defined precedence order:

```go
// Using Viper for a CLI with config file support
package cmd

import (
    "github.com/spf13/cobra"
    "github.com/spf13/viper"
)

func initConfig() {
    if cfgFile != "" {
        viper.SetConfigFile(cfgFile)
    } else {
        home, _ := os.UserHomeDir()
        viper.AddConfigPath(home)
        viper.AddConfigPath(".")
        viper.SetConfigName(".myapp")
        viper.SetConfigType("yaml")
    }

    // Environment variables override file config
    viper.AutomaticEnv()
    viper.SetEnvPrefix("MYAPP")

    // Defaults
    viper.SetDefault("output", "table")
    viper.SetDefault("timeout", "30s")

    if err := viper.ReadInConfig(); err != nil {
        if _, ok := err.(viper.ConfigFileNotFoundError); !ok {
            // Config file found but could not be parsed
            fmt.Fprintln(os.Stderr, "Error reading config:", err)
            os.Exit(1)
        }
        // Config file not found: that is fine, use defaults + env
    }
}
```

## In The Wild

The configuration approach that has worked best for me in production services is a strict separation: all configuration is read at startup, validated immediately, and passed to components as explicit arguments. Nothing reads environment variables after `main` starts.

```go
// main.go — config is loaded, validated, and passed down
func main() {
    cfg, err := config.Load()
    if err != nil {
        slog.Error("failed to load config", "error", err)
        os.Exit(1)
    }

    db, err := database.New(cfg.Database)
    if err != nil {
        slog.Error("failed to connect to database", "error", err)
        os.Exit(1)
    }

    srv := server.New(cfg.Server, db)
    // ...
}
```

This pattern makes configuration dependencies explicit. When you trace through the code, you can always see what configuration each component uses — it is in the function signature, not hidden behind a global `os.Getenv` call somewhere in a subpackage.

For CLI tools where users configure preferences locally (output format, default namespace, API token), I use a minimal Viper setup with the config file living at `~/.myapp.yaml`. But I decode it into a typed struct immediately rather than calling `viper.GetString` scattered throughout the command tree:

```go
type CLIConfig struct {
    APIToken  string `mapstructure:"api_token"`
    Namespace string `mapstructure:"namespace"`
    Output    string `mapstructure:"output"`
}

func loadCLIConfig() (*CLIConfig, error) {
    var cfg CLIConfig
    if err := viper.Unmarshal(&cfg); err != nil {
        return nil, fmt.Errorf("decoding config: %w", err)
    }
    return &cfg, nil
}
```

One call to `viper.Unmarshal`, and then the rest of the code uses typed fields on `CLIConfig`. No stringly-typed config key lookups scattered across files.

## The Gotchas

**`panic` vs. `os.Exit` in config loading.** Using `panic` for missing required config is reasonable during early startup (before logging is initialized), but it produces a stack trace that is ugly for end users. For user-facing CLIs, validate configuration and return a clear error instead. For server deployments where misconfiguration is an operator error, `panic` or `log.Fatal` at startup are acceptable.

**Environment variable expansion.** Viper does not expand shell variables in values by default. If a user puts `~/.ssh/key` in their config file, it will not be expanded to their home directory. Handle this explicitly with `os.ExpandEnv` if you need it.

**Test isolation with environment variables.** Tests that read environment variables can interfere with each other if they run in parallel. Use `t.Setenv` (Go 1.17+) which automatically restores the original value after the test, or restructure tests to accept the config struct directly rather than reading from the environment.

## Key Takeaway

The right tool depends on the use case: raw `os.Getenv` for simple scripts and lambdas; struct-based loading with explicit defaults for services; Viper for CLI tools that need config files. Whichever approach you choose, the pattern that works best is load once at startup, validate immediately, pass as explicit arguments. The applications that are hardest to debug in production are the ones where configuration is read lazily in the middle of a request handler. Load it early, fail fast if it is wrong, and make the configuration an explicit dependency instead of a hidden global.

---

← [Lesson 1: Building CLIs with Cobra](/post/go/go-cli-cobra) | [Course Index](/series/go-cli-tooling) | [Next → Lesson 3: File I/O Patterns](/post/go/go-cli-file-io)
