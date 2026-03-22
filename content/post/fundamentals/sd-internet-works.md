---
title: "Lesson 1: How the Internet Works — DNS, TCP, HTTP, TLS in 15 Minutes"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 1
tags:
  - fundamentals
  - system design
keywords:
  - DNS
  - TCP
  - HTTP
  - TLS
  - how the internet works
  - system design fundamentals
date: "2024-04-20T00:00:00.000Z"
---

Every system design interview starts with the same silent assumption: you already know how the internet works. Interviewers won't ask you to explain DNS. But when you confidently say "the client calls the API" without being able to say what actually happens between those words, the cracks show up in your design. Understanding the layers — DNS, TCP, HTTP, TLS — isn't trivia. It's the mental model that tells you where latency hides, why connections are expensive, and what breaks under load.

This is lesson one because everything else builds on it.

## The Core Concept

Think of a web request as a series of handshakes, each layer depending on the one below it.

**DNS: The Phone Book**

Before any byte of data travels, your computer needs an IP address. You typed `api.example.com`. That name means nothing to routers. The Domain Name System is a globally distributed database that translates names to IPs.

The resolution chain looks like this:

```
Your browser → OS cache → Local resolver (your ISP or 8.8.8.8)
  → Root nameserver (knows who manages .com)
    → TLD nameserver (knows who manages example.com)
      → Authoritative nameserver (returns 93.184.216.34)
```

Each step can be cached. TTL (Time To Live) on DNS records controls how long that cache is valid. A low TTL (60s) lets you update records quickly during a migration. A high TTL (86400s) means faster lookups — caches rarely miss — but changes propagate slowly.

For system design, this matters when you think about: failover speed, geographic routing, and traffic shifting between regions.

**TCP: The Reliable Pipe**

Once you have an IP, you need a connection. TCP is the protocol that makes data delivery reliable and ordered. Before sending anything useful, TCP does a three-way handshake:

```
Client → SYN → Server
Client ← SYN-ACK ← Server
Client → ACK → Server
```

Only after those three exchanges can data flow. Each round-trip takes time proportional to the physical distance — this is why latency from Sydney to New York is irreducible. The speed of light is a hard limit.

TCP also does congestion control. It starts slow (slow start), ramps up, and backs off when packets are dropped. This is why a fresh TCP connection feels sluggish compared to one that has been warmed up.

**TLS: The Encrypted Wrapper**

For HTTPS, TLS sits on top of TCP and adds encryption. The TLS handshake (in TLS 1.3) adds one more round-trip on top of TCP's handshake: the client and server negotiate cipher suites and exchange keys. TLS 1.3 is significantly faster than 1.2 because it cut the handshake from two round-trips to one.

The full cost to establish a connection: 1 RTT for TCP + 1 RTT for TLS 1.3 = 2 RTTs before any application data flows. At 100ms RTT (a reasonable transatlantic number), that's 200ms of setup overhead on every cold connection.

**HTTP: The Application Protocol**

HTTP defines the structure of requests and responses. HTTP/1.1 reuses TCP connections (keep-alive) but sends requests serially. HTTP/2 multiplexes multiple streams over one connection, removing head-of-line blocking at the application layer. HTTP/3 rebuilds on QUIC (UDP-based), removing head-of-line blocking at the transport layer too.

## How to Design It

When you're designing a system, this stack directly informs decisions:

**Connection pooling** exists because TCP+TLS setup is expensive. Rather than opening a new connection per request, services maintain a pool of established connections and reuse them. In Go, `http.Client` does this by default via `http.Transport`.

```go
// Default transport already does connection pooling
client := &http.Client{
    Transport: &http.Transport{
        MaxIdleConns:        100,
        MaxIdleConnsPerHost: 10,
        IdleConnTimeout:     90 * time.Second,
    },
    Timeout: 10 * time.Second,
}
```

**DNS caching strategies** matter for microservices. If service A calls service B by hostname, and B's IP changes, how long until A notices? If you're using Kubernetes, internal DNS TTLs are often very low (5–30 seconds). But Go's default DNS resolver caches aggressively. You may need to tune this.

**Geographic placement** follows from RTT physics. You can't make packets travel faster than light. You can put servers closer to users. This is the entire business model of CDNs, which we'll cover in Lesson 6.

## Real-World Example

Let me trace what happens when you open `https://github.com` cold:

1. Browser checks its DNS cache. Miss.
2. OS checks its resolver cache. Miss.
3. Query goes to your ISP's resolver, which asks the root servers, then `.com`, then `github.com`'s authoritative server. Returns an IP. ~50ms in the worst case.
4. TCP SYN sent to GitHub's IP. SYN-ACK returns. ACK sent. ~20ms RTT if you're in the US.
5. TLS 1.3 handshake. ~20ms more.
6. HTTP GET / sent. HTML response returns. ~20ms more.
7. Browser parses HTML, discovers CSS/JS/images, opens more connections (or reuses via HTTP/2 streams).

Total from cold start to first byte: easily 100–200ms even in ideal conditions. This is the baseline you're working with. Every optimization in system design — CDNs, caching, keep-alive, preconnect hints — is chipping away at this number.

## Interview Tips

When an interviewer says "the client sends a request to the server," you should mentally ask: which protocol? Same datacenter or cross-region? Is this a fresh connection or reusing a pool? These questions shape your latency estimates.

If asked "why is your service slow for users in Europe?", the answer often starts with RTT. If your servers are in us-east-1 and the RTT to Europe is 100ms, every synchronous call in your request path adds 100ms. Eliminating round-trips (batching, caching) or moving compute closer (edge functions, regional deployments) are the levers.

DNS matters during incidents. If you need to redirect traffic quickly, your DNS TTL determines how fast that propagates. Teams that haven't thought about this ahead of time find themselves waiting hours for a failover to complete while users are down.

Understand the difference between a TCP connection failing (network error) and an HTTP error (4xx, 5xx). These are different layers. A load balancer returns a 502 when it can't reach your backend — but the connection between the client and the load balancer was fine. Knowing which layer broke tells you where to look.

## Key Takeaway

The internet is a stack of protocols, each solving a different problem: DNS resolves names, TCP provides reliable ordered delivery, TLS encrypts and authenticates, HTTP gives you request/response semantics. Every layer adds latency — typically one or more round-trips. System design decisions at every level (connection pooling, DNS TTL tuning, CDN placement, HTTP/2 adoption) are about reducing unnecessary round-trips and moving data closer to where it's needed. Know the stack, and you'll always have a principled answer for why things are slow and what to do about it.

---

**Next:** [Lesson 2: Load Balancing — L4 vs L7 and why it matters](/post/fundamentals/sd-load-balancing/)
