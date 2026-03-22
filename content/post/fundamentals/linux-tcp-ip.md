---
title: 'Lesson 4: TCP/IP Stack — SYN Floods, TIME_WAIT, Connection Tuning'
author: Atharva Pandey
series: "Linux for Backend Engineers"
lesson: 4
keywords:
  - linux
  - TCP
  - IP
  - SYN flood
  - TIME_WAIT
  - connection tuning
  - network
  - backend engineering
tags:
  - fundamentals
  - linux
  - operating systems
date: '2024-06-11T00:00:00.000Z'
---

A load test I ran against a Go HTTP server one afternoon produced a strange result: throughput leveled off at about 30,000 requests per second and couldn't go higher, even though CPU was at 30%. Running `netstat -an | grep TIME_WAIT | wc -l` showed over 28,000 connections in `TIME_WAIT` state. The kernel was running out of local port numbers. Understanding TCP connection states — and how to tune Linux's TCP stack — unblocked the test and taught me more about networking than any course had. This lesson covers the TCP mechanics that actually matter for backend engineers running high-throughput services.

## How It Actually Works

TCP is a connection-oriented protocol. Every connection goes through a state machine, and the two states that most affect production systems are the three-way handshake and the TIME_WAIT state.

**Three-Way Handshake**:
```
Client                Server
  SYN →
         ← SYN-ACK
  ACK →
  (connection established)
```

The kernel maintains a **SYN backlog** (half-open connections — SYN received, SYN-ACK sent, ACK not yet received) and a **accept backlog** (fully established connections waiting for the application to call `accept()`). Both have limits.

**Connection States** (the ones that matter):

| State | Meaning |
|-------|---------|
| `LISTEN` | Server waiting for incoming connections |
| `SYN_SENT` | Client sent SYN, waiting for SYN-ACK |
| `SYN_RECV` | Server received SYN, sent SYN-ACK |
| `ESTABLISHED` | Active connection |
| `FIN_WAIT_1/2` | Active close initiated |
| `TIME_WAIT` | Both FINs exchanged, waiting 2×MSL before cleanup |
| `CLOSE_WAIT` | Peer closed, local app hasn't called close() yet |

**TIME_WAIT** is the most misunderstood state. After a connection closes (four-way FIN handshake), the side that initiated the close enters `TIME_WAIT` for 2 × MSL (Maximum Segment Lifetime, typically 60 seconds). This means the socket cannot be reused for the same (local IP, local port, remote IP, remote port) 4-tuple.

The reason TIME_WAIT exists: delayed packets from the old connection must not be mistaken for packets in a new connection that reuses the same 4-tuple.

## Why It Matters

**SYN backlog tuning**: under heavy connection load, the SYN backlog fills up and new SYNs are dropped. The fix:

```bash
# Increase the SYN backlog
sysctl -w net.ipv4.tcp_max_syn_backlog=8192

# Enable SYN cookies — stateless defense against SYN floods
# When SYN backlog is full, encode state in the sequence number instead
sysctl -w net.ipv4.tcp_syncookies=1
```

**Accept backlog**: the `listen()` syscall takes a backlog parameter. In Go's `net.Listen`, this defaults to the OS maximum (`net.core.somaxconn`). Raise both:

```bash
sysctl -w net.core.somaxconn=65535
```

**TIME_WAIT exhaustion**: when your service makes many short-lived outbound connections (microservice calling another service, service calling a database), local ports in TIME_WAIT prevent new connections. You have ~28,000 ephemeral ports (range 32768–60999). With 60-second TIME_WAIT, that's ~467 new short-lived connections per second maximum — easily exceeded.

Fixes:

```bash
# Reduce TIME_WAIT duration (non-standard but widely used)
sysctl -w net.ipv4.tcp_fin_timeout=30

# Allow port reuse for TIME_WAIT sockets when safe
sysctl -w net.ipv4.tcp_tw_reuse=1  # only for outbound connections

# Increase the ephemeral port range
sysctl -w net.ipv4.ip_local_port_range="1024 65535"
```

## Production Example

In Go, TIME_WAIT is most problematic when using HTTP/1.1 without keep-alive, or when connection reuse is broken. Always use connection pooling:

```go
// Properly configured http.Transport avoids TIME_WAIT flood
transport := &http.Transport{
    // Keep connections alive — reuse instead of close+reopen
    DisableKeepAlives: false,

    // Pool size: how many idle connections to keep per host
    MaxIdleConnsPerHost: 100,
    MaxIdleConns:        1000,

    // How long to keep an idle connection — match server keepalive
    IdleConnTimeout: 90 * time.Second,

    // Connection timeout
    DialContext: (&net.Dialer{
        Timeout:   5 * time.Second,
        KeepAlive: 30 * time.Second,
    }).DialContext,

    // TLS handshake timeout
    TLSHandshakeTimeout: 5 * time.Second,
}

client := &http.Client{
    Transport: transport,
    Timeout:   30 * time.Second,
}
```

With keep-alive, a connection is reused for multiple requests instead of closed after each one — drastically reducing TIME_WAIT accumulation.

For database connections, always use a connection pool (`database/sql` handles this). Never open a new connection per query.

Observe TCP state distribution:

```go
// Parse /proc/net/tcp to count connection states
func tcpStateCount() (map[string]int, error) {
    // States in /proc/net/tcp are hex: 01=ESTABLISHED, 06=TIME_WAIT, etc.
    stateMap := map[string]string{
        "01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV",
        "04": "FIN_WAIT1",   "05": "FIN_WAIT2", "06": "TIME_WAIT",
        "07": "CLOSE",       "08": "CLOSE_WAIT", "09": "LAST_ACK",
        "0A": "LISTEN",      "0B": "CLOSING",
    }
    counts := make(map[string]int)

    data, err := os.ReadFile("/proc/net/tcp")
    if err != nil {
        return nil, err
    }
    for _, line := range strings.Split(string(data), "\n")[1:] {
        fields := strings.Fields(line)
        if len(fields) < 4 {
            continue
        }
        if name, ok := stateMap[strings.ToUpper(fields[3])]; ok {
            counts[name]++
        }
    }
    return counts, nil
}
```

## The Tradeoffs

**`tcp_tw_reuse` vs `tcp_tw_recycle`**: `tcp_tw_reuse` (enabled above) is safe — it allows reusing a TIME_WAIT socket for a new outbound connection if the new connection's timestamp is strictly newer. `tcp_tw_recycle` was removed in Linux 4.12 because it broke connections through NAT (multiple clients behind a single IP could have non-monotonic timestamps).

**`SO_REUSEADDR`**: allows binding to a port in TIME_WAIT state. Essential for server restarts — without it, restarting a service that was listening on port 8080 may fail for up to 4 minutes. Go's `net.Listen` sets `SO_REUSEADDR` automatically.

**`SO_LINGER` with timeout 0**: causes an RST instead of a FIN when closing. Immediately removes the connection without entering TIME_WAIT. Useful for servers that want to avoid TIME_WAIT buildup, but RST is less graceful — the peer may not receive all data.

**Keep-alive settings**: TCP keep-alive probes detect dead connections (server crashed, network partition). Configure at the kernel level:

```bash
sysctl -w net.ipv4.tcp_keepalive_time=60      # idle before probes
sysctl -w net.ipv4.tcp_keepalive_intvl=10     # interval between probes
sysctl -w net.ipv4.tcp_keepalive_probes=5     # probes before giving up
```

## Key Takeaway

TCP connection state matters for production backend services. TIME_WAIT accumulates when you make many short-lived connections — fix it with connection pooling and keep-alive. SYN floods exhaust the accept backlog — fix with SYN cookies and a larger backlog. Tune `net.ipv4.ip_local_port_range`, `tcp_tw_reuse`, and accept backlog sizes for high-connection-rate services. In Go, configure `http.Transport` properly — the defaults are conservative for high-throughput use.

---

**Previous:** [Lesson 3: File Descriptors](/post/fundamentals/linux-file-descriptors/) | **Next:** [Lesson 5: Epoll and IO Multiplexing — How Go's Netpoller Works](/post/fundamentals/linux-epoll/)
