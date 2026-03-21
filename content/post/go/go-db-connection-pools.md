---
title: "Lesson 2: Connection Pool Tuning — Your pool config is probably wrong"
author: Atharva Pandey
series: "Go Database Patterns"
lesson: 2
keywords:
  - Go
  - golang
  - database
  - postgres
  - connection pool
  - SetMaxOpenConns
  - SetMaxIdleConns
  - SetConnMaxLifetime
tags:
  - Go tutorial
  - golang
  - database
date: '2025-08-08T00:00:00.000Z'
---

Here's a scenario that played out for me once: service is running fine for weeks, then we double traffic, and suddenly requests start timing out. Not all of them — maybe 10%. The logs show `pq: sorry, too many clients already`. Postgres is refusing connections. But we have a connection pool — that's the whole point, right? Why is Postgres seeing more connections than it can handle?

Because `database/sql`'s default pool settings are almost certainly wrong for production, and most people never change them.

The defaults are: `MaxOpenConns = 0` (unlimited) and `MaxIdleConns = 2`. Unlimited open connections means every goroutine that needs a database connection will try to open one if the idle pool is empty. Under a burst of traffic, you can have hundreds of goroutines all opening connections simultaneously. Postgres has a hard `max_connections` limit — typically 100 on a small instance. You'll hit it fast.

## The Problem

The two failure modes that destroy services are pool exhaustion and idle connection churn. Let me show both:

```go
// WRONG — default pool settings, no limits
func NewDB(dsn string) (*sql.DB, error) {
    db, err := sql.Open("postgres", dsn)
    if err != nil {
        return nil, err
    }
    // No pool configuration at all.
    // MaxOpenConns = 0 (unlimited)
    // MaxIdleConns = 2
    // ConnMaxLifetime = 0 (connections live forever)
    return db, nil
}

// Under load: 500 goroutines all want a DB connection
// They all try to open new ones because MaxOpenConns is unlimited
// Postgres gets 500 connection attempts simultaneously
// Postgres is configured for max_connections=100
// Result: 400 connections refused, panicked service, on-call page at 3am
```

The idle connection problem is subtler. With `MaxIdleConns = 2` and real traffic, your service constantly opens and closes connections. Opening a TCP connection to Postgres involves a TLS handshake, authentication, and session setup — that's easily 5-20ms. At high throughput, this overhead adds up. The pool isn't helping if it's perpetually creating connections because the idle pool is too small to keep up.

```go
// WRONG — MaxIdleConns too low for traffic, causing connection churn
func NewDB(dsn string) (*sql.DB, error) {
    db, err := sql.Open("postgres", dsn)
    if err != nil {
        return nil, err
    }
    db.SetMaxOpenConns(100)
    db.SetMaxIdleConns(2) // Only 2 idle connections held
    // With 50 concurrent requests hitting the DB, 48 of them open new connections
    // The connection establishment overhead kills your p99 latency
    return db, nil
}
```

## The Idiomatic Way

The production-correct approach sets all three parameters deliberately, based on your Postgres config and expected concurrency:

```go
// RIGHT — deliberate pool configuration based on load expectations
func NewDB(dsn string, cfg DBConfig) (*sql.DB, error) {
    db, err := sql.Open("postgres", dsn)
    if err != nil {
        return nil, fmt.Errorf("sql.Open: %w", err)
    }

    // MaxOpenConns: the ceiling on total connections (open + in-use)
    // Rule of thumb: (postgres max_connections / number_of_app_instances) * 0.8
    // If Postgres allows 100 connections and you have 4 app instances:
    // (100 / 4) * 0.8 = 20 per instance
    db.SetMaxOpenConns(cfg.MaxOpenConns)

    // MaxIdleConns: connections held open even when not in use
    // Should be equal to MaxOpenConns for high-throughput services
    // (you want the pool to absorb bursts without connection churn)
    db.SetMaxIdleConns(cfg.MaxIdleConns)

    // ConnMaxLifetime: force-recycle connections after this duration
    // Important for environments where Postgres is behind a load balancer
    // or when using connection proxies like PgBouncer that may close idle conns
    db.SetConnMaxLifetime(cfg.ConnMaxLifetime)

    // ConnMaxIdleTime: close idle connections that haven't been used recently
    // Keeps the pool lean during low-traffic periods
    db.SetConnMaxIdleTime(cfg.ConnMaxIdleTime)

    ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
    defer cancel()

    if err := db.PingContext(ctx); err != nil {
        db.Close()
        return nil, fmt.Errorf("db.Ping: %w", err)
    }

    return db, nil
}

type DBConfig struct {
    MaxOpenConns    int
    MaxIdleConns    int
    ConnMaxLifetime time.Duration
    ConnMaxIdleTime time.Duration
}

// Sensible production defaults for a single Postgres instance
// with max_connections=100, 4 app instances
func DefaultDBConfig() DBConfig {
    return DBConfig{
        MaxOpenConns:    20,
        MaxIdleConns:    20,
        ConnMaxLifetime: 5 * time.Minute,
        ConnMaxIdleTime: 1 * time.Minute,
    }
}
```

Why `MaxIdleConns == MaxOpenConns`? Because if MaxIdleConns is less than MaxOpenConns, connections will be closed after use rather than returned to the idle pool — exactly the churn you're trying to avoid. There's rarely a reason to have them differ unless you have a specific memory constraint.

## In The Wild

Monitoring pool stats is how you know if your configuration is working. `db.Stats()` returns everything you need:

```go
// RIGHT — expose pool metrics for monitoring
func RecordPoolStats(db *sql.DB, serviceName string) {
    go func() {
        ticker := time.NewTicker(15 * time.Second)
        defer ticker.Stop()
        for range ticker.C {
            stats := db.Stats()

            // These are the numbers that matter:
            // InUse: connections actively running a query
            // Idle: connections sitting in the pool ready to use
            // WaitCount: total number of times a goroutine had to wait for a conn
            // WaitDuration: total time spent waiting for connections
            // MaxIdleClosed: connections closed because pool was full
            // MaxLifetimeClosed: connections closed due to ConnMaxLifetime

            log.Printf("[db pool] open=%d inuse=%d idle=%d waitcount=%d waitduration=%s",
                stats.OpenConnections,
                stats.InUse,
                stats.Idle,
                stats.WaitCount,
                stats.WaitDuration,
            )

            // If WaitCount is growing, your MaxOpenConns is too low
            // If MaxIdleClosed is growing, your MaxIdleConns is too low
            // If InUse == MaxOpenConns consistently, you're saturated
        }
    }()
}

// Or, if you're using Prometheus:
func RegisterPoolMetrics(db *sql.DB, reg prometheus.Registerer) {
    openConns := prometheus.NewGaugeFunc(prometheus.GaugeOpts{
        Name: "db_open_connections",
        Help: "Number of open database connections.",
    }, func() float64 { return float64(db.Stats().OpenConnections) })

    inUse := prometheus.NewGaugeFunc(prometheus.GaugeOpts{
        Name: "db_in_use_connections",
        Help: "Number of connections currently in use.",
    }, func() float64 { return float64(db.Stats().InUse) })

    waitCount := prometheus.NewCounterFunc(prometheus.CounterOpts{
        Name: "db_wait_count_total",
        Help: "Total number of connections waited for.",
    }, func() float64 { return float64(db.Stats().WaitCount) })

    reg.MustRegister(openConns, inUse, waitCount)
}
```

Set up a dashboard. If `WaitCount` is nonzero and growing, your pool is too small. If `Idle` is always near zero, your `MaxIdleConns` is too low. If `InUse` is always at `MaxOpenConns`, you're hitting the ceiling and need to either increase it or reduce the number of concurrent database operations.

## The Gotchas

**`ConnMaxLifetime` and load balancers.** If your Postgres is behind an AWS RDS Proxy or a Kubernetes service that load-balances across replicas, connections can get routed to a node that's been removed — and without `ConnMaxLifetime`, those stale connections stay in the pool forever. Setting a lifetime of 5-10 minutes ensures the pool cycles through fresh connections and picks up new routing.

**PgBouncer interaction.** If you're running PgBouncer in transaction-pooling mode (you should be for high throughput), `ConnMaxLifetime` becomes less important for load distribution — PgBouncer handles that. But you should still set `ConnMaxIdleTime` to avoid holding PgBouncer slots open during idle periods.

**The `MaxOpenConns = 0` footgun.** Zero means unlimited. This is the default. In production, this is almost always wrong. An accidental surge — say, a slow query holds connections while new requests keep arriving — can exhaust all of Postgres's available connections in seconds. Always set an explicit limit.

**Setting `MaxIdleConns > MaxOpenConns`.** This is silently ignored. `database/sql` caps idle connections at the max open value. Don't set idle higher than open — it just creates confusion when reading the config.

**Lifetime vs idle time.** `ConnMaxLifetime` is the maximum age of a connection regardless of use. `ConnMaxIdleTime` is how long a connection can sit idle before being closed. You want both. Lifetime handles stale routing and authentication token rotation. Idle time handles pool cleanup during low-traffic periods.

**One pool per database, not one per request.** I keep saying this but I'll keep saying it: `*sql.DB` is a pool. Create one per database. Share it. Using `sync.Once` to initialize it is common:

```go
// RIGHT — singleton pool initialized once
var (
    globalDB   *sql.DB
    globalDBMu sync.Once
)

func GetDB() *sql.DB {
    globalDBMu.Do(func() {
        var err error
        globalDB, err = NewDB(os.Getenv("DATABASE_URL"), DefaultDBConfig())
        if err != nil {
            log.Fatalf("failed to initialize database: %v", err)
        }
    })
    return globalDB
}
```

Or better — initialize it in `main` and inject it into your handlers/repositories. Globals work, but explicit injection is easier to test.

## Key Takeaway

The default `database/sql` pool settings will hurt you under load. Always set `MaxOpenConns` to something deliberate — typically `(postgres_max_connections / app_instance_count) * 0.8`. Set `MaxIdleConns` equal to `MaxOpenConns` to avoid connection churn. Set `ConnMaxLifetime` (5 minutes is a good default) and `ConnMaxIdleTime` (1 minute). Expose pool stats to your monitoring system — `WaitCount` growing is your early warning that the pool is too tight. These four settings are the difference between a service that handles traffic spikes gracefully and one that pages you at 3am.

---

← [Lesson 1: database/sql Done Right](/post/go/go-db-sql-done-right) | [Lesson 3: Transactions That Don't Bite →](/post/go/go-db-transactions)
