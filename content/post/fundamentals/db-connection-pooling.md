---
title: 'Lesson 7: Connection Pooling — What PgBouncer Actually Does'
author: Atharva Pandey
series: "Database Internals for Backend Engineers"
lesson: 7
keywords:
  - database internals
  - connection pooling
  - PgBouncer
  - Postgres connections
  - backend engineering
tags:
  - fundamentals
  - databases
date: '2024-07-27T00:00:00.000Z'
---

A Postgres connection is not cheap. When I first scaled a service from a single server to 20 replicas — each running a Go application with `database/sql`'s default pool settings — the database became the bottleneck almost immediately. Not because the queries were slow. Because we had 20 × 100 = 2,000 open connections, each consuming RAM on the Postgres server, and Postgres was spending more time managing connections than executing queries. This is the problem PgBouncer solves, and understanding how it works makes you a much better architect of backend systems.

## How It Actually Works

A Postgres server process handles exactly one connection. Each `postgres` backend process consumes roughly 5–10 MB of memory, handles the full TLS handshake, authentication, and startup protocol, and maintains session state. Connecting is expensive — typically 10–50ms for a fresh TCP + auth + startup sequence.

Applications deal with this in two layers:

**Application-level pooling** (`database/sql` in Go): your application maintains a pool of already-open connections. New queries grab a connection from the pool and return it when done. No repeated TLS/auth overhead. This is what `db.SetMaxOpenConns`, `db.SetMaxIdleConns`, and `db.SetConnMaxLifetime` control.

**External pooler** (PgBouncer, pgpool): a separate process that sits between your applications and Postgres. Applications connect to the pooler; the pooler multiplexes those onto a smaller number of real Postgres connections.

PgBouncer has three pooling modes:

**Session mode**: a server connection is assigned to a client for the entire client session. Functionally equivalent to direct connection — the benefit is faster initial connection (PgBouncer handles it without the full Postgres startup). Used when you need session-level features (prepared statements, advisory locks, `SET` variables).

**Transaction mode**: a server connection is assigned to a client only for the duration of a transaction. Between transactions, the connection is returned to the pool and can be used by other clients. This is where PgBouncer shines — 1,000 application connections can share 50 server connections if they're mostly between transactions.

**Statement mode**: a server connection is assigned per individual statement. Breaks any multi-statement flow. Rarely used.

Here is a simplified model of transaction-mode pooling in Go:

```go
type ConnectionPool struct {
    mu          sync.Mutex
    serverConns chan *ServerConn // actual Postgres connections
    maxSize     int
}

type ClientRequest struct {
    query  string
    result chan QueryResult
}

func (p *ConnectionPool) Execute(query string) (QueryResult, error) {
    // grab a real server connection
    serverConn := <-p.serverConns

    result, err := serverConn.Execute(query)

    // return connection to pool immediately after use
    p.serverConns <- serverConn

    return result, err
}

// 1000 clients can share 50 server connections
// as long as each client's between-transaction time >> query execution time
```

The multiplexing ratio is the key insight. If a typical request cycle is: 5ms query, 195ms application processing, then a connection is actively used only 2.5% of the time. 50 server connections can serve 2,000 active clients without queuing.

## Why It Matters

Postgres's documented soft limit is around 100–200 connections before performance degrades. Each connection runs a separate OS process. The scheduler overhead, the RAM consumption (5–10 MB × 2,000 = 10–20 GB just for connection processes), and the shared memory contention all become significant.

With PgBouncer in transaction mode:
- 20 application servers × 100 pool connections = 2,000 application-side connections
- PgBouncer maintains 50 server connections to Postgres
- Postgres is healthy, RAM consumption is predictable, scheduler overhead is minimal

This is not just theory. The pattern of "horizontally scaling application servers + connection pooler + appropriately sized Postgres" is the standard architecture for virtually every high-scale Postgres deployment.

## Production Example

In Go, configuring `database/sql` correctly is the first line of defense:

```go
db, err := sql.Open("pgx", dsn)
if err != nil {
    return err
}

// Max open connections — must match what your Postgres/PgBouncer can handle
db.SetMaxOpenConns(25)

// Max idle connections — keep these warm to avoid reconnect overhead
db.SetMaxIdleConns(10)

// Recycle connections periodically to handle server-side resets, failover
db.SetConnMaxLifetime(5 * time.Minute)

// Close idle connections that haven't been used — prevents connection leaks
db.SetConnMaxIdleTime(1 * time.Minute)
```

For PgBouncer's `pgbouncer.ini`:

```ini
[databases]
myapp = host=postgres-primary port=5432 dbname=myapp

[pgbouncer]
listen_port = 6432
listen_addr = *
auth_type = scram-sha-256
pool_mode = transaction
max_client_conn = 2000    ; application-side connections
default_pool_size = 50    ; server-side Postgres connections
min_pool_size = 10        ; keep this many warm
reserve_pool_size = 10    ; extra connections for traffic spikes
server_idle_timeout = 600
log_connections = 0       ; disable in production — high volume
```

The Go application connects to PgBouncer (`host=pgbouncer-host port=6432`), not directly to Postgres. From the application's perspective, it's just a Postgres endpoint.

## The Tradeoffs

Transaction-mode pooling has restrictions. Because the server connection is released between transactions, session-level state does not persist:

- **Prepared statements**: PgBouncer in transaction mode does not support protocol-level prepared statements. Use named queries or disable them in your driver (`prefer_simple_protocol=true` in pgx).
- **`SET` variables**: `SET search_path = myschema` at the session level won't survive transaction boundaries. Use search_path in the DSN instead.
- **Advisory locks**: `pg_advisory_lock()` is session-scoped. Use `pg_advisory_xact_lock()` (transaction-scoped) instead.
- **`LISTEN/NOTIFY`**: requires a persistent session connection. Use a dedicated connection that bypasses PgBouncer for this.

Also: PgBouncer is a single process. For very high connection counts, consider running multiple instances behind a load balancer, or use Pgpool-II which supports multiple processes.

## Key Takeaway

A Postgres connection is an OS process consuming 5–10 MB RAM and significant scheduler overhead. Application-level pooling handles reconnect cost; an external pooler like PgBouncer handles the Postgres process count. Transaction-mode PgBouncer is the right default for OLTP workloads — it lets thousands of application connections share dozens of server connections. Learn its restrictions (no session prepared statements, no session SET) and design around them.

---

**Previous:** [Lesson 6: Query Planning and EXPLAIN](/post/fundamentals/db-explain/) | **Next:** [Lesson 8: Replication — Streaming, Logical, and Failover](/post/fundamentals/db-replication/)
