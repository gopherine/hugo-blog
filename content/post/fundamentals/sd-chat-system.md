---
title: "Lesson 9: Design a Chat System — WebSocket, Presence, Message Ordering"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 9
tags:
  - fundamentals
  - system design
keywords:
  - chat system design
  - WebSocket
  - presence
  - message ordering
  - fan-out
  - real-time messaging
  - system design
date: "2024-08-21T00:00:00.000Z"
---

Building a chat system is the interview problem that catches people on protocol fundamentals. HTTP is a request-response protocol — the client asks, the server answers, and then the connection is idle. For chat, the server needs to push messages to clients the moment they arrive. This inversion of the HTTP model is what makes chat hard, and it's what drives the entire architecture.

## The Core Concept

**Why HTTP polling doesn't work at scale**

You could poll: the client calls `GET /messages?since=lastTimestamp` every second. At 10 million active users polling once per second, that's 10 million HTTP requests per second just to check for new messages — most of which return empty. The latency is up to one second. The cost is enormous.

**Long polling** is better: the client sends a request, the server holds it open until a message arrives (or a timeout). When a message arrives, the server responds and the client immediately re-establishes the connection. This reduces empty responses but still creates a new connection per message.

**WebSocket** is the right answer: a persistent bidirectional connection. After an HTTP upgrade handshake, the connection becomes a TCP tunnel. The server can push data to the client at any time without the client asking. Latency is near zero. Overhead per message is tiny (2-byte framing header vs. hundreds of bytes for HTTP headers).

```
HTTP Upgrade Handshake:
Client → GET /ws HTTP/1.1
         Upgrade: websocket
         Connection: Upgrade

Server → HTTP/1.1 101 Switching Protocols
         Upgrade: websocket
         Connection: Upgrade

[Now a persistent TCP connection — bidirectional]
Server → {"type":"message", "text":"hello"}
Client → {"type":"typing", "conversationId":"123"}
```

## How to Design It

**Architecture overview**

```
[Client] ←WebSocket→ [Chat Server A] ←→ [Message Queue (Kafka)]
[Client] ←WebSocket→ [Chat Server B] ←→ [Message Store (Cassandra)]
                                     ←→ [Presence Service]
                                     ←→ [Notification Service]
```

Users connect to one of many stateful chat servers. Each chat server maintains in-memory WebSocket connections for all users currently connected to it.

**Routing: how does a message reach the recipient?**

User A (connected to Server 1) sends a message to User B (connected to Server 3). Server 1 receives the message. How does it get to Server 3?

Option 1: **Broadcast via pub/sub**. Server 1 publishes to a channel (e.g., Redis Pub/Sub, or a Kafka topic per user/conversation). Server 3 subscribes to User B's channel and receives the message. Server 3 pushes it over User B's WebSocket.

Option 2: **Direct server-to-server**. A routing layer tracks which server each user is connected to. Server 1 looks up Server 3 and makes a direct gRPC call. Simpler, but creates tight coupling between servers.

For large-scale systems, the pub/sub approach scales better — adding more servers doesn't require updating a routing table.

**Message storage**

Chat messages need durable storage for:
- Loading history when a user opens a conversation
- Delivering to users who were offline when the message was sent

Cassandra is a popular choice for message storage because:
- Write-heavy workload (many messages written, history reads are less frequent)
- Natural time-series access pattern: "give me the last N messages in conversation X"
- Horizontal scale without complex resharding

```sql
-- Cassandra table design
CREATE TABLE messages (
    conversation_id UUID,
    message_id      TIMEUUID,  -- includes timestamp, naturally ordered
    sender_id       UUID,
    content         TEXT,
    created_at      TIMESTAMP,
    PRIMARY KEY (conversation_id, message_id)
) WITH CLUSTERING ORDER BY (message_id DESC);
```

Partition key: `conversation_id` — all messages in a conversation are on the same node, efficient for history loads.
Clustering key: `message_id` (TIMEUUID) — messages within a conversation are ordered by time.

**Message ordering**

Ordering is tricky in distributed systems. Two messages sent within milliseconds of each other from different devices may arrive at the server in any order. Clocks on different machines differ by potentially hundreds of milliseconds.

Solutions:
- **Server-assigned sequence numbers**: the server assigns a monotonically increasing sequence number per conversation when it stores the message. Clients use this for display order, not their local clock.
- **Lamport timestamps**: a logical clock that ensures causal ordering (if A happened before B, A's timestamp is lower). Doesn't require synchronized clocks.
- **TIMEUUID in Cassandra**: encodes both a timestamp and a random component, providing a natural sort order that's roughly chronological and unique.

For practical chat systems, server-side sequence numbers per conversation are sufficient and simple.

**Presence (online/offline status)**

Presence is its own hard sub-problem. A user is "online" if they have an active WebSocket connection. But connections can drop silently (mobile networks, sleep mode). The user might be on multiple devices.

The approach:
- When a WebSocket connects, write `user:online:{userID}` to Redis with a TTL (e.g., 30 seconds)
- The client sends a heartbeat every 20 seconds; the server refreshes the TTL
- If the heartbeat stops (user closes app, network drops), the TTL expires and the user appears offline
- When a user connects from a second device, increment a connection counter in Redis

For showing presence to others: use a pub/sub mechanism. When User A's presence changes, publish to a channel. Server subscribes on behalf of users who care about A's presence (their contacts/friends).

**Offline message delivery**

When a user is offline and receives a message, the chat server publishes to their channel but no server is subscribed (they're not connected). The message goes to the message store. When they reconnect, they sync: `GET /conversations?since=lastSyncTimestamp`. A push notification (APNs for iOS, FCM for Android) alerts them to reconnect.

## Real-World Example

Slack's architecture is notable. Each Slack workspace has channels, and users subscribe to channels. Messages are routed via a pub/sub system across a fleet of real-time servers. They store messages in Kafka for durability, then asynchronously write to MySQL (per-workspace sharded). The search index is built asynchronously from Kafka events. Message history is loaded from MySQL; real-time delivery is from the pub/sub layer.

WhatsApp famously runs on Erlang (BEAM VM), which is designed for massive concurrency of lightweight processes. Each user connection is essentially a process. The BEAM scheduler makes this extremely efficient — millions of connections per node. Messages are end-to-end encrypted, so WhatsApp's servers only hold ciphertext.

Discord uses a combination of WebSocket gateways and a custom voice/video infrastructure. For text, they migrated message storage from MongoDB to Cassandra as scale grew, which is a textbook example of the write-heavy, time-series access pattern matching Cassandra's strengths.

## Interview Tips

"How do you handle a user reconnecting after being offline?" Sync on reconnect: the client sends its last known message ID, the server returns all messages since then. For group conversations, this can be a lot of data — paginate it.

"What if the chat server crashes?" In-memory WebSocket state is lost. Clients reconnect (exponential backoff). The message store has all messages — no data loss. The pub/sub subscription is re-established on reconnect.

"How do you scale to 1 billion users?" You can't have a single Kafka topic for all users. Partition by user ID or conversation ID. Run hundreds of chat servers. Use geographically distributed clusters. The protocol (WebSocket + pub/sub) scales horizontally — adding servers is adding capacity.

"Read receipts and typing indicators?" Typing indicators are ephemeral — publish to the pub/sub layer without writing to DB (they don't need persistence). Read receipts require a write per message per reader — batching and async writes help.

## Key Takeaway

Chat systems require a persistent connection protocol (WebSocket) because HTTP's request-response model is too expensive for push delivery. Messages route between servers via pub/sub (Redis or Kafka) rather than direct server-to-server calls. Message storage in Cassandra fits the write-heavy, time-series access pattern. Presence uses TTL-based heartbeats in Redis. Ordering is solved server-side with sequence numbers or TIMEUUID, not client clocks. Each of these sub-problems — routing, storage, presence, ordering, offline delivery — is worth discussing in the interview, because the best candidates walk through all of them.

---

**Previous:** [Lesson 8: Design a URL Shortener](/post/fundamentals/sd-url-shortener/)
**Next:** [Lesson 10: Design a News Feed — Fan-out on Write vs Read](/post/fundamentals/sd-news-feed/)
