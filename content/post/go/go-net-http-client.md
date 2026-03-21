---
title: 'Lesson 1: HTTP Client Internals — Transport, connection reuse, and the pool you forgot to configure'
author: Atharva Pandey
series: "Go Networking & Distributed Systems"
lesson: 1
keywords:
  - Go
  - golang
  - HTTP client
  - transport
  - connection pool
  - net/http
  - keep-alive
  - connection reuse
tags:
  - Go tutorial
  - golang
  - networking
date: '2024-06-14T00:00:00.000Z'
---

The first production Go service I wrote hammered our database proxy with thousands of new TCP connections every minute. The proxy started refusing connections. The on-call engineer thought it was the database. It wasn't. It was me — specifically, my decision to create a new `http.Client` on every request and then never read the response body to completion. I spent three hours debugging what turned out to be two lines of misunderstood standard library behavior.

That experience taught me more about Go's HTTP client than any tutorial ever could. The `net/http` package is deceptively simple on the surface and genuinely intricate underneath. Understanding what lives below `http.Get()` is the difference between a service that handles 10k RPS gracefully and one that exhausts file descriptors under load.

## The Problem

The naive pattern looks like this:

```go
// WRONG — creates a new client (and implicitly a new Transport) each time
func fetchUser(id string) (*User, error) {
    client := &http.Client{Timeout: 5 * time.Second}
    resp, err := client.Get("https://api.example.com/users/" + id)
    if err != nil {
        return nil, err
    }
    // ALSO WRONG — forgetting to read and close the body
    defer resp.Body.Close()
    var u User
    return &u, json.NewDecoder(resp.Body).Decode(&u)
}
```

Two bugs here. First: creating a new `http.Client` with a new `http.Transport` on every call means no TCP connections are ever reused. Every request opens a fresh connection. TLS handshakes are repeated. DNS is re-resolved. The kernel's file descriptor table fills with connections in `TIME_WAIT`. Under any real load, you run out of ephemeral ports.

Second: if you decode only part of the body — or stop reading early due to an error — the connection cannot be returned to the pool. Go's transport layer can only reuse a connection once the response body has been fully consumed. Leave bytes on the wire and you permanently retire that connection.

The second bug is subtler and I've seen it in production code at companies that absolutely should have known better:

```go
// WRONG — partial read prevents connection reuse
resp, err := client.Get(url)
if err != nil {
    return err
}
defer resp.Body.Close()

if resp.StatusCode != 200 {
    return fmt.Errorf("unexpected status: %d", resp.StatusCode)
    // body was never drained — connection is dead
}
```

## The Idiomatic Way

The fix starts at package level: one client, shared everywhere, configured once.

```go
package httpclient

import (
    "net"
    "net/http"
    "time"
)

// Package-level client — initialize once, reuse forever.
var Client = &http.Client{
    Timeout: 30 * time.Second,
    Transport: &http.Transport{
        // How long to wait for a connection from the pool.
        // Default is unlimited — dangerous under load.
        ResponseHeaderTimeout: 10 * time.Second,

        // How many idle connections to keep open per host.
        // Default is 2. Tune this to your actual concurrency.
        MaxIdleConnsPerHost: 100,
        MaxIdleConns:        200,

        // Keep connections alive.
        DisableKeepAlives: false,

        // Dial timeout — don't wait forever for TCP connect.
        DialContext: (&net.Dialer{
            Timeout:   5 * time.Second,
            KeepAlive: 30 * time.Second,
        }).DialContext,

        // TLS handshake timeout.
        TLSHandshakeTimeout: 5 * time.Second,

        // How long an idle connection can sit in the pool.
        IdleConnTimeout: 90 * time.Second,
    },
}
```

And the correct way to drain a body, even on error paths:

```go
func fetchUser(id string) (*User, error) {
    resp, err := Client.Get("https://api.example.com/users/" + id)
    if err != nil {
        return nil, fmt.Errorf("get user %s: %w", id, err)
    }
    defer func() {
        // Always drain and close — even if we got an error status.
        // io.Copy to io.Discard reads the remaining bytes so the
        // transport can reuse the connection.
        io.Copy(io.Discard, resp.Body)
        resp.Body.Close()
    }()

    if resp.StatusCode != http.StatusOK {
        return nil, fmt.Errorf("unexpected status %d for user %s", resp.StatusCode, id)
    }

    var u User
    if err := json.NewDecoder(resp.Body).Decode(&u); err != nil {
        return nil, fmt.Errorf("decode user: %w", err)
    }
    return &u, nil
}
```

The `io.Copy(io.Discard, resp.Body)` before `Close()` is the idiom. It reads whatever bytes are left in the response body — even if you've already read what you needed — and throws them away. The transport sees a fully consumed body and can put the underlying TCP connection back in the pool.

## In The Wild

When I look at high-throughput Go services, the transport configuration is always tuned. The Go standard library ships conservative defaults that are correct for low-traffic scenarios but need adjustment for services handling more than a few dozen concurrent requests.

`MaxIdleConnsPerHost` deserves special attention. The default is 2. If your service fans out to a single upstream — say, a payment API — with 100 concurrent goroutines, 98 of those goroutines will open new connections on every call because the pool only holds 2. The fix is setting `MaxIdleConnsPerHost` to at least your expected concurrency against that host.

One pattern I've adopted is instrumenting the transport to expose pool metrics:

```go
// Wrap the transport to track active connections.
type InstrumentedTransport struct {
    Base     http.RoundTripper
    active   atomic.Int64
    reused   atomic.Int64
}

func (t *InstrumentedTransport) RoundTrip(req *http.Request) (*http.Response, error) {
    t.active.Add(1)
    defer t.active.Add(-1)

    resp, err := t.Base.RoundTrip(req)
    if err == nil && resp != nil {
        // The "wasIdle" field tells you if the connection was reused.
        // Access it via the unexported trace hook below.
    }
    return resp, err
}
```

For production visibility, use `httptrace.ClientTrace`. You can hook `GotConn` and check `info.Reused` to measure your actual connection reuse rate. If that number is below 90% on steady traffic, your pool is misconfigured.

```go
trace := &httptrace.ClientTrace{
    GotConn: func(info httptrace.GotConnInfo) {
        if info.Reused {
            reusedConnections.Inc()
        } else {
            newConnections.Inc()
        }
    },
}
req = req.WithContext(httptrace.WithClientTrace(req.Context(), trace))
```

This is how you catch the pool misconfiguration before your on-call engineer finds it at 2am.

## The Gotchas

**Timeout semantics are not what you think.** The `Timeout` field on `http.Client` covers the entire request — including reading the body. If you're streaming a large file and the total transfer takes longer than the timeout, the context gets cancelled mid-stream. For large downloads, set `Timeout` to zero and use `context.WithTimeout` on the request itself, sized to the expected transfer time.

**`http.DefaultClient` has no timeout.** Go's package-level `http.Get`, `http.Post`, and friends all use `http.DefaultClient`, which has `Timeout: 0` — meaning no timeout at all. A slow upstream will hold the goroutine forever. Never use `http.DefaultClient` in production code.

**Proxy environment variables.** `http.Transport` reads `HTTP_PROXY`, `HTTPS_PROXY`, and `NO_PROXY` by default through `http.ProxyFromEnvironment`. In containerized environments where these variables are sometimes set unexpectedly, this can route your traffic through an unintended proxy. If you don't want proxy support, set `Proxy: nil` explicitly.

**Redirects.** By default, `http.Client` follows up to 10 redirects. Each redirect is a new request, but the original timeout still applies to the entire chain. I've seen services get caught in redirect loops that consumed the entire timeout before returning an error.

## Key Takeaway

Go's HTTP client is a precision instrument that ships with training-wheels defaults. The three things that will save you in production: share one client across the application, tune `MaxIdleConnsPerHost` to match your concurrency, and always drain the response body before closing it. Everything else — timeouts, tracing, proxy configuration — builds on top of those three fundamentals.

---

**Previous:** Series introduction
**Next:** [Lesson 2: Retries with Exponential Backoff — Retry right or retry forever](/post/go/go-net-retries/)
