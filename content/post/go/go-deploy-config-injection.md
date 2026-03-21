---
title: 'Lesson 4: Config Injection — Environment variables, flags, files — in that order'
author: Atharva Pandey
series: "Go Deployment & Operations"
lesson: 4
keywords:
  - Go
  - golang
  - configuration
  - environment variables
  - flags
  - config files
  - 12-factor
  - deployment
tags:
  - Go tutorial
  - golang
  - deployment
  - devops
date: '2024-11-02T00:00:00.000Z'
---

Configuration management is one of those topics that seems solved until you maintain a service that runs in four different environments, has twenty configurable parameters, and needs its secrets rotated without a redeployment. I've gone through several evolutionary stages on this: hardcoded values (the naive phase), a single `config.yaml` file, then twelve `config-{env}.yaml` files, and finally landing on what the 12-factor app methodology describes — environment variables as the source of truth for deployment-specific configuration.

The order in my title — environment variables, then flags, then files — reflects a deliberate precedence hierarchy. Environment variables win because they're easiest to inject in containerized environments and to manage in Kubernetes secrets. Flags win over files because they're explicit and visible in process listings. Files are a fallback for complex structured configuration that's awkward to express in flat key-value form.

## The Problem

The classic mistake is reading config from a YAML file with no override mechanism:

```go
// WRONG — config is baked into a file that travels with the binary
type Config struct {
    DatabaseURL string `yaml:"database_url"` // contains a password
    Port        int    `yaml:"port"`
    Debug       bool   `yaml:"debug"`
}

func loadConfig() (*Config, error) {
    data, err := os.ReadFile("config.yaml")
    if err != nil {
        return nil, err
    }
    var cfg Config
    return &cfg, yaml.Unmarshal(data, &cfg)
}
```

Problems: `config.yaml` contains secrets that shouldn't be in version control. There's no way to override individual values without modifying the file. Different environments (staging, production) require different files, which means different deployment artifacts. And Go binaries built correctly are static — shipping a YAML file alongside them reintroduces a deployment artifact management problem.

The second common mistake is parsing flags ad hoc:

```go
// WRONG — scattered flag parsing with no central config struct
var (
    port    = flag.Int("port", 8080, "listen port")
    debug   = flag.Bool("debug", false, "enable debug logging")
    dbURL   = flag.String("db-url", "", "database URL")
)

func main() {
    flag.Parse()
    // now these are accessible anywhere via *port, *debug, *dbURL
}
```

This is better, but there's no environment variable override, validation is missing, and the config is scattered rather than centralized in a typed struct.

## The Idiomatic Way

The pattern I settled on: define a typed config struct, populate it from environment variables by default, allow flag overrides, and validate it at startup:

```go
package config

import (
    "flag"
    "fmt"
    "os"
    "strconv"
    "time"
)

// Config holds all service configuration. Fields are populated in order:
// 1. Defaults (defined here)
// 2. Environment variables
// 3. Command-line flags (highest precedence)
type Config struct {
    // Server
    Port            int
    ReadTimeout     time.Duration
    WriteTimeout    time.Duration

    // Database
    DatabaseURL     string
    DBMaxConns      int

    // Feature flags
    Debug           bool
    EnableTracing   bool
}

// Load populates Config from environment variables and flags.
func Load() (*Config, error) {
    cfg := &Config{
        // Sensible defaults
        Port:         8080,
        ReadTimeout:  15 * time.Second,
        WriteTimeout: 30 * time.Second,
        DBMaxConns:   25,
    }

    // Layer 1: environment variables
    if v := os.Getenv("PORT"); v != "" {
        p, err := strconv.Atoi(v)
        if err != nil {
            return nil, fmt.Errorf("PORT must be an integer: %w", err)
        }
        cfg.Port = p
    }
    if v := os.Getenv("DATABASE_URL"); v != "" {
        cfg.DatabaseURL = v
    }
    if v := os.Getenv("DB_MAX_CONNS"); v != "" {
        n, err := strconv.Atoi(v)
        if err != nil {
            return nil, fmt.Errorf("DB_MAX_CONNS must be an integer: %w", err)
        }
        cfg.DBMaxConns = n
    }
    cfg.Debug = os.Getenv("DEBUG") == "true" || os.Getenv("DEBUG") == "1"
    cfg.EnableTracing = os.Getenv("ENABLE_TRACING") == "true"

    // Layer 2: flags override environment variables
    flag.IntVar(&cfg.Port, "port", cfg.Port, "listen port (overrides PORT env)")
    flag.StringVar(&cfg.DatabaseURL, "db-url", cfg.DatabaseURL, "database URL (overrides DATABASE_URL env)")
    flag.BoolVar(&cfg.Debug, "debug", cfg.Debug, "enable debug logging (overrides DEBUG env)")
    flag.Parse()

    // Validate required fields
    if cfg.DatabaseURL == "" {
        return nil, fmt.Errorf("DATABASE_URL or -db-url is required")
    }

    return cfg, nil
}
```

The key insight: flags initialize from the already-populated config values, so the default shown in `--help` output reflects the current environment variable, not the hardcoded default. This makes the service's effective configuration visible and auditable.

For more complex configuration with many fields, the `github.com/kelseyhightower/envconfig` library eliminates the boilerplate while keeping the environment-first approach:

```go
package config

import (
    "time"
    "github.com/kelseyhightower/envconfig"
)

type Config struct {
    Port           int           `envconfig:"PORT"          default:"8080"`
    DatabaseURL    string        `envconfig:"DATABASE_URL"  required:"true"`
    DBMaxConns     int           `envconfig:"DB_MAX_CONNS"  default:"25"`
    ReadTimeout    time.Duration `envconfig:"READ_TIMEOUT"  default:"15s"`
    Debug          bool          `envconfig:"DEBUG"         default:"false"`
    EnableTracing  bool          `envconfig:"ENABLE_TRACING" default:"false"`
}

func Load() (*Config, error) {
    var cfg Config
    if err := envconfig.Process("", &cfg); err != nil {
        return nil, fmt.Errorf("load config: %w", err)
    }
    return &cfg, nil
}
```

`envconfig` reads the struct tags, parses values from environment variables, applies defaults, validates required fields, and returns a nicely formatted error if anything is wrong. Call `envconfig.Usage("", &cfg)` to print a table of all config variables, their types, defaults, and whether they're required — invaluable for writing deployment documentation.

## In The Wild

Secrets deserve special treatment. Never log the config struct — it will log the database password. Add a `String()` method that redacts sensitive fields:

```go
func (c *Config) String() string {
    // Redact anything that looks like a URL with a password
    dbURL := c.DatabaseURL
    if dbURL != "" {
        dbURL = "[redacted]"
    }
    return fmt.Sprintf("port=%d debug=%v db=%s", c.Port, c.Debug, dbURL)
}
```

In Kubernetes, inject secrets as environment variables from a `Secret` resource:

```yaml
env:
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: myapp-secrets
        key: database-url
  - name: PORT
    value: "8080"
  - name: DEBUG
    value: "false"
```

This keeps secrets out of the container image and out of the pod spec (the Secret is separate). Rotation means updating the Secret and rolling the deployment — no image rebuild.

## The Gotchas

**Don't parse flags in `init`.** The `flag.Parse()` call must happen in `main`, after all `flag.XxxVar` registrations. Parsing in `init` or in imported packages breaks the flag registration order and makes testing with custom arguments impossible.

**`os.Getenv` returns empty string for unset AND empty.** If `DATABASE_URL=""` is explicitly set (perhaps as a mistake), `os.Getenv("DATABASE_URL") == ""` is true even though the variable is set. Use `os.LookupEnv` if you need to distinguish "not set" from "set to empty":

```go
if v, ok := os.LookupEnv("DATABASE_URL"); ok {
    cfg.DatabaseURL = v // even if v is empty, the variable was explicitly set
}
```

**Configuration at startup, not on every request.** Load config once at startup and store it in the `Config` struct. Don't call `os.Getenv` in request handlers — it works, but it's slower, untestable, and makes it impossible to do validated config loading.

**Test with config.** Your tests should construct a `Config` directly or use a test helper that returns a known-good test config. Don't rely on environment variables in unit tests — that makes tests environment-dependent.

## Key Takeaway

Environment variables are the right primary mechanism for deployment configuration in containerized Go services. Define a typed config struct, populate it from env vars, allow flag overrides for debugging and development, validate at startup, and redact secrets from logs. The result is a service that's trivially configurable across environments without config file management or build-time environment detection.

---

**Previous:** [Lesson 3: Health Checks](/post/go/go-deploy-health-checks/)
**Next:** [Lesson 5: CI/CD for Go — GitHub Actions that actually catch bugs](/post/go/go-deploy-cicd/)
