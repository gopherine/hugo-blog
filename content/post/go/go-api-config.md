---
title: "Lesson 9: Config Management — Twelve-factor or twelve headaches"
author: Atharva Pandey
series: "Go API and Service Design"
lesson: 9
keywords: [Go, golang, API, backend, config, configuration, environment variables, twelve-factor]
tags: [Go tutorial, golang, backend]
date: '2025-05-10T00:00:00.000Z'
---

I have seen configuration managed in at least ten different ways across the Go projects I have worked on: hardcoded constants, config structs passed around by pointer, global variables read at startup, YAML files baked into Docker images, environment variables with no validation, and one particularly creative approach involving a shared Google Sheet. Every one of those had the same core problem: the configuration was not treated as a first-class part of the application.

## The Problem

Configuration management goes wrong in a few predictable ways. The most common is treating config as an afterthought — you get it working for development and then scramble to parameterise it when you need to deploy to production. You end up with a mix of hardcoded values and environment variables, no validation, and no documentation of what variables even exist.

The second failure mode is reading config throughout the codebase — `os.Getenv("DB_HOST")` sprinkled across your database package, your HTTP client, your metrics setup. When a variable name changes or a new required value is added, tracking down all the call sites is painful.

The third failure mode is no validation at startup. A service that starts successfully but silently uses a zero-value for a missing required config is much harder to debug than a service that refuses to start with a clear error message.

## The Idiomatic Way

Load all configuration once at startup into a validated struct. Pass that struct (or sub-structs of it) explicitly into the components that need it. Never read environment variables outside the config loading code.

```go
package config

import (
    "fmt"
    "os"
    "strconv"
    "strings"
    "time"
)

type Config struct {
    Server   ServerConfig
    Database DatabaseConfig
    Auth     AuthConfig
    Observability ObservabilityConfig
}

type ServerConfig struct {
    Port            int
    ReadTimeout     time.Duration
    WriteTimeout    time.Duration
    IdleTimeout     time.Duration
    ShutdownTimeout time.Duration
}

type DatabaseConfig struct {
    DSN             string
    MaxOpenConns    int
    MaxIdleConns    int
    ConnMaxLifetime time.Duration
}

type AuthConfig struct {
    JWTSecret     string
    TokenExpiry   time.Duration
}

type ObservabilityConfig struct {
    LogLevel    string
    MetricsAddr string
    Environment string
}

// Load reads configuration from environment variables and validates it.
// It returns an error immediately if any required variable is missing or invalid.
func Load() (*Config, error) {
    var errs []string

    cfg := &Config{}

    // Server
    cfg.Server.Port = envInt("PORT", 8080)
    cfg.Server.ReadTimeout = envDuration("SERVER_READ_TIMEOUT", 5*time.Second)
    cfg.Server.WriteTimeout = envDuration("SERVER_WRITE_TIMEOUT", 10*time.Second)
    cfg.Server.IdleTimeout = envDuration("SERVER_IDLE_TIMEOUT", 120*time.Second)
    cfg.Server.ShutdownTimeout = envDuration("SERVER_SHUTDOWN_TIMEOUT", 30*time.Second)

    // Database
    if dsn := os.Getenv("DATABASE_URL"); dsn == "" {
        errs = append(errs, "DATABASE_URL is required")
    } else {
        cfg.Database.DSN = dsn
    }
    cfg.Database.MaxOpenConns = envInt("DB_MAX_OPEN_CONNS", 25)
    cfg.Database.MaxIdleConns = envInt("DB_MAX_IDLE_CONNS", 5)
    cfg.Database.ConnMaxLifetime = envDuration("DB_CONN_MAX_LIFETIME", 5*time.Minute)

    // Auth
    if secret := os.Getenv("JWT_SECRET"); secret == "" {
        errs = append(errs, "JWT_SECRET is required")
    } else if len(secret) < 32 {
        errs = append(errs, "JWT_SECRET must be at least 32 characters")
    } else {
        cfg.Auth.JWTSecret = secret
    }
    cfg.Auth.TokenExpiry = envDuration("JWT_TOKEN_EXPIRY", 24*time.Hour)

    // Observability
    cfg.Observability.LogLevel = envOneOf("LOG_LEVEL", "info", "debug", "info", "warn", "error")
    cfg.Observability.MetricsAddr = envDefault("METRICS_ADDR", ":9090")
    cfg.Observability.Environment = envDefault("ENVIRONMENT", "development")

    if len(errs) > 0 {
        return nil, fmt.Errorf("configuration errors:\n  - %s", strings.Join(errs, "\n  - "))
    }

    return cfg, nil
}
```

The helper functions keep the loading code clean:

```go
func envDefault(key, defaultVal string) string {
    if v := os.Getenv(key); v != "" {
        return v
    }
    return defaultVal
}

func envInt(key string, defaultVal int) int {
    v := os.Getenv(key)
    if v == "" {
        return defaultVal
    }
    n, err := strconv.Atoi(v)
    if err != nil {
        return defaultVal
    }
    return n
}

func envDuration(key string, defaultVal time.Duration) time.Duration {
    v := os.Getenv(key)
    if v == "" {
        return defaultVal
    }
    d, err := time.ParseDuration(v)
    if err != nil {
        return defaultVal
    }
    return d
}

func envOneOf(key, defaultVal string, allowed ...string) string {
    v := os.Getenv(key)
    if v == "" {
        return defaultVal
    }
    for _, a := range allowed {
        if v == a {
            return v
        }
    }
    return defaultVal
}
```

## In The Wild

In `main()`, the config is loaded first, before anything else is initialised. If it fails, the service exits with a clear error:

```go
func main() {
    cfg, err := config.Load()
    if err != nil {
        fmt.Fprintf(os.Stderr, "fatal: %v\n", err)
        os.Exit(1)
    }

    logger := buildLogger(cfg.Observability.LogLevel)

    db, err := openDB(cfg.Database)
    if err != nil {
        logger.Error("failed to connect to database", "error", err)
        os.Exit(1)
    }
    defer db.Close()

    srv := api.NewServer(cfg.Server, db, logger)

    // ... start server
}

func openDB(cfg config.DatabaseConfig) (*sql.DB, error) {
    db, err := sql.Open("postgres", cfg.DSN)
    if err != nil {
        return nil, err
    }
    db.SetMaxOpenConns(cfg.Database.MaxOpenConns)
    db.SetMaxIdleConns(cfg.Database.MaxIdleConns)
    db.SetConnMaxLifetime(cfg.Database.ConnMaxLifetime)
    if err := db.PingContext(context.Background()); err != nil {
        return nil, fmt.Errorf("database ping failed: %w", err)
    }
    return db, nil
}
```

Notice that `openDB` takes a `config.DatabaseConfig`, not the full `*config.Config`. Each component only receives the configuration it needs. This makes testing easier (you can construct a `DatabaseConfig` directly in a test) and makes the dependencies explicit.

For local development, a `.env` file loaded with a minimal helper avoids polluting the shell environment:

```go
// loadDotEnv reads a .env file if it exists (development only)
func loadDotEnv(path string) {
    f, err := os.Open(path)
    if err != nil {
        return // file is optional
    }
    defer f.Close()

    scanner := bufio.NewScanner(f)
    for scanner.Scan() {
        line := strings.TrimSpace(scanner.Text())
        if line == "" || strings.HasPrefix(line, "#") {
            continue
        }
        parts := strings.SplitN(line, "=", 2)
        if len(parts) != 2 {
            continue
        }
        key := strings.TrimSpace(parts[0])
        val := strings.Trim(strings.TrimSpace(parts[1]), `"'`)
        if os.Getenv(key) == "" { // do not override real env vars
            os.Setenv(key, val)
        }
    }
}
```

## The Gotchas

**Never commit `.env` files with secrets.** The `.env` file is for local development values only. Add it to `.gitignore`. Use your platform's secret management (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault) for anything that is secret in production. Pull secrets at startup and inject them as environment variables.

**Config structs should not be global.** Resist the temptation to store `*config.Config` in a package-level variable. Global state makes tests fragile and dependencies invisible. Pass configuration through constructors. Your test for a handler that needs a JWT secret should be able to construct that handler with a test secret in a single line.

**Validate bounds, not just presence.** A `DATABASE_URL` set to an empty string after trimming is as bad as one that is absent. A `MAX_OPEN_CONNS=0` will mean the database package uses its default (which might not be what you want). Validate not just that a value is present but that it is in a reasonable range.

**Document your config.** I keep a `.env.example` file in every project — a copy of the expected environment variables with placeholder values and comments explaining what each one does. New team members and deployment scripts use it as the reference. When you add a new required variable, update `.env.example` first.

## Key Takeaway

Configuration is code. Treat it with the same discipline you would any other part of your application: validate at startup, fail fast on missing required values, document every variable, and never scatter `os.Getenv` calls throughout your codebase. A single `config.Load()` call at startup that returns either a fully validated `*Config` or a clear list of errors makes your service easier to deploy, easier to test, and nearly impossible to misconfigure silently.

---

**Series: Go API and Service Design**

[← Lesson 8: Rate Limiting](/post/go/go-api-rate-limiting) | [Lesson 10: The Complete Go Service →](/post/go/go-api-complete-service)
