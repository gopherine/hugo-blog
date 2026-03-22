---
title: "Lesson 2: HTTP/2 and HTTP/3 — Multiplexing and QUIC"
author: Atharva Pandey
keywords:
  - HTTP/2
  - HTTP/3
  - QUIC
  - multiplexing
  - head-of-line blocking
  - networking
  - backend engineering
tags:
  - fundamentals
  - networking
series: "Networking for Backend Engineers"
lesson: 2
date: '2024-05-14T00:00:00.000Z'
---

The first time I looked at a waterfall chart in Chrome DevTools and saw a hundred requests stacking up like rush-hour traffic, I knew HTTP/1.1 was the problem. We had a dashboard loading twelve API calls and a handful of assets, and they were all waiting in line. Not because the server was slow — it was idle — but because the browser had a six-connection-per-host limit and every request had to wait its turn. That was my introduction to why HTTP/2 exists, and it changed how I think about protocol design.

## How It Works

**HTTP/1.1's core problem**

HTTP/1.1 is a text-based, request-response protocol. One request, one response, per connection — and the response must arrive before the next request can start (without pipelining, which had so many edge-case issues it was effectively disabled everywhere). The workaround was opening multiple parallel connections, but browsers cap this at 6 per origin. For modern pages loading dozens of resources, this is a fundamental bottleneck.

**HTTP/2: Multiplexing over a single connection**

HTTP/2 (standardized in RFC 7540, 2015) rewrites the framing layer while keeping HTTP semantics identical. The key mechanism is **streams**: multiple independent request-response pairs interleaved over a single TCP connection.

```
HTTP/1.1 (3 connections, sequential):
Conn1: [Request A] -----> [Response A] [Request D] -----> [Response D]
Conn2: [Request B] -----> [Response B] [Request E] -----> [Response E]
Conn3: [Request C] -----> [Response C]

HTTP/2 (1 connection, multiplexed):
          Stream 1: [Req A frames...] [Resp A frames...]
          Stream 3: [Req B frames...] [Resp B frames...]
Conn1 --> Stream 5: [Req C frames...] [Resp C frames...]
          Stream 7: [Req D frames...]
          Stream 9: [Req E frames...]
```

Each stream has a unique ID (odd numbers for client-initiated). The connection layer interleaves DATA frames from multiple streams. No stream blocks another at the HTTP layer.

HTTP/2 also introduces:
- **Header compression (HPACK)**: Headers are compressed using a shared dynamic table. Repeated headers (like `Content-Type`, `Authorization`) send a single integer reference instead of the full string. This matters because HTTP/1.1 headers are repeated verbatim on every request.
- **Server push**: The server can proactively send resources the client hasn't asked for yet. In practice, this is mostly unused now — browsers got better at preloading and the complexity wasn't worth it.
- **Stream prioritization**: Clients can declare dependencies between streams. Rarely used correctly in practice.

**The TCP head-of-line blocking problem**

HTTP/2's multiplexing solves application-level head-of-line blocking. But TCP still delivers a byte stream in order. If a single TCP packet is lost, *all* streams on that connection stall while TCP retransmits. On lossy networks (mobile, congested WiFi), HTTP/2 can actually perform worse than HTTP/1.1's multiple connections because a single loss affects everything.

This is the problem HTTP/3 solves.

**HTTP/3 and QUIC**

HTTP/3 replaces TCP with QUIC (RFC 9000, 2021). QUIC is a transport protocol built on top of UDP that reimplements TCP's reliability and ordering guarantees — but at the stream level, not the connection level.

In QUIC, a lost packet only blocks the stream it belongs to. Other streams continue unaffected. This eliminates transport-layer head-of-line blocking entirely.

QUIC also bakes in:
- **0-RTT connection establishment**: On subsequent connections to a known server, QUIC can send application data in the very first packet (after an initial connection caches session state). Compare to TCP + TLS 1.3 which needs 1 RTT.
- **Connection migration**: QUIC connections are identified by a Connection ID, not the 4-tuple (src IP, src port, dst IP, dst port). When a mobile client switches from WiFi to LTE, the connection survives without re-handshaking.
- **Mandatory encryption**: QUIC encrypts not just the payload but also most of the transport metadata. There's no unencrypted QUIC.

```
HTTP/1.1: TCP handshake (1 RTT) + TLS (1-2 RTT) + HTTP request (1 RTT) = 3-4 RTT
HTTP/2:   TCP handshake (1 RTT) + TLS (1 RTT)   + HTTP request (1 RTT) = 3 RTT
HTTP/3:   QUIC 0-RTT (0-1 RTT)  + HTTP request (1 RTT)                 = 1-2 RTT
```

## Why It Matters

For API servers, HTTP/2 gives you multiplexing and header compression "for free" once you enable it in your server and load balancer. You don't change your API design. The wire format changes, but your handlers look the same.

For mobile and global users, HTTP/3's connection migration and 0-RTT are significant. A user on a train switching between cell towers doesn't lose their session. Latency for repeat visitors drops. Google reports ~10% improvement in search results delivery using QUIC internally.

For gRPC, HTTP/2 is the foundation — gRPC's streaming and multiplexing are direct consequences of running on HTTP/2. Understanding HTTP/2 streams is understanding how gRPC handles concurrent RPCs.

## Production Example

Enabling HTTP/2 in a Go service is essentially free with `net/http`:

```go
package main

import (
    "crypto/tls"
    "net/http"
    "golang.org/x/net/http2"
    "golang.org/x/net/http2/h2c"
)

func main() {
    mux := http.NewServeMux()
    mux.HandleFunc("/api/v1/data", handleData)

    // HTTP/2 with TLS (standard — h2 in ALPN)
    server := &http.Server{
        Addr:    ":443",
        Handler: mux,
        TLSConfig: &tls.Config{
            MinVersion: tls.VersionTLS12,
        },
    }
    // http2.ConfigureServer enables HTTP/2 on the server
    http2.ConfigureServer(server, &http2.Server{
        MaxConcurrentStreams: 250,
        MaxReadFrameSize:     1 << 20, // 1MB
    })
    server.ListenAndServeTLS("cert.pem", "key.pem")
}

// HTTP/2 cleartext (h2c) for internal services behind a load balancer
func startH2CServer() {
    h2s := &http2.Server{}
    handler := h2c.NewHandler(http.DefaultServeMux, h2s)
    http.ListenAndServe(":8080", handler)
}
```

For the HTTP client side — if you're calling services that support HTTP/2, your Go client will negotiate it automatically via ALPN:

```go
client := &http.Client{
    Transport: &http.Transport{
        TLSClientConfig: &tls.Config{},
        ForceAttemptHTTP2: true,
        // These settings matter for HTTP/2 performance
        MaxIdleConns:        100,
        IdleConnTimeout:     90 * time.Second,
        DisableCompression:  false,
    },
}
```

To verify HTTP/2 is being used: `curl -v --http2 https://yourservice.example.com/` — look for `< HTTP/2 200` in the response headers.

For HTTP/3, NGINX and Caddy have production support. In Go, `quic-go` is the main implementation. Cloudflare and other CDNs terminate HTTP/3 at the edge — you don't need to implement it yourself for most applications.

## The Tradeoffs

**HTTP/2 multiplexing vs head-of-line blocking**: HTTP/2 makes things better on reliable networks and worse on lossy ones. If your users are on mobile or unreliable connections, measure before assuming HTTP/2 always wins.

**Connection count vs multiplexing**: HTTP/2 uses one connection per origin. This means one TCP congestion window. Under heavy load, multiple HTTP/1.1 connections can sometimes saturate bandwidth better because each has its own congestion window that ramps up. Benchmark your specific workload.

**0-RTT security**: QUIC's 0-RTT data is replay-vulnerable. An attacker can replay 0-RTT packets to resend a request. This is mitigated by only using 0-RTT for idempotent GET requests, not POST/PUT. Your application layer needs to be aware.

**Debugging complexity**: HTTP/2 binary framing is not human-readable. You need Wireshark with HTTP/2 decryption or specialized tools. The mental model of "one request, one response" no longer maps to the wire format.

**Server push abandonment**: Most browsers are removing server push support. Chrome removed it in 2022. Don't build features that depend on it.

## Key Takeaway

HTTP/2 fixes the application-level head-of-line blocking of HTTP/1.1 by multiplexing streams over a single connection. HTTP/3 fixes the remaining transport-level head-of-line blocking by replacing TCP with QUIC. For backend engineers: enable HTTP/2 on your servers (it's low-effort, high-reward), understand that gRPC's streaming is HTTP/2 streams, and monitor your actual connection count metrics before and after — the reduction from many parallel connections to one is measurable in file descriptor usage and TLS handshake overhead.

---

**Previous:** [Lesson 1: TCP Deep Dive](/post/fundamentals/net-tcp/)
**Next:** [Lesson 3: TLS Handshake](/post/fundamentals/net-tls/)
