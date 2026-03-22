---
title: "Lesson 5: DNS Resolution and Custom Resolvers — Understanding name resolution"
author: Atharva Pandey
series: "Rust Networking & Distributed Systems"
lesson: 5
keywords: [Rust, rust, networking, distributed, DNS, resolver, name resolution, hickory, trust-dns]
tags: [Rust tutorial, rust, networking, distributed-systems]
date: '2025-05-21T13:55:00.000Z'
---

A few months back, our entire staging environment went down for an hour. Not because any service crashed — because someone changed a DNS record and forgot that our Kubernetes ingress had a 5-minute TTL cache while the CDN had a 24-hour cache. Half our traffic was going to the old IP, half to the new one. Debugging it took forever because `dig` on my laptop showed the correct answer, but the services inside the cluster were seeing stale records.

That incident drove me to actually understand DNS instead of treating it as magic. Turns out, Rust has excellent tools for working with DNS programmatically — and building a custom resolver gives you insights you can't get from reading RFCs.

## How DNS Actually Works

Before we write code, let's nail down what happens when you call `TcpStream::connect("api.example.com:443")`:

1. The runtime calls `getaddrinfo()` — a libc function that handles name resolution.
2. `getaddrinfo()` checks `/etc/hosts` first.
3. Then it queries the resolver configured in `/etc/resolv.conf` (usually your router or ISP's DNS server, or `8.8.8.8` if you've configured Google's).
4. That resolver checks its cache. Cache miss? It walks the DNS hierarchy — root servers → `.com` servers → `example.com`'s authoritative nameserver.
5. The answer comes back: one or more IP addresses, each with a TTL (time to live).
6. Everyone caches the result for the TTL duration.

This whole dance happens every time you make an HTTP request. It's normally 1-50ms, but when things go wrong — misconfigured nameservers, expired domains, DNSSEC validation failures — it can hang for seconds or return wrong answers silently.

## Basic DNS Resolution in Rust

The standard library doesn't have a native async DNS resolver, but `tokio::net::lookup_host` gives you basic resolution:

```rust
use tokio::net;
use std::net::SocketAddr;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Simple hostname resolution
    let addrs: Vec<SocketAddr> = net::lookup_host("google.com:443")
        .await?
        .collect();

    println!("google.com resolves to:");
    for addr in &addrs {
        println!("  {addr}");
    }

    // Note: this uses the system resolver (getaddrinfo)
    // It respects /etc/hosts and /etc/resolv.conf
    // But it's blocking under the hood — tokio runs it on a threadpool

    Ok(())
}
```

This works for simple cases, but it has limitations. You can't control which DNS server is queried, you can't see TTLs, you can't do anything beyond A/AAAA record lookups, and the blocking `getaddrinfo` call ties up a threadpool slot.

## Hickory DNS — The Full-Featured Resolver

For anything beyond basic lookups, you want **hickory-dns** (formerly trust-dns). It's a pure-Rust DNS implementation that supports async resolution, DNSSEC validation, DNS-over-TLS, DNS-over-HTTPS, and custom record types.

```toml
[dependencies]
hickory-resolver = "0.24"
tokio = { version = "1", features = ["full"] }
```

```rust
use hickory_resolver::config::{ResolverConfig, ResolverOpts};
use hickory_resolver::TokioAsyncResolver;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Use Google's public DNS
    let resolver = TokioAsyncResolver::tokio(
        ResolverConfig::google(),
        ResolverOpts::default(),
    );

    // A record lookup
    let response = resolver.lookup_ip("github.com").await?;

    println!("github.com A/AAAA records:");
    for ip in response.iter() {
        println!("  {ip}");
    }

    // TTL information
    let records = resolver
        .lookup("github.com", hickory_resolver::proto::rr::RecordType::A)
        .await?;

    for record in records.iter() {
        println!(
            "  {} (TTL: {}s)",
            record.data().unwrap(),
            record.ttl(),
        );
    }

    Ok(())
}
```

## Querying Different Record Types

DNS isn't just about IP addresses. There's a whole zoo of record types, and querying them programmatically is incredibly useful for debugging and monitoring.

```rust
use hickory_resolver::config::{ResolverConfig, ResolverOpts};
use hickory_resolver::TokioAsyncResolver;
use hickory_resolver::proto::rr::RecordType;

async fn comprehensive_lookup(
    resolver: &TokioAsyncResolver,
    domain: &str,
) -> Result<(), Box<dyn std::error::Error>> {
    println!("=== DNS records for {domain} ===\n");

    // A records (IPv4)
    match resolver.lookup(domain, RecordType::A).await {
        Ok(records) => {
            println!("A records:");
            for r in records.iter() {
                println!("  {} (TTL: {}s)", r.data().unwrap(), r.ttl());
            }
        }
        Err(e) => println!("A: {e}"),
    }

    // AAAA records (IPv6)
    match resolver.lookup(domain, RecordType::AAAA).await {
        Ok(records) => {
            println!("\nAAAA records:");
            for r in records.iter() {
                println!("  {} (TTL: {}s)", r.data().unwrap(), r.ttl());
            }
        }
        Err(e) => println!("AAAA: {e}"),
    }

    // MX records (mail servers)
    match resolver.lookup(domain, RecordType::MX).await {
        Ok(records) => {
            println!("\nMX records:");
            for r in records.iter() {
                println!("  {} (TTL: {}s)", r.data().unwrap(), r.ttl());
            }
        }
        Err(e) => println!("MX: {e}"),
    }

    // TXT records (SPF, DKIM, verification)
    match resolver.lookup(domain, RecordType::TXT).await {
        Ok(records) => {
            println!("\nTXT records:");
            for r in records.iter() {
                println!("  {} (TTL: {}s)", r.data().unwrap(), r.ttl());
            }
        }
        Err(e) => println!("TXT: {e}"),
    }

    // NS records (nameservers)
    match resolver.lookup(domain, RecordType::NS).await {
        Ok(records) => {
            println!("\nNS records:");
            for r in records.iter() {
                println!("  {} (TTL: {}s)", r.data().unwrap(), r.ttl());
            }
        }
        Err(e) => println!("NS: {e}"),
    }

    // CNAME records
    match resolver.lookup(domain, RecordType::CNAME).await {
        Ok(records) => {
            println!("\nCNAME records:");
            for r in records.iter() {
                println!("  {} (TTL: {}s)", r.data().unwrap(), r.ttl());
            }
        }
        Err(e) => println!("CNAME: {e}"),
    }

    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let resolver = TokioAsyncResolver::tokio(
        ResolverConfig::google(),
        ResolverOpts::default(),
    );

    comprehensive_lookup(&resolver, "google.com").await?;
    println!();
    comprehensive_lookup(&resolver, "github.com").await?;

    Ok(())
}
```

MX lookups are handy for email validation — not just "is this a valid email format" but "does this domain actually accept email." TXT records are where you find SPF policies, DKIM keys, and domain verification tokens for services like Google Workspace.

## Building a DNS Health Checker

Here's a practical tool — a DNS monitor that checks multiple nameservers and alerts you when they disagree.

```rust
use hickory_resolver::config::{NameServerConfig, Protocol, ResolverConfig, ResolverOpts};
use hickory_resolver::TokioAsyncResolver;
use std::collections::HashSet;
use std::net::{IpAddr, SocketAddr};
use std::time::{Duration, Instant};

struct DnsServer {
    name: String,
    addr: IpAddr,
}

struct ResolveResult {
    server: String,
    ips: Vec<IpAddr>,
    duration: Duration,
    error: Option<String>,
}

async fn resolve_with_server(
    server: &DnsServer,
    domain: &str,
) -> ResolveResult {
    let ns = NameServerConfig::new(
        SocketAddr::new(server.addr, 53),
        Protocol::Udp,
    );

    let mut config = ResolverConfig::new();
    config.add_name_server(ns);

    let mut opts = ResolverOpts::default();
    opts.timeout = Duration::from_secs(5);
    opts.attempts = 1;

    let resolver = TokioAsyncResolver::tokio(config, opts);
    let start = Instant::now();

    match resolver.lookup_ip(domain).await {
        Ok(response) => {
            let ips: Vec<IpAddr> = response.iter().collect();
            ResolveResult {
                server: server.name.clone(),
                ips,
                duration: start.elapsed(),
                error: None,
            }
        }
        Err(e) => ResolveResult {
            server: server.name.clone(),
            ips: vec![],
            duration: start.elapsed(),
            error: Some(e.to_string()),
        },
    }
}

async fn check_dns_consistency(domain: &str) -> Result<(), Box<dyn std::error::Error>> {
    let servers = vec![
        DnsServer {
            name: "Google".into(),
            addr: "8.8.8.8".parse()?,
        },
        DnsServer {
            name: "Cloudflare".into(),
            addr: "1.1.1.1".parse()?,
        },
        DnsServer {
            name: "Quad9".into(),
            addr: "9.9.9.9".parse()?,
        },
    ];

    println!("Checking DNS consistency for {domain}\n");

    let mut handles = vec![];
    for server in &servers {
        let domain = domain.to_string();
        let server = DnsServer {
            name: server.name.clone(),
            addr: server.addr,
        };
        handles.push(tokio::spawn(async move {
            resolve_with_server(&server, &domain).await
        }));
    }

    let mut all_ips: Vec<HashSet<IpAddr>> = vec![];
    for handle in handles {
        let result = handle.await?;
        match &result.error {
            Some(err) => {
                println!(
                    "  {} — ERROR ({:?}): {err}",
                    result.server, result.duration
                );
            }
            None => {
                let ips: Vec<String> =
                    result.ips.iter().map(|ip| ip.to_string()).collect();
                println!(
                    "  {} — {:?} ({:?})",
                    result.server,
                    ips,
                    result.duration
                );
                let ip_set: HashSet<IpAddr> = result.ips.into_iter().collect();
                all_ips.push(ip_set);
            }
        }
    }

    // Check consistency
    if all_ips.len() > 1 {
        let first = &all_ips[0];
        let consistent = all_ips.iter().all(|s| s == first);
        if consistent {
            println!("\n  All servers agree.");
        } else {
            println!("\n  WARNING: Servers returned different results!");
        }
    }

    Ok(())
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    check_dns_consistency("github.com").await?;
    println!();
    check_dns_consistency("example.com").await?;

    Ok(())
}
```

I've used a tool like this to catch DNS propagation issues during domain migrations. It queries multiple public resolvers in parallel and flags disagreements. In production, you'd run this on a schedule and send alerts.

## Custom Resolver for reqwest

One powerful use case: plugging a custom DNS resolver into your HTTP client. Maybe you want to use DNS-over-HTTPS for privacy, or route certain domains to internal nameservers, or implement client-side load balancing based on DNS weights.

```rust
use hickory_resolver::config::{ResolverConfig, ResolverOpts};
use hickory_resolver::TokioAsyncResolver;
use reqwest::dns::{Addrs, Name, Resolve, Resolving};
use std::io;
use std::net::SocketAddr;
use std::sync::Arc;

#[derive(Clone)]
struct HickoryResolver {
    resolver: Arc<TokioAsyncResolver>,
}

impl HickoryResolver {
    fn new() -> Self {
        let resolver = TokioAsyncResolver::tokio(
            ResolverConfig::cloudflare(),
            ResolverOpts::default(),
        );
        Self {
            resolver: Arc::new(resolver),
        }
    }
}

impl Resolve for HickoryResolver {
    fn resolve(&self, name: Name) -> Resolving {
        let resolver = self.resolver.clone();

        Box::pin(async move {
            let response = resolver
                .lookup_ip(name.as_str())
                .await
                .map_err(|e| -> Box<dyn std::error::Error + Send + Sync> {
                    Box::new(io::Error::new(io::ErrorKind::Other, e.to_string()))
                })?;

            let addrs: Vec<SocketAddr> = response
                .iter()
                .map(|ip| SocketAddr::new(ip, 0))
                .collect();

            let addrs: Addrs = Box::new(addrs.into_iter());
            Ok(addrs)
        })
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let resolver = HickoryResolver::new();

    let client = reqwest::Client::builder()
        .dns_resolver(Arc::new(resolver))
        .build()?;

    let resp = client
        .get("https://httpbin.org/get")
        .send()
        .await?;

    println!("Status: {}", resp.status());
    println!("Body: {}", resp.text().await?);

    Ok(())
}
```

Now every HTTP request from this client goes through Cloudflare's DNS instead of the system resolver. You could extend this to implement split-horizon DNS — internal domains go to your corporate nameserver, everything else goes to Cloudflare.

## DNS Caching Strategies

The default hickory resolver caches based on TTL, which is usually what you want. But sometimes you need more control:

```rust
use hickory_resolver::config::{ResolverConfig, ResolverOpts};
use hickory_resolver::TokioAsyncResolver;
use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::RwLock;

struct CachedResolver {
    resolver: TokioAsyncResolver,
    cache: RwLock<HashMap<String, CacheEntry>>,
    min_ttl: Duration,
    max_ttl: Duration,
}

struct CacheEntry {
    ips: Vec<IpAddr>,
    expires_at: Instant,
}

impl CachedResolver {
    fn new(min_ttl: Duration, max_ttl: Duration) -> Self {
        let resolver = TokioAsyncResolver::tokio(
            ResolverConfig::google(),
            ResolverOpts::default(),
        );

        Self {
            resolver,
            cache: RwLock::new(HashMap::new()),
            min_ttl,
            max_ttl,
        }
    }

    async fn resolve(&self, domain: &str) -> Result<Vec<IpAddr>, Box<dyn std::error::Error>> {
        // Check cache first
        {
            let cache = self.cache.read().await;
            if let Some(entry) = cache.get(domain) {
                if entry.expires_at > Instant::now() {
                    return Ok(entry.ips.clone());
                }
            }
        }

        // Cache miss or expired — resolve
        let response = self.resolver.lookup_ip(domain).await?;
        let ips: Vec<IpAddr> = response.iter().collect();

        // Clamp TTL between min and max
        let dns_ttl = response
            .as_lookup()
            .records()
            .first()
            .map(|r| Duration::from_secs(r.ttl() as u64))
            .unwrap_or(self.min_ttl);

        let ttl = dns_ttl.clamp(self.min_ttl, self.max_ttl);

        // Update cache
        let entry = CacheEntry {
            ips: ips.clone(),
            expires_at: Instant::now() + ttl,
        };

        self.cache.write().await.insert(domain.to_string(), entry);

        Ok(ips)
    }
}
```

Clamping TTLs is important in production. Some domains set ridiculously low TTLs (5 seconds) for failover purposes, but that means you're doing a DNS lookup on nearly every request. Setting a minimum TTL of 30-60 seconds is a reasonable compromise between freshness and performance.

On the flip side, some domains set very high TTLs (24 hours), and if they change their IP, your service won't notice for a day. A maximum TTL caps that risk.

## Reverse DNS Lookups

Sometimes you have an IP and want to know the hostname. Reverse DNS (PTR records) handles this:

```rust
async fn reverse_lookup(
    resolver: &TokioAsyncResolver,
    ip: IpAddr,
) -> Result<Vec<String>, Box<dyn std::error::Error>> {
    let names = resolver.reverse_lookup(ip).await?;
    let hostnames: Vec<String> = names
        .iter()
        .map(|name| name.to_string().trim_end_matches('.').to_string())
        .collect();
    Ok(hostnames)
}
```

Reverse DNS is useful for logging — instead of "connection from 140.82.121.4", you get "connection from lb-140-82-121-4.github.com". Just don't rely on it for security — anyone can set up a PTR record for their IP to say whatever they want.

## What's Next

DNS resolves names to addresses, but the connection between your client and those addresses needs to be encrypted. Next, we'll dive into TLS in Rust — using rustls and native-tls to secure your network communication.
