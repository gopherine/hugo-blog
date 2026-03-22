---
title: "Lesson 2: Load Balancing — L4 vs L7 and Why It Matters"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 2
tags:
  - fundamentals
  - system design
keywords:
  - load balancing
  - L4 load balancer
  - L7 load balancer
  - round robin
  - consistent hashing
  - system design
date: "2024-05-05T00:00:00.000Z"
---

The first time I drew a system design diagram in an interview, I drew a box labeled "load balancer" and drew arrows from clients to it, and from it to servers. My interviewer asked, "What kind of load balancer?" I didn't have an answer. I knew load balancers existed. I didn't know they made fundamentally different decisions at different network layers — and that the choice between them shapes what your system can and cannot do.

This lesson closes that gap.

## The Core Concept

A load balancer sits in front of a pool of servers and distributes incoming traffic across them. The goal is to prevent any single server from becoming a bottleneck while making the pool of servers appear as a single endpoint to clients. But *how* a load balancer makes routing decisions depends on which layer of the network stack it operates at.

**Layer 4 (Transport Layer) Load Balancing**

L4 load balancers route based on TCP/UDP information: source IP, destination IP, source port, destination port. They don't look at the content of the traffic. They just forward packets.

```
[Client] → [L4 LB sees: src=1.2.3.4:50000 dst=10.0.0.1:443]
         → [Routes to backend B based on IP hash]
         → [All packets for this TCP connection go to backend B]
```

The key property: an L4 load balancer establishes one TCP connection to the client and another to the backend, and it forwards bytes between them (or in pass-through mode, doesn't even terminate the connection). It's fast because it does minimal work. It doesn't care if you're sending HTTP, WebSocket, gRPC, or raw binary — it just moves bytes.

**Layer 7 (Application Layer) Load Balancing**

L7 load balancers terminate the connection, read the application-layer content, and then forward to a backend based on that content. For HTTP, this means they can read headers, URLs, method types, and cookies.

```
[Client] → [L7 LB terminates TCP+TLS, reads HTTP request]
         → [Sees: GET /api/v2/users/123]
         → [Routes to users-service based on path prefix]
         → [Opens new connection to users-service backend]
```

The L7 LB re-initiates a new TCP connection to the chosen backend. This costs more CPU and adds a bit more latency — but it unlocks routing logic that's impossible at L4.

## How to Design It

The practical difference comes down to what you want to route on.

**When to use L4:**
- You need maximum throughput and minimum latency
- Your protocol isn't HTTP (gaming UDP, raw TCP, SMTP)
- You're doing TLS passthrough (the backend terminates TLS, not the LB)
- You want the backend to see the real client IP without extra headers

**When to use L7:**
- You have multiple services behind one IP, differentiated by URL path or hostname (virtual hosting)
- You want to route `/api/*` to your API servers and `/static/*` to a storage service
- You need sticky sessions based on cookies (not just IP)
- You want to A/B test by routing a percentage of requests to a new backend
- You need to handle gRPC (which multiplexes streams; L4 would pin all streams to one backend)

**Load Balancing Algorithms**

The algorithm determines *which* backend gets each request:

- **Round Robin**: requests 1, 2, 3 go to backends A, B, C in rotation. Simple, but ignores that server A might be faster or have lighter load.
- **Weighted Round Robin**: you assign weights. If A has twice the CPU of B, it gets twice the traffic. Useful when backends are heterogeneous.
- **Least Connections**: send the next request to the backend with the fewest active connections. Works well when request duration varies widely.
- **Consistent Hashing**: a hash of some request attribute (client IP, user ID from header) determines the backend. The same attribute always routes to the same backend. Crucial for caching (ensures a user's requests land on the same cache-warm server) and stateful protocols.

Here's a simple consistent hash ring concept in Go:

```go
type ConsistentHash struct {
    ring     map[uint32]string
    sorted   []uint32
    replicas int
}

func (ch *ConsistentHash) Add(node string) {
    for i := 0; i < ch.replicas; i++ {
        hash := crc32.ChecksumIEEE([]byte(fmt.Sprintf("%s-%d", node, i)))
        ch.ring[hash] = node
        ch.sorted = append(ch.sorted, hash)
    }
    sort.Slice(ch.sorted, func(i, j int) bool {
        return ch.sorted[i] < ch.sorted[j]
    })
}

func (ch *ConsistentHash) Get(key string) string {
    hash := crc32.ChecksumIEEE([]byte(key))
    // find first ring position >= hash
    idx := sort.Search(len(ch.sorted), func(i int) bool {
        return ch.sorted[i] >= hash
    })
    if idx == len(ch.sorted) {
        idx = 0
    }
    return ch.ring[ch.sorted[idx]]
}
```

The "replicas" trick distributes load more evenly — without it, nodes can end up owning disproportionate slices of the hash ring.

**Health Checks**

A load balancer that sends traffic to a dead server is worse than no load balancer. Every LB does health checks: either passive (watches for connection failures and removes the backend) or active (periodically sends a probe request to `/health`). Your backends need to expose a health endpoint that returns 200 if ready to serve and 503 if not.

## Real-World Example

AWS's ELB family demonstrates the distinction well. Their Classic Load Balancer was L4 only. The Application Load Balancer (ALB) is L7 — it routes by path and host, handles WebSocket upgrades, terminates TLS, and supports gRPC. The Network Load Balancer (NLB) is L4 — it handles millions of requests per second with ultra-low latency, preserves the client IP, and is used for things like gaming servers and financial systems where every millisecond matters.

Nginx used as a reverse proxy is an L7 load balancer. It reads HTTP, routes by `location` block, strips or adds headers, and can do SSL termination. HAProxy can operate at both L4 and L7 depending on configuration.

In a Kubernetes cluster, the Service object is typically an L4 load balancer (kube-proxy, iptables rules). An Ingress controller (like ingress-nginx or Traefik) is an L7 load balancer sitting in front of Services, routing by hostname and path.

## Interview Tips

When you draw a load balancer in an interview, say "I'll use an application load balancer here because we need to route traffic to different services by path." That specificity signals you know what you're talking about.

Common follow-up questions:

**"What happens when a backend goes down?"** — The LB detects this via health checks and removes it from rotation. In-flight requests to that backend fail and may need retry logic on the client or LB side.

**"How do you handle session stickiness?"** — L7 LBs can use a cookie (inserted by the LB or already present) to pin subsequent requests from the same user to the same backend. This is useful for stateful applications, but it hurts your ability to rebalance load when backends are added or removed.

**"What's the load balancer's single point of failure?"** — A valid concern. Production LBs run in HA pairs (active-passive or active-active). AWS ALB is already managed and multi-AZ. If you're running your own Nginx LBs, you need two with a floating IP managed by keepalived or similar.

**"How does the load balancer scale?"** — L4 LBs scale to millions of packets per second because they do so little per packet. L7 LBs are more expensive per connection. Both can be scaled horizontally with DNS round-robin or an L4 LB in front of them (the classic double-LB pattern).

## Key Takeaway

L4 load balancers route by IP/port — fast, protocol-agnostic, minimal processing. L7 load balancers route by application content — powerful, HTTP-aware, supports path routing and content inspection. Choose L4 for raw throughput and non-HTTP protocols; choose L7 when you need smart routing across services. The algorithm that decides which backend receives each request — round robin, least connections, consistent hashing — matters as much as the layer. Know both layers and both algorithm trade-offs, because interviewers will probe both.

---

**Previous:** [Lesson 1: How the Internet Works](/post/fundamentals/sd-internet-works/)
**Next:** [Lesson 3: Caching — The Hardest Easy Problem in CS](/post/fundamentals/sd-caching/)
