---
title: "Lesson 1: TCP Deep Dive — Three-way handshake, congestion, Nagle"
author: Atharva Pandey
keywords:
  - TCP
  - networking
  - three-way handshake
  - congestion control
  - Nagle's algorithm
  - backend engineering
tags:
  - fundamentals
  - networking
series: "Networking for Backend Engineers"
lesson: 1
date: '2024-04-29T00:00:00.000Z'
---

I used to treat TCP as a black box. Data goes in one side, data comes out the other — reliably, in order, no duplicates. That was all I needed to know, right? Then I started debugging latency spikes in a payment service and spent three days chasing a 200ms tail latency that turned out to be Nagle's algorithm fighting with delayed ACKs. After that, I stopped treating TCP as a black box.

If you're building backend services, TCP is the foundation everything sits on. Understanding it at the level of "how does data actually move" pays dividends in debugging, capacity planning, and writing systems that behave well under pressure.

## How It Works

TCP is a connection-oriented, reliable, ordered byte stream protocol. Every TCP connection begins with a three-way handshake.

**The Three-Way Handshake**

Before any application data is exchanged, the client and server go through this dance:

1. **SYN** — Client sends a segment with SYN flag, picks a random Initial Sequence Number (ISN), say 1000.
2. **SYN-ACK** — Server responds with its own ISN (say 5000) and ACKs the client's ISN by returning `ACK=1001`.
3. **ACK** — Client acknowledges the server's ISN: `ACK=5001`. Connection established.

```
Client                          Server
  |                               |
  |------- SYN (seq=1000) ------->|
  |                               |
  |<-- SYN-ACK (seq=5000,        |
  |            ack=1001) ---------|
  |                               |
  |------- ACK (ack=5001) ------->|
  |                               |
  | <<< connection established >>> |
```

This handshake costs one round trip before any data flows. For high-frequency short-lived connections this is significant — which is exactly why HTTP keep-alive, connection pools, and HTTP/2 exist.

**Sequence Numbers and Reliability**

TCP reliability comes from sequence numbers and acknowledgments. Every byte in the stream has a sequence number. The receiver sends ACKs confirming receipt. If the sender doesn't receive an ACK within a timeout, it retransmits. The receiver's buffer handles reordering if segments arrive out of order.

**Congestion Control**

TCP assumes that packet loss means network congestion, and it backs off. The main algorithm is CUBIC (the default in Linux) though you'll also see BBR in modern systems.

The core concept is a congestion window (cwnd) that limits how much unacknowledged data can be in flight:

- **Slow Start**: cwnd starts small (10 segments in modern kernels) and doubles every RTT until it hits the slow start threshold (ssthresh) or sees loss.
- **Congestion Avoidance**: After ssthresh, cwnd grows by 1 segment per RTT (additive increase).
- **On loss**: ssthresh is halved, cwnd drops (multiplicative decrease).

```
cwnd
 ^
 |          /\
 |         /  \
 |        /    \____/\
 |       /            \____
 |      /
 |_____/
 +-----------------------> time
  slow   congestion
  start  avoidance
```

**Nagle's Algorithm**

Here's the one that bit me. Nagle's algorithm buffers small writes to send them as a single larger segment. The rule: if there's unacknowledged data in flight, buffer new small writes until the buffer is full or the ACK arrives.

This is great for throughput. It's terrible for latency when combined with delayed ACKs — the receiver's optimization where it waits up to 200ms before ACKing to see if there's a response to piggyback on.

The result: a write of 1 byte → Nagle waits for ACK → receiver waits 200ms to ACK → 200ms stall. This is the 40ms or 200ms mystery latency that haunts debugging sessions.

## Why It Matters

Every time your service makes an outbound connection — to a database, another microservice, a third-party API — TCP's mechanics affect your latency and throughput. A service making 1,000 short-lived connections per second is paying 1,000 handshake RTTs per second. A service hitting a chatty protocol with Nagle enabled is paying invisible buffering tax.

The connection setup cost is why connection pooling is not optional for production databases. A fresh TCP + TLS handshake to PostgreSQL costs 3–5 round trips before you send your first SQL byte. A pool keeps those connections alive.

## Production Example

Here's how I fixed that payment service latency issue. The service was making individual RPC calls to an internal auth service. Each call was a small payload. Fix was two lines:

```go
// Disable Nagle's algorithm for latency-sensitive connections
conn, err := net.DialTCP("tcp", nil, addr)
if err != nil {
    return nil, fmt.Errorf("dial: %w", err)
}
if err := conn.SetNoDelay(true); err != nil {
    return nil, fmt.Errorf("set no delay: %w", err)
}
```

`TCP_NODELAY = true` disables Nagle. Segments are sent immediately. Combined with the HTTP client configuration:

```go
transport := &http.Transport{
    DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
        dialer := &net.Dialer{
            Timeout:   5 * time.Second,
            KeepAlive: 30 * time.Second,
        }
        conn, err := dialer.DialContext(ctx, network, addr)
        if err != nil {
            return nil, err
        }
        tc := conn.(*net.TCPConn)
        tc.SetNoDelay(true)
        return tc, nil
    },
    MaxIdleConns:        100,
    MaxIdleConnsPerHost: 20,
    IdleConnTimeout:     90 * time.Second,
}
```

The 200ms tail latency disappeared. P99 dropped from 210ms to 12ms.

For a database connection pool, I also tune the socket receive buffer. If your application reads slowly, a small receive buffer causes the sender to stall (TCP flow control). Increase it for high-throughput connections:

```go
conn.SetReadBuffer(256 * 1024)   // 256KB receive buffer
conn.SetWriteBuffer(256 * 1024)  // 256KB send buffer
```

## The Tradeoffs

**TCP_NODELAY vs throughput**: Disabling Nagle increases packet count. For bulk transfer workloads (file uploads, streaming large responses), Nagle's batching helps throughput. Only disable it when you're optimizing for latency on small messages.

**Connection pooling vs resource usage**: More pooled connections mean more file descriptors and memory. The OS has a limit. You'll hit `too many open files` if you're not careful. Set `MaxIdleConns` based on measured concurrency, not "more is better."

**TCP keep-alive vs connection state**: TCP keep-alive probes detect dead connections, but they fire by default after 2 hours in Linux. Your application-level health check should be faster. Most database drivers implement their own heartbeat.

**Slow Start vs burst traffic**: A freshly established TCP connection starts with a small cwnd. If your service restarts and suddenly gets traffic, the first few requests are throttled while TCP ramps up. This is why graceful deployments with connection warming matter for latency-sensitive services.

**IPv4 vs IPv6 connection setup**: Dual-stack "happy eyeballs" connection racing can add complexity. When debugging connection issues, always check which address family is actually being used.

## Key Takeaway

TCP is reliable but not free. The three-way handshake costs a round trip, Nagle's algorithm buffers small writes in ways that interact badly with delayed ACKs, and congestion control backs off under loss. The practical implications: pool your connections, set `TCP_NODELAY` for latency-sensitive RPC, tune your socket buffers for high-throughput paths, and use `ss -ti` or `netstat -s` when debugging — they expose the TCP state machine directly.

Understanding what's happening at the TCP layer transforms network debugging from guesswork into systematic diagnosis. When your P99 latency has an inexplicable 40ms or 200ms floor, Nagle + delayed ACKs is the first suspect.

---

**Next:** [Lesson 2: HTTP/2 and HTTP/3 — Multiplexing and QUIC](/post/fundamentals/net-http2-http3/)
