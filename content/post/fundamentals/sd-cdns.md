---
title: "Lesson 6: CDNs — Put Your Bytes Close to Your Users"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 6
tags:
  - fundamentals
  - system design
keywords:
  - CDN
  - content delivery network
  - edge caching
  - origin server
  - cache-control
  - CloudFront
  - system design
date: "2024-07-04T00:00:00.000Z"
---

Physics is the enemy of performance. Light travels through fiber optic cables at about 200,000 km/s — roughly two-thirds the speed of light in a vacuum. A round-trip from New York to London is about 11,000 km each way. That means the minimum possible latency for that trip is 55ms. You cannot engineer your way past it. What you can do is stop making the trip at all. Content Delivery Networks are the infrastructure that puts popular content at the edge — closer to users, so the bytes never have to travel far.

## The Core Concept

A CDN is a geographically distributed network of servers (called Points of Presence, or PoPs) that cache copies of your content close to end users. When a user requests a file, instead of traveling all the way to your origin server, the request goes to the nearest CDN edge server. If the edge has the content (a cache hit), it responds immediately from a few milliseconds away. If not (a cache miss), it fetches from your origin, caches the copy, and serves the user.

```
Without CDN:
User in Mumbai → [110ms] → Origin in us-east-1

With CDN:
User in Mumbai → [5ms] → CDN edge in Mumbai
                         (cache hit → no trip to origin)
```

The CDN serves two purposes: reducing latency by proximity, and reducing load on your origin server.

**What CDNs cache**

Originally, CDNs cached static assets: images, CSS, JavaScript, fonts, videos. These are ideal CDN candidates: they don't change per user, they're large (high transfer cost savings), and they can have long TTLs.

Modern CDNs can do more:
- **Dynamic caching**: cache API responses that vary by query parameter or header, using Vary headers to segment the cache
- **Edge compute**: run code at the edge (Cloudflare Workers, Lambda@Edge) to personalize responses or do A/B testing without going back to origin
- **TLS termination**: CDN terminates TLS at the edge, closer to the user
- **DDoS protection**: absorbs attack traffic before it reaches your origin

**Cache-Control headers**

Your origin controls CDN caching behavior through HTTP headers:

```
Cache-Control: public, max-age=86400, s-maxage=3600
```

- `public`: this response can be cached by CDN servers (not just the browser)
- `max-age=86400`: browsers cache for 24 hours
- `s-maxage=3600`: CDN caches for 1 hour (overrides max-age for shared caches)

```
Cache-Control: private, no-store
```

- `private`: only the user's browser can cache this, not the CDN
- `no-store`: don't cache at all

The `Vary` header tells CDNs that different versions exist for different request headers:
```
Vary: Accept-Encoding, Accept-Language
```
This creates separate cache entries for compressed vs. uncompressed and for different languages.

## How to Design It

**Static asset CDN setup**

Your build process generates hashed filenames: `main.abc123.js`. The hash changes when the file changes. You set a very long TTL (one year) because the filename itself guarantees cache-busting when content changes. No stale content risk.

```
/static/main.abc123.js   → Cache-Control: max-age=31536000, immutable
/static/main.def456.js   → New hash when code changes
```

**Dynamic content CDN**

For pages or API responses that change but can tolerate some staleness:

```
/api/products/featured → Cache-Control: s-maxage=60, stale-while-revalidate=30
```

`stale-while-revalidate=30` tells the CDN: after the content is stale, you can continue serving the old version for 30 more seconds while you asynchronously fetch a fresh copy. The user never waits — they get the fast stale response while the update happens in the background.

**Cache invalidation on CDNs**

When you deploy a new version, how do you invalidate cached content at edge nodes worldwide? Most CDNs offer cache purge APIs. You can purge by URL, by tag (all assets tagged `product-images`), or globally (nuke everything — expensive and should be rare).

```
POST https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache
{
  "files": ["https://example.com/api/products/featured"]
}
```

For static assets with hashed filenames, you don't need to purge — new deploys use new filenames.

**Origin Shield**

Rather than every edge node going to your origin on a miss, you can configure an intermediary layer between edges and origin (called an origin shield, or a "mid-tier cache"). Edge misses go to the shield; only shield misses go to origin. This collapses the origin traffic significantly when you have many edge PoPs.

```
User → CDN Edge (Miss) → CDN Shield (Hit) → response
                               ↓ (Miss)
                         Origin Server
```

## Real-World Example

Netflix's Open Connect CDN is one of the most studied content delivery systems in the world. They deploy their own specialized CDN appliances inside ISP data centers worldwide. When a user in Bangalore presses play on a show, the video data is served from a Netflix appliance physically inside their ISP's facility — microseconds away, not milliseconds. They pre-position popular content during off-peak hours so that edge nodes are warm before peak viewing times.

For a web app, consider how GitHub Pages works. Static HTML, CSS, and JS are distributed globally via Fastly CDN. The repository data is on GitHub's origin servers in the US, but the compiled site assets are served from edges worldwide. When you visit a GitHub Pages site from anywhere in the world, you get sub-50ms response times for assets even from the other side of the globe.

## Interview Tips

When should you not use a CDN? When content is highly personalized (user-specific API responses), when latency requirements are so strict that even a CDN miss is too slow (use local caching instead), or when you're in an environment where CDN edge nodes don't exist (internal enterprise networks, certain countries).

"What happens when your origin goes down — can the CDN save you?" Partially. If the CDN has a cached copy and the TTL hasn't expired, users still get served (this is called "stale-if-error" behavior, configurable in Cache-Control). But new requests that bypass the cache, or cache misses, will fail. The CDN is not a substitute for origin availability.

"How do you ensure users always get the latest version?" Hash-based cache-busting for static assets. Short TTLs for dynamic content. Combine with deployment automation that handles cache purges for anything that needs instant invalidation.

A common interview question for large-scale designs: "You're building a video streaming service — how do you design your CDN strategy?" Key points: use multiple CDN providers for redundancy, segment content by popularity (popular content at all edges, long-tail content at fewer edges or origin), consider peer-to-peer delivery for the most popular live streams, and implement adaptive bitrate streaming so the CDN can serve different quality levels.

## Key Takeaway

CDNs solve the physics problem of network latency by caching content close to users. Static assets with hashed filenames get long TTLs and are never stale. Dynamic content uses shorter TTLs plus `stale-while-revalidate` for fresh-enough delivery without blocking users. Cache-Control headers are your primary tool for controlling CDN behavior. Origin shield reduces origin load by adding a mid-tier cache. In any design serving global users with significant static or semi-static content, a CDN is one of the highest-leverage optimizations you can make — it reduces latency, reduces origin load, and absorbs DDoS traffic before it reaches your infrastructure.

---

**Previous:** [Lesson 5: Message Queues](/post/fundamentals/sd-message-queues/)
**Next:** [Lesson 7: Rate Limiting — Token Bucket, Sliding Window, Distributed](/post/fundamentals/sd-rate-limiting/)
