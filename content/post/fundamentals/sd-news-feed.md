---
title: "Lesson 10: Design a News Feed — Fan-out on Write vs Read"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 10
tags:
  - fundamentals
  - system design
keywords:
  - news feed design
  - fan-out on write
  - fan-out on read
  - Twitter feed
  - Facebook feed
  - timeline design
  - system design
date: "2024-09-06T00:00:00.000Z"
---

The news feed is the product feature that defines social media. Every time you open Instagram or Twitter, you see a personalized, ranked, real-time stream of content from people you follow. Behind that deceptively simple UI is one of the hardest distributed systems problems in consumer tech: how do you compute a personalized feed for hundreds of millions of users, where any piece of content needs to appear in potentially millions of feeds, within seconds of being posted?

## The Core Concept

When User A posts something, every one of their followers should see it in their feed. If A has 1,000 followers, that's 1,000 feeds that need updating. If A has 50 million followers (think a celebrity on Twitter), that's 50 million feed entries to create. The question is: when do you do that work?

**Fan-out on Write (Push Model)**

When a post is created, immediately distribute it to all followers' feeds. Each follower has a pre-computed feed stored in cache or a dedicated feed store. When they open the app, their feed is already ready — just read from their feed store.

```
User A posts → [Fan-out Service] → writes to feed of Follower 1
                                 → writes to feed of Follower 2
                                 → writes to feed of Follower N
```

Pros: reads are extremely fast — the feed is pre-built. No computation at read time.

Cons: writes are expensive and slow. A celebrity with 50M followers posting creates 50M write operations. This write amplification is the fundamental problem with fan-out on write at scale.

**Fan-out on Read (Pull Model)**

When a user opens their feed, query the posts from all the people they follow, merge them, rank them, and return the result. No pre-computation on write.

```
User B opens feed → fetch all IDs User B follows (say, 500 people)
                  → fetch recent posts from each of those 500 people
                  → merge and rank
```

Pros: writes are cheap — posting is just writing one record. Celebrities don't cause write amplification.

Cons: reads are expensive. Fetching from 500 accounts and merging is a lot of work per page load. At scale, this is too slow for a responsive feed.

**Hybrid: The Practical Answer**

Real systems use a hybrid approach. Twitter's architecture (pre-2023) is the canonical example:

- **Regular users** (< N followers, say 10,000): fan-out on write. When they post, distribute to followers' timelines immediately.
- **Celebrity users** (> N followers): fan-out on read. Their posts are not pre-distributed. Instead, when a follower loads their feed, celebrity posts are fetched live and merged with the pre-computed regular timeline.

```
User loads feed:
1. Fetch their pre-computed timeline (fan-out-on-write posts from normal users)
2. Fetch recent posts from any celebrities they follow
3. Merge, rank, return
```

This bounds the write fan-out to reasonable numbers (10,000 writes per post) while keeping reads fast (merging a small number of celebrity lists at read time is cheap).

## How to Design It

**Feed Storage**

Each user's feed is stored as a sorted list, ordered by timestamp or score. Redis sorted sets are ideal:

```
Key: feed:{userID}
Members: post IDs
Scores: timestamp (or ranking score)
```

When a post is added to a follower's feed:
```go
rdb.ZAdd(ctx, fmt.Sprintf("feed:%d", followerID), redis.Z{
    Score:  float64(post.CreatedAt.Unix()),
    Member: post.ID,
})
// Trim to keep only the last N posts per feed
rdb.ZRemRangeByRank(ctx, fmt.Sprintf("feed:%d", followerID), 0, -1001) // keep 1000
```

When a user loads their feed:
```go
postIDs, _ := rdb.ZRevRange(ctx, fmt.Sprintf("feed:%d", userID), 0, 19).Result()
// Fetch post details from DB or post cache
```

Notice we store only post IDs in the feed, not the full post content. This keeps the feed store small. The content is fetched separately (and cached).

**The Fan-out Service**

When a post is created, an event is published to Kafka. A fleet of fan-out workers consume from Kafka and write to follower feeds. Using an async queue means the post creation endpoint is fast (just write to DB + Kafka), and the fan-out happens in the background.

```
Post created → DB write + Kafka event
                   ↓
          Fan-out workers (scale horizontally)
                   ↓
          Redis sorted sets for each follower
```

The fan-out workers need the list of followers. This is fetched from a social graph service (a dedicated service that stores follow relationships, possibly in a graph database or a denormalized cache).

**Ranking**

A simple chronological feed is easy. A ranked feed (showing what you're most likely to engage with) is where ML enters. Twitter, Instagram, and Facebook all use ranking models that consider: recency, engagement history (do you like posts from this person?), content type (do you prefer video?), virality signals (how many others are engaging with this post right now?).

For a system design interview, you don't need to implement ML ranking. But acknowledge it: "The feed is stored by timestamp as a default sort key. A ranking service can reorder the top N posts using an engagement model before returning to the user. This ranking can be done at read time and cached per user."

**Pagination**

Never return all feed items at once. Use cursor-based pagination:

```
GET /feed?limit=20&cursor={lastPostTimestamp}
```

The cursor is the timestamp (or composite score) of the last item returned. On the next request, return items with score less than the cursor. This is stateless and efficient on sorted sets.

## Real-World Example

Twitter's original "Inbox" model was pure fan-out on write, which worked when users had modest follower counts. When celebrities joined with millions of followers, a single tweet triggered millions of writes and the system buckled. They moved to the hybrid model described above, plus a tiered cache: home timelines are in Redis (fast, volatile), and there's a backing store for users who haven't been active recently.

Facebook's approach is more sophisticated: they use a ranked model by default (not chronological), which adds ML complexity. Their backend fan-out goes through a system called "Aggregation," which merges multiple feed sources (friends' posts, group posts, ads, suggested content) into a unified ranked list.

Instagram uses a similar hybrid model, but notably shifted from chronological to ranked order in 2016 (and famously faced user backlash, then added a "Latest" filter to allow chronological viewing).

## Interview Tips

The fan-out on write vs. read question is almost certain to come up. Frame it as a trade-off: write amplification vs. read complexity. Then show the hybrid solution and explain why it's necessary for celebrity accounts.

"How do you handle a user following 10,000 accounts?" Their fan-out-on-read at read time would query 10,000 accounts. You cap it: if they follow more than a threshold, you switch their read model (or partition into who they interact with most).

"What about deleted posts?" If a post is deleted, remove it from the feed stores asynchronously. The feed might briefly show a deleted post until cleanup propagates.

"How do you handle the feed for a new user with no follows?" Show "discover" content, trending topics, suggested follows — a separate ranking system. Worth mentioning to show you've thought about onboarding.

"How do you ensure new posts appear near-instantly in feeds?" Fan-out workers should be fast (Redis writes are sub-millisecond). Keep the fan-out queue queue depth low and scale workers to keep latency under a few seconds end-to-end.

## Key Takeaway

News feed design is the fan-out problem. Fan-out on write pre-computes feeds for fast reads but creates write amplification for high-follower accounts. Fan-out on read is cheap for writes but expensive for reads. The hybrid model — fan-out on write for normal users, fan-out on read for celebrities — is what production systems use. Store only post IDs in feed stores (Redis sorted sets) and hydrate content separately. Use async fan-out via a message queue to keep post creation fast. Ranking is a read-time or background concern, not a storage concern. Cursor-based pagination keeps reads stateless and efficient.

---

**Previous:** [Lesson 9: Design a Chat System](/post/fundamentals/sd-chat-system/)
**Next:** [Lesson 11: Design a Notification System — Push vs Pull, Priority, Dedup](/post/fundamentals/sd-notifications/)
