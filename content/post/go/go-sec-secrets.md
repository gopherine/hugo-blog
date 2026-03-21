---
title: "Lesson 2: Secret Handling — Env vars are not a vault"
author: Atharva Pandey
series: "Go Security in Production"
lesson: 2
keywords:
  - Go
  - golang
  - security
tags:
  - Go tutorial
  - golang
  - security
date: '2024-07-08T00:00:00.000Z'
---

I once found database credentials committed directly in a Go source file — not in a toy project, in a staging environment of a real product. The developer who wrote it knew it was wrong, but they were "just testing" and forgot to revert it. That file lived in the repository for eight months before anyone noticed. By then, the credentials had been rotated, but the history was permanent.

Secret handling in Go is not primarily a coding problem. It is a discipline problem. The code patterns are simple. The failures happen when you treat secrets as a detail to clean up later, or when you assume that environment variables are inherently safe because they are not in source code.

## The Problem

Hardcoded secrets are the obvious failure, but they are surprisingly common in real codebases:

```go
// WRONG — hardcoded credentials in source
const dbDSN = "postgres://admin:SuperSecret123@prod-db.internal:5432/myapp"

func connectDB() (*sql.DB, error) {
    return sql.Open("postgres", dbDSN)
}
```

Every developer who clones the repo has those credentials. Every CI log that prints the DSN leaks them. Every container image that bakes the binary in and then runs `strings` on it exposes them.

A subtler mistake is logging secrets. This happens constantly in debug code that never gets removed:

```go
// WRONG — logging secrets via Sprintf or fmt.Println
func initDB(cfg Config) {
    log.Printf("connecting to database: %s", cfg.DatabaseURL) // full DSN with password
    log.Printf("using API key: %s", cfg.APIKey)
}
```

Log aggregators, monitoring dashboards, and alerting systems all store this output. Your secrets are now in Datadog, CloudWatch, or wherever your logs go — indexed, searchable, and retained for years.

A third anti-pattern is treating environment variables as the final destination for secrets rather than a transport mechanism:

```go
// WRONG — treating env vars as authoritative secret store
func getSecrets() (string, string) {
    dbPass := os.Getenv("DB_PASSWORD")   // plaintext in env, visible in /proc/<pid>/environ
    apiKey := os.Getenv("STRIPE_KEY")    // accessible to any process running as same user
    return dbPass, apiKey
}
```

Environment variables are visible in `/proc/<pid>/environ` on Linux, logged by some process supervisors, and exposed by container inspection commands. They are fine as an injection mechanism from a real secret store, but they are not themselves a vault.

## The Idiomatic Way

The right pattern is a `Secrets` struct loaded once at startup from a real secret store, with all fields treated as sensitive throughout their lifetime:

```go
// RIGHT — load secrets from a vault client at startup
import (
    "context"
    vault "github.com/hashicorp/vault/api"
)

type Secrets struct {
    DatabaseURL string
    StripeKey   string
    JWTSecret   []byte
}

func loadSecrets(ctx context.Context) (Secrets, error) {
    client, err := vault.NewClient(vault.DefaultConfig())
    if err != nil {
        return Secrets{}, fmt.Errorf("vault client: %w", err)
    }

    secret, err := client.KVv2("secret").Get(ctx, "myapp/production")
    if err != nil {
        return Secrets{}, fmt.Errorf("vault read: %w", err)
    }

    return Secrets{
        DatabaseURL: secret.Data["db_url"].(string),
        StripeKey:   secret.Data["stripe_key"].(string),
        JWTSecret:   []byte(secret.Data["jwt_secret"].(string)),
    }, nil
}
```

If you are using a simpler setup — AWS Secrets Manager, for example — the same pattern applies: load at startup, inject via constructor, never log.

For local development where a full vault is impractical, use a `.env` file that is `.gitignore`d from day one, and load it with a library like `github.com/joho/godotenv`:

```go
// RIGHT — dev-only env file loading, never in production
func main() {
    if os.Getenv("APP_ENV") == "development" {
        if err := godotenv.Load(); err != nil {
            log.Println("no .env file found, using environment")
        }
    }

    secrets, err := loadSecrets(context.Background())
    if err != nil {
        log.Fatalf("failed to load secrets: %v", err)
    }

    app := NewApp(secrets)
    app.Run()
}
```

The key constraint: the `Secrets` struct is constructed once, injected everywhere it is needed, and never touches a log line.

## In The Wild

**Secret zero.** The vault client itself needs a credential to authenticate. This is the bootstrap problem. Cloud providers solve it elegantly: in AWS, your EC2 instance or Lambda has an IAM role, and the vault SDK retrieves a short-lived token automatically using that role. No static credential is ever present. If you are on bare metal or a provider without instance identity, use Vault's AppRole or Kubernetes auth instead of a static token.

**Secret rotation.** Load secrets at startup is correct for most applications, but secrets rotate. A database password rotation will invalidate your DSN and your connections will start failing. Build a reload path: either restart the process when a secret rotates (simplest, works well with Kubernetes rolling updates) or implement a watcher that re-fetches secrets and rebuilds the connection pool.

**Memory safety.** Go does not give you control over when memory is garbage collected or copied. A `string` containing a password could be copied to multiple heap locations before it is released. For extremely sensitive material — private keys, symmetric encryption keys — use `golang.org/x/crypto/memguard` or similar to pin and zero memory explicitly. For most application secrets (database passwords, API keys), this level of care is not warranted, but it is worth knowing the option exists.

## The Gotchas

**`fmt.Sprintf` in error messages.** When a connection fails, it is tempting to include the DSN in the error for debugging:

```go
// WRONG — DSN in error message will end up in logs
return fmt.Errorf("failed to connect to %s: %w", cfg.DatabaseURL, err)

// RIGHT — reference the config key, not the value
return fmt.Errorf("failed to connect to database (check DATABASE_URL): %w", err)
```

**Panicking with secrets.** If a function that processes a secret panics, the stack trace printed by the runtime may include the string value. Recover panics at your middleware boundary and log only the error type and message, not the full stack dump, in production.

**Dependency injection vs. global variables.** A global `var db *sql.DB` is convenient but it means the database credential used to create the connection is effectively in process-wide scope. Prefer passing connections through constructors. If you must use globals, initialize them in `main` and treat them as read-only after that point.

**CI/CD secrets.** GitHub Actions, GitLab CI, and similar systems support repository secrets injected as environment variables during build. These are fine for CI, but be careful about `--build-arg` in Docker — build arguments are baked into the image layer history. Use multi-stage builds and only copy the final binary, or use BuildKit's secret mount syntax.

## Key Takeaway

Environment variables are a delivery mechanism, not a vault. Secrets belong in a dedicated secret store, loaded once at startup, injected through constructors, and never printed. The discipline of never logging a value that came from a secret field is the single habit that will prevent the most incidents — because log pipelines are where secrets escape most often in practice.

---

**Go Security in Production**

Previous: [Lesson 1: Input Validation — Trust nothing from the wire](/post/go/go-sec-input-validation/)
Next: [Lesson 3: TLS Configuration — Your default HTTP server is unencrypted](/post/go/go-sec-tls/)
