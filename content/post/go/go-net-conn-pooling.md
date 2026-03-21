---
title: 'Lesson 4: Connection Pooling — One connection per request is a performance bug'
author: Atharva Pandey
series: "Go Networking & Distributed Systems"
lesson: 4
keywords:
  - Go
  - golang
  - connection pool
  - database
  - sql.DB
  - pgx
  - TCP
  - performance
tags:
  - Go tutorial
  - golang
  - networking
date: '2024-10-12T00:00:00.000Z'
---

The first time I benchmarked a Go service against a database, the numbers were embarrassing. Five hundred requests per second, each one taking 30 milliseconds to execute a trivially simple query. The query itself took 2 milliseconds on the database server. The other 28 milliseconds were TCP handshake plus TLS plus PostgreSQL authentication — repeated for every single request because I had no connection pool.

Connection pooling is one of those topics that feels like an advanced optimization until you discover that nearly every database driver in Go already pools connections by default — you just have to configure the pool instead of leaving it at its default settings, which are almost always wrong for your workload.

## The Problem

The database/sql package's `sql.DB` type is already a connection pool. But the default configuration is surprisingly conservative:

```go
// WRONG — default sql.DB configuration is dangerous in production
db, err := sql.Open("postgres", connStr)
if err != nil {
    log.Fatal(err)
}
// Default MaxOpenConns: unlimited — can exhaust database connections
// Default MaxIdleConns: 2 — wasteful under concurrent load
// Default ConnMaxLifetime: 0 (forever) — connections never refreshed
// Default ConnMaxIdleTime: 0 (forever) — idle connections held indefinitely
```

The unlimited `MaxOpenConns` is the dangerous one. PostgreSQL has a hard limit on connections — typically 100 in default configurations. If your service opens a new connection for every concurrent request during a traffic spike, you'll hit that limit, PostgreSQL will refuse new connections, and every query will fail simultaneously. Meanwhile, your existing queries might be holding locks that prevent recovery.

The flip side: if `MaxIdleConns` is too low relative to your concurrency, the pool can't reuse connections fast enough and you get the same overhead as no pooling at all.

## The Idiomatic Way

Configure the pool intentionally. Here's what I use as a starting point and tune from there:

```go
package db

import (
    "database/sql"
    "fmt"
    "time"

    _ "github.com/lib/pq"
)

// Open returns a configured *sql.DB pool. Tune the pool settings to your
// specific workload and database server's connection limit.
func Open(connStr string) (*sql.DB, error) {
    db, err := sql.Open("postgres", connStr)
    if err != nil {
        return nil, fmt.Errorf("open: %w", err)
    }

    // Cap total open connections. Set this to ~80% of the database server's
    // max_connections limit, shared across all instances of your service.
    // Example: Postgres default max_connections=100, 3 service instances
    // → 100 * 0.8 / 3 ≈ 26 per instance.
    db.SetMaxOpenConns(25)

    // Keep at most 25 connections idle. Matching MaxOpenConns means we
    // never spin up a new connection when one is available in the pool.
    db.SetMaxIdleConns(25)

    // Refresh connections after 30 minutes. This catches stale connections
    // that were dropped by a firewall or load balancer without a TCP RST.
    db.SetConnMaxLifetime(30 * time.Minute)

    // Return connections that have been idle for more than 5 minutes
    // to the OS. This keeps the pool lean during quiet periods.
    db.SetConnMaxIdleTime(5 * time.Minute)

    // Verify the connection works before returning.
    if err := db.Ping(); err != nil {
        return nil, fmt.Errorf("ping: %w", err)
    }

    return db, nil
}
```

Pool sizing has a formula: `MaxOpenConns = (database max_connections × 0.8) / number_of_service_instances`. The 0.8 headroom leaves room for ad hoc connections from migrations, dashboards, and debugging sessions. Distribute the remainder across all instances.

For non-database TCP pools — say, a pool of connections to a custom protocol server — the standard library's `sync.Pool` isn't the right tool (it's for object reuse, not connection management). You need something more explicit:

```go
// A minimal generic connection pool using a buffered channel.
type ConnPool struct {
    conns chan net.Conn
    dial  func() (net.Conn, error)
}

func NewConnPool(size int, dial func() (net.Conn, error)) (*ConnPool, error) {
    p := &ConnPool{
        conns: make(chan net.Conn, size),
        dial:  dial,
    }
    // Pre-warm the pool.
    for i := 0; i < size; i++ {
        conn, err := dial()
        if err != nil {
            p.Close()
            return nil, fmt.Errorf("pre-warm: %w", err)
        }
        p.conns <- conn
    }
    return p, nil
}

func (p *ConnPool) Get(ctx context.Context) (net.Conn, error) {
    select {
    case conn := <-p.conns:
        return conn, nil
    case <-ctx.Done():
        return nil, ctx.Err()
    }
}

func (p *ConnPool) Put(conn net.Conn) {
    select {
    case p.conns <- conn:
    default:
        conn.Close() // pool is full — discard the connection
    }
}

func (p *ConnPool) Close() {
    close(p.conns)
    for conn := range p.conns {
        conn.Close()
    }
}
```

This is the channel-based pool pattern. The `Get` respects context cancellation — if the pool is empty and the context deadline passes, the call returns immediately with an error instead of blocking indefinitely.

## In The Wild

The most important signal from your pool is `db.Stats()`. I expose this as a metrics endpoint in every service:

```go
func recordPoolStats(db *sql.DB, reg prometheus.Registerer) {
    inUse := prometheus.NewGaugeFunc(prometheus.GaugeOpts{
        Name: "db_connections_in_use",
    }, func() float64 {
        return float64(db.Stats().InUse)
    })
    idle := prometheus.NewGaugeFunc(prometheus.GaugeOpts{
        Name: "db_connections_idle",
    }, func() float64 {
        return float64(db.Stats().Idle)
    })
    waitCount := prometheus.NewCounterFunc(prometheus.CounterOpts{
        Name: "db_connections_wait_total",
    }, func() float64 {
        return float64(db.Stats().WaitCount)
    })
    reg.MustRegister(inUse, idle, waitCount)
}
```

`WaitCount` is the number of times a goroutine had to wait for a connection from the pool. This should be zero or near-zero. If it's climbing, your pool is too small for your concurrency — or your queries are holding connections too long.

## The Gotchas

**Transactions hold connections.** A `sql.Tx` holds exactly one connection from the pool for its entire lifetime. Long-running transactions — even read-only ones — can drain your pool fast. Keep transactions short. Don't hold a transaction open across HTTP request boundaries, and definitely don't hold one while waiting for user input.

**`sql.Open` does not connect.** It just validates the DSN and initializes the pool struct. The first actual connection happens on the first query. Use `db.PingContext` at startup to fail fast if the database is unreachable rather than discovering it on the first real request.

**Connection string secrets.** The connection string typically contains a password. Don't log it. Don't include it in error messages. Inject it from an environment variable or secrets manager, not from a config file checked into version control.

**Health checks need a dedicated query.** Some ORMs and SQL drivers use a sentinel query like `SELECT 1` for health checks. Make sure this health check query goes through the pool so it can detect pool exhaustion, not around it.

## Key Takeaway

The connection pool is already there in `database/sql` — you just have to configure it. Set `MaxOpenConns` based on your database's connection limit divided by your instance count, set `MaxIdleConns` to match it, add a connection lifetime and idle time, and expose pool stats as metrics. That's all of it. Five lines of configuration prevents a class of production failures that otherwise looks mysterious.

---

**Previous:** [Lesson 3: Circuit Breaking](/post/go/go-net-circuit-breaker/)
**Next:** [Lesson 5: Message Queues in Go — NATS, RabbitMQ, Kafka — pick your tradeoff](/post/go/go-net-message-queues/)
