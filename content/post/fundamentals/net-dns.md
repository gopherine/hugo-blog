---
title: "Lesson 4: DNS — Resolution, caching, and why changes take time"
author: Atharva Pandey
keywords:
  - DNS
  - domain name system
  - resolution
  - TTL
  - caching
  - networking
  - backend engineering
tags:
  - fundamentals
  - networking
series: "Networking for Backend Engineers"
lesson: 4
date: '2024-06-15T00:00:00.000Z'
---

We pushed a production incident fix at 3am — rotated to a new IP address for a critical service, updated the DNS record, set the TTL to 60 seconds. Thirty minutes later, half our users were still hitting the broken server. The other half were fine. We had updated the DNS record correctly. The TTL had expired. Yet somehow, stale answers were persisting. That night taught me more about DNS than any documentation had.

DNS is the phone book of the internet, and like any phone book, it can be out of date. But the failure modes are subtle, the caching is layered, and the rules around TTL are not always followed by the parties involved. If you run production services, you need to understand this.

## How It Works

DNS translates human-readable names (`api.example.com`) into IP addresses. The resolution is hierarchical and involves multiple servers.

**The Resolution Hierarchy**

When a client resolves a name, the query flows through several layers:

```
Client App
    |
    v
OS Resolver (checks /etc/hosts, then local resolver cache)
    |
    v
Recursive Resolver (usually your ISP or 8.8.8.8, 1.1.1.1)
    |
    v (on cache miss)
Root Name Server (.) → knows where to find TLDs
    |
    v
TLD Name Server (.com) → knows which NS records handle example.com
    |
    v
Authoritative Name Server (ns1.example.com) → knows the actual A record
```

A full resolution (cache miss all the way down) looks like:

1. Client asks recursive resolver: "What's the IP for `api.example.com`?"
2. Recursive resolver asks root server: "Who handles `.com`?"
3. Root says: "Go ask Verisign's TLD servers" → returns NS records for `.com`.
4. Recursive resolver asks Verisign: "Who handles `example.com`?"
5. Verisign says: "Go ask `ns1.example.com`" → returns NS records for `example.com`.
6. Recursive resolver asks `ns1.example.com`: "What's the A record for `api.example.com`?"
7. Authoritative server responds: "It's `203.0.113.42`, TTL 300."
8. Recursive resolver caches this and returns it to the client.

This full resolution might take 100-300ms. Cached resolutions take under 1ms.

**Record Types**

- **A**: IPv4 address → `api.example.com → 203.0.113.42`
- **AAAA**: IPv6 address → `api.example.com → 2001:db8::1`
- **CNAME**: Canonical name alias → `www.example.com → example.com`
- **MX**: Mail exchange servers
- **TXT**: Arbitrary text (used for SPF, DKIM, domain verification)
- **NS**: Name server records — which servers are authoritative for a zone
- **SOA**: Start of Authority — metadata about a zone, including default TTL
- **SRV**: Service location records — used by Kubernetes, service discovery

**TTL and Why Changes Take Time**

Every DNS record has a TTL (Time To Live) in seconds. This is how long resolvers and clients are *allowed* to cache the answer. When you change a record, old answers remain valid at every layer until their individual TTL expires.

The classic mistake: your TTL is 3600 (1 hour). You need to change the IP right now. Even after you update the record, it can take up to 1 hour for all caches to expire. The fix is to lower your TTL *before* a planned change, wait for the old TTL to expire, make the change, then raise the TTL again.

**Why My 3am Fix Failed**

The TTL was set to 60 seconds. That should have meant a 1-minute propagation window. What went wrong:

1. Some recursive resolvers ignore TTLs and impose their own minimum (often 5 minutes or more).
2. Our own application was caching the resolved IP internally and not re-resolving. The Go `net.Dial` call was using a cached connection from a pool — DNS re-resolution never happened.
3. One ISP's resolver was returning the old answer for 15 minutes despite the TTL.

Item 2 is the one I had control over. The application was responsible for the longest stale period.

**Negative Caching**

DNS also caches negative responses — "that name doesn't exist" (NXDOMAIN). The SOA record specifies the negative TTL. If your service makes a DNS query for a name that doesn't exist yet (a race during deployment, a misconfiguration), the NXDOMAIN is cached and subsequent lookups fail for the full negative TTL even after the record is created. This bites teams doing rolling deployments with new service names.

## Why It Matters

DNS is in the critical path of every outbound connection your service makes. A failed or slow DNS resolution means a failed or slow connection. This is often overlooked because it's hidden inside `net.Dial`.

For Kubernetes services: internal DNS (`service.namespace.svc.cluster.local`) is handled by CoreDNS. Under heavy load, CoreDNS can become a bottleneck. Each pod's resolver makes multiple queries because of the search domain list — a query for `postgres` becomes queries for `postgres.namespace.svc.cluster.local`, `postgres.svc.cluster.local`, `postgres.cluster.local`, and finally `postgres` before resolving. Four queries for one resolution.

## Production Example

Diagnosing DNS issues in Go and fixing the Kubernetes search domain penalty:

```go
// The default Go resolver respects TTLs but has no built-in negative cache TTL control.
// For services that need predictable DNS behavior, use a custom resolver:

import (
    "context"
    "net"
    "time"
)

// Custom dialer with DNS timeout and retry
func newDialer() *net.Dialer {
    return &net.Dialer{
        Timeout:   30 * time.Second,
        KeepAlive: 30 * time.Second,
        Resolver: &net.Resolver{
            PreferGo: true, // Use Go's pure-Go resolver, not CGo
            Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
                d := net.Dialer{Timeout: 5 * time.Second}
                // Point at your internal DNS server
                return d.DialContext(ctx, "udp", "10.96.0.10:53")
            },
        },
    }
}

// For HTTP clients — ensure DNS re-resolution happens per-request
// rather than relying on connection pool's cached connections indefinitely
transport := &http.Transport{
    DialContext:         newDialer().DialContext,
    MaxIdleConnsPerHost: 10,
    // Connections older than this are closed, forcing DNS re-resolution
    IdleConnTimeout:     90 * time.Second,
}
```

For the Kubernetes ndots penalty, configure your pod's DNS to use `ndots:1` for internal services that use fully qualified names:

```yaml
# In your Deployment spec
spec:
  template:
    spec:
      dnsConfig:
        options:
          - name: ndots
            value: "1"
          - name: single-request-reopen
```

With `ndots:5` (default), `postgres.namespace.svc.cluster.local` — which has 5 dots — triggers all the search domain queries. With `ndots:1` and using the full FQDN in your connection string, a single query resolves immediately.

Debugging DNS in production:

```bash
# Check what a DNS query returns and from which server
dig +trace api.example.com

# Check TTL remaining on a cached record
dig api.example.com @8.8.8.8 | grep -A1 "ANSWER SECTION"

# On Linux — check systemd-resolved's cache
resolvectl statistics

# In Kubernetes — check CoreDNS logs
kubectl logs -n kube-system -l k8s-app=kube-dns --tail=50

# Measure DNS resolution time in Go
start := time.Now()
addrs, err := net.DefaultResolver.LookupHost(ctx, "api.example.com")
log.Printf("DNS lookup took %v, result: %v", time.Since(start), addrs)
```

For planned migrations: set your DNS TTL to 60 seconds at least 24 hours before the change. Wait for the old TTL (whatever it was before you lowered it) to expire everywhere. Make the change. Verify. Raise the TTL back to your normal value (300-3600 seconds for most records).

## The Tradeoffs

**Low TTL vs resolver load**: Lower TTLs mean fresher data but more DNS queries. Your authoritative DNS provider charges per query. A TTL of 60 on a high-traffic domain generates real query volume. The sweet spot for most production records is 300 seconds.

**CNAME chains vs A records**: CNAMEs add an extra DNS lookup per level of indirection. `www → example.com → 203.0.113.42` requires two lookups. For latency-critical paths, resolve to A records directly. Avoid deep CNAME chains.

**DNS-based load balancing vs proper LB**: Some services use multiple A records (round-robin DNS) for load balancing. This ignores server health — a failed server's IP stays in the rotation until someone manually removes it. Real load balancers are better. DNS is for routing to load balancers, not replacing them.

**Split-horizon DNS**: Returning different answers to internal vs external queries is powerful but operationally confusing. It means `api.example.com` resolves to your internal IP from inside the VPC and your external IP from outside. Debug carefully — the answer depends on where you're asking from.

**DNSSEC**: Cryptographic signatures on DNS responses prevent cache poisoning attacks (Kaminsky attack). Most major TLDs support it. Enabling DNSSEC for your domain is a good practice, but it adds complexity to DNS management and zone key rotation.

## Key Takeaway

DNS is a distributed cache with inconsistent expiry semantics. The TTL is a suggestion, not a guarantee. Changes take time to propagate because resolvers at every layer have their own cached copy. For production systems: lower TTLs before planned changes, use FQDN with `ndots:1` in Kubernetes to eliminate unnecessary queries, and don't let your application cache DNS results longer than the record's TTL. When something is mysteriously not connecting, `dig +trace` is always the first diagnostic tool.

---

**Previous:** [Lesson 3: TLS Handshake](/post/fundamentals/net-tls/)
**Next:** [Lesson 5: WebSockets — Upgrade, framing, vs SSE vs polling](/post/fundamentals/net-websockets/)
