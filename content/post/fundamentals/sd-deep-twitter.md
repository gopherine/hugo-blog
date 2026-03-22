---
title: "Lesson 5: Design Twitter/X — Tweet fanout, timeline ranking, trending topics at 500M users"
author: "Atharva Pandey"
series: "System Design Deep Dives"
lesson: 5
tags:
  - fundamentals
  - system design
  - interviews
keywords:
  - Twitter system design
  - tweet fanout
  - timeline ranking
  - trending topics
  - celebrity problem distributed systems
date: "2024-11-14T00:00:00.000Z"
---

Twitter is the classic system design problem for good reason. It looks like a glorified blog until you start pulling on the threads: how does a tweet from a user with 100 million followers appear in every follower's timeline within seconds? How do you rank timelines without reading millions of tweets per request? How do you identify trending topics across 500 million users in near real-time? Each of these is a genuinely hard problem, and they interact in non-obvious ways.

I've studied Twitter's architecture across multiple public engineering blog posts and talks over the years. What strikes me is how many times they've redesigned the fanout system specifically — it's the hardest part, and it has no perfect solution.

## Requirements

**Functional requirements**
- Users post tweets (text, images, video, links) up to 280 characters of text
- Users follow other users
- Home timeline shows tweets from all followed accounts, reverse chronological with algorithmic ranking
- Search returns recent and relevant tweets for a query
- Trending topics surface the most discussed subjects in real time
- Notifications for mentions, likes, retweets, follows

**Non-functional requirements**
- Timeline load latency: under 200ms (p99)
- Fanout latency: new tweet visible in follower timelines within ~5 seconds
- Scale: 500M daily active users, 500M tweets per day
- Read-to-write ratio: heavily skewed read-heavy; timeline reads dwarf tweet writes

**Scale estimates**
- 500M tweets/day → ~5,800 tweets/second average, ~15,000/second peak
- Average follower count: ~200 (long tail with celebrities at 100M+)
- A typical tweet fanout: 5,800 tweets/sec × 200 followers = ~1.16M write ops/second
- Celebrity tweet fanout: Cristiano Ronaldo tweets → 100M+ writes in seconds

## High-Level Design

Twitter's architecture has two fundamentally different read paths for timelines:

1. **Push (fan-out on write)**: when a tweet is posted, pre-compute and write to every follower's timeline cache. Reads are fast — just fetch the pre-built timeline.
2. **Pull (fan-out on read)**: when a user loads their timeline, query all followed accounts and merge their recent tweets. Reads are expensive; no write amplification.

Neither works for all users. The hybrid approach — which Twitter uses — applies push for normal users and pull for users following celebrities. Let's build this up from first principles.

## Deep Dive

**The Fan-out Service**

When a user posts a tweet, the flow is:

1. Tweet is persisted to the Tweet Store (a distributed key-value or document store, sharded by tweet ID)
2. The Fan-out Service reads the author's follower list from the Social Graph Service
3. For each follower (excluding celebrity followers — more on this below), the fan-out service writes the tweet ID into the follower's Timeline Cache entry

```go
type FanoutJob struct {
    TweetID  int64
    AuthorID int64
    PostedAt time.Time
}

func (f *FanoutService) Process(ctx context.Context, job FanoutJob) error {
    followers, err := f.socialGraph.GetFollowers(ctx, job.AuthorID)
    if err != nil {
        return fmt.Errorf("fetching followers for %d: %w", job.AuthorID, err)
    }

    // Process in batches to avoid single giant transaction
    const batchSize = 1000
    for i := 0; i < len(followers); i += batchSize {
        end := i + batchSize
        if end > len(followers) {
            end = len(followers)
        }
        batch := followers[i:end]

        for _, followerID := range batch {
            // Skip celebrity followers — their timelines are built on read
            if f.isCelebrityFollower(followerID) {
                continue
            }
            if err := f.timelineCache.Prepend(ctx, followerID, job.TweetID); err != nil {
                // Log but don't fail — timeline will self-heal via pull
                f.logger.Warnf("failed to fanout tweet %d to follower %d: %v",
                    job.TweetID, followerID, err)
            }
        }
    }
    return nil
}
```

The timeline cache is Redis, with each user's timeline stored as a sorted set (score = timestamp) or a list (capped at ~800 entries). Prepending a tweet ID is O(1) for a list or O(log n) for a sorted set.

**The Celebrity Problem**

A user with 100 million followers would trigger 100 million write operations on a single tweet. Even distributed across a worker pool, this write storm causes:
- Massive queue backlog during high-activity periods (celebrity accounts post multiple times per day)
- Disproportionate infrastructure cost for a small number of accounts
- Spiky write load that's hard to provision for

Twitter's solution: celebrity accounts are identified by a follower count threshold (historically ~1M followers). Their tweets are not fanned out to individual timeline caches. Instead:

- When a user loads their timeline, the Timeline Service checks if they follow any celebrities
- If so, it fetches recent tweets from those celebrities directly from the Tweet Store (a pull query)
- It merges the celebrity tweets with the pre-built fan-out timeline from cache
- The merged result is ranked and returned

This hybrid approach bounds the write amplification from any single tweet while keeping reads fast for the common case (most users don't follow millions of celebrities).

```go
func (t *TimelineService) Build(ctx context.Context, userID int64) ([]Tweet, error) {
    // Fast path: pre-built cache
    cachedIDs, err := t.timelineCache.Get(ctx, userID, 200)
    if err != nil {
        return nil, fmt.Errorf("fetching timeline cache: %w", err)
    }

    // Merge: pull celebrity tweets directly
    celebrities, err := t.socialGraph.GetFollowedCelebrities(ctx, userID)
    if err != nil {
        return nil, fmt.Errorf("fetching followed celebrities: %w", err)
    }

    var allIDs []int64
    allIDs = append(allIDs, cachedIDs...)
    for _, celeb := range celebrities {
        recentIDs, _ := t.tweetStore.GetRecentByAuthor(ctx, celeb, 50)
        allIDs = append(allIDs, recentIDs...)
    }

    // Hydrate tweet IDs to full tweet objects
    tweets, err := t.tweetStore.GetByIDs(ctx, dedup(allIDs))
    if err != nil {
        return nil, fmt.Errorf("hydrating tweets: %w", err)
    }

    // Rank and return top N
    return t.ranker.Rank(ctx, userID, tweets, 50), nil
}
```

**Timeline Ranking**

Reverse chronological order is simple but not what Twitter serves by default. The algorithmic timeline ranks tweets by a relevance score combining:

- Recency (strong signal; older tweets decay quickly)
- Engagement rate of the tweet (likes, retweets, replies relative to author follower count)
- Relationship signal (how often does this user engage with this author?)
- Media presence (tweets with images and video generally rank higher)

The ranking model is a trained ML model (logistic regression or a small neural net) applied to a candidate set of ~200–500 tweets. The ranking step adds latency — a few tens of milliseconds — but it happens on the already-fetched tweet set, not as a database query. The expensive part is fetching; the cheap part is scoring in memory.

**Trending Topics**

Trending topics is a streaming aggregation problem: find the most-mentioned topics (hashtags, noun phrases) across all tweets in a rolling time window (say, the last 1 hour), segmented by geography.

The naive approach — count hashtag occurrences in a database — doesn't work at 5,800 tweets/second. The production approach uses a streaming pipeline:

1. Every tweet is published to a high-throughput event stream (Kafka)
2. A stream processing layer (Flink or Storm) consumes the stream and maintains windowed counts
3. Counts are maintained using an approximate data structure — Count-Min Sketch for frequency counting, HyperLogLog for distinct user count (to prevent a coordinated spam campaign from manufacturing trends)
4. A trend eligibility filter removes topics that are trending due to bots or coordinated behavior (e.g., topics with very high frequency but few unique users)
5. Top K topics per window per region are materialized to a low-latency read store

```go
// Count-Min Sketch for approximate frequency counting
type CountMinSketch struct {
    depth  int
    width  int
    table  [][]int64
    hashes []hash.Hash64
}

func (cms *CountMinSketch) Add(item string) {
    for i, h := range cms.hashes {
        h.Reset()
        _, _ = h.Write([]byte(item))
        col := int(h.Sum64() % uint64(cms.width))
        cms.table[i][col]++
    }
}

func (cms *CountMinSketch) Estimate(item string) int64 {
    min := int64(math.MaxInt64)
    for i, h := range cms.hashes {
        h.Reset()
        _, _ = h.Write([]byte(item))
        col := int(h.Sum64() % uint64(cms.width))
        if cms.table[i][col] < min {
            min = cms.table[i][col]
        }
    }
    return min
}
```

Windowed counts are maintained with a sliding window: as each time bucket expires, its contribution is subtracted from the running total. The Top-K extraction (find the K highest frequency items from millions of candidates) uses a min-heap of size K.

## Scaling Challenges

**Search and the indexing problem**

Search requires a full-text index over 500M tweets per day. Twitter built their own real-time search index (Earlybird, based on Lucene) that indexes tweets within seconds of posting. The index is sharded temporally — recent tweets are hot and queried frequently, so they're stored on high-memory, high-CPU machines. Older tweets are on cheaper hardware. A search query fans out to multiple index shards in parallel and merges results.

**Retweets and quote tweets**

A viral retweet can trigger another fan-out cascade. If Celebrity A posts a tweet and Celebrity B (with 80M followers) retweets it, you now have two celebrity fan-outs. The fan-out service must recognize this and not double-write to timelines of users who follow both A and B. Deduplication by tweet ID in the timeline cache handles this — prepending a tweet ID that already exists in a sorted set is a no-op.

**Write amplification at scale**

The fan-out service processes tweets through a queue (Kafka). Worker pools consume from the queue. At 5,800 tweets/second with 200 followers average, that's ~1.16M timeline writes per second. With Redis at sub-millisecond write latency and horizontal sharding by user ID, this is achievable but requires careful capacity planning. Twitter has reported using hundreds of Redis instances for timeline storage.

## Interview Tips

The Twitter problem is rich enough that candidates can go in many directions. Here's where to focus your energy:

**Lead with the fanout decision.** Push vs. pull vs. hybrid is the central architectural choice. State it clearly: push for normal users (fast reads, write amplification), pull for celebrities (no write amplification, slightly slower reads requiring merge), hybrid for everyone else. This shows you understand the core tradeoff.

**Quantify the celebrity problem.** "A user with 100M followers would cause 100M write operations per tweet" lands better than "celebrities are a special case." Numbers make the problem concrete.

**Talk about the timeline cache expiration.** Timelines are cached as capped lists (~800 entries). Entries older than the cap are evicted. If a user hasn't logged in for months, their cache might be empty — they get a cold start where the system falls back to a pull query.

**Mention approximate counting for trends.** Count-Min Sketch or similar probabilistic structures are the right answer for frequency counting at tweet-stream scale. Exact counts would require a database write per hashtag per tweet, which doesn't scale.

## Key Takeaway

Twitter's architecture is a lesson in embracing asymmetry. The system is fundamentally asymmetric: celebrities write to millions of followers; the code and infrastructure must handle that asymmetry explicitly rather than treating all users identically. The hybrid fan-out (push for normal users, pull for celebrity tweets) is not elegant — it adds complexity to the read path — but it's the right engineering answer for the real-world distribution of follower counts. The other lesson: timeline reads dominate, so you optimize aggressively for read latency (pre-built caches, pre-computed rankings) at the cost of write complexity. That's the right tradeoff for a read-heavy social feed.

---

**Previous:** [Lesson 4: Design WhatsApp](/post/fundamentals/sd-deep-whatsapp/)

---

## 🎓 Course Complete!

You've finished **System Design Deep Dives**. Over five lessons we covered:

- **YouTube**: video upload pipelines, transcoding fan-out, adaptive bitrate streaming, CDN tiering
- **Uber**: geospatial indexing with Redis GEORADIUS, real-time location ingestion, surge pricing over H3 hexagons
- **Google Docs**: Operational Transformation vs. CRDTs, optimistic local application, the operation log as source of truth
- **WhatsApp**: Signal Protocol key exchange, at-least-once delivery with dedup, Sender Keys for group scaling
- **Twitter/X**: hybrid fan-out, the celebrity problem, timeline ranking, trending topics with Count-Min Sketch

These five systems cover the major patterns that appear across virtually every system design interview: pub/sub pipelines, spatial indexing, conflict-free data structures, end-to-end security models, and asymmetric read/write workloads. If you can walk through any of these end-to-end with confidence, you're ready.
