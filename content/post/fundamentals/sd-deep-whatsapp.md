---
title: "Lesson 4: Design WhatsApp — End-to-end encryption, message delivery guarantees, presence"
author: "Atharva Pandey"
series: "System Design Deep Dives"
lesson: 4
tags:
  - fundamentals
  - system design
  - interviews
keywords:
  - WhatsApp system design
  - end-to-end encryption
  - message delivery guarantees
  - Signal Protocol
  - presence system
date: "2024-09-14T00:00:00.000Z"
---

WhatsApp is deceptively simple from a user perspective: you send a message, it arrives. But building a messaging system that handles 100 billion messages per day with end-to-end encryption, reliable delivery semantics, and real-time presence for 2 billion users is a genuinely hard engineering problem. I find this problem particularly instructive because it forces you to confront three things simultaneously: cryptographic key management, message delivery guarantees, and the cost of maintaining online/offline state at massive scale.

## Requirements

**Functional requirements**
- One-to-one and group messaging (up to ~1,024 participants in a group)
- Messages are end-to-end encrypted — the server cannot read message content
- Delivery receipts: sent (server received), delivered (recipient device received), read (recipient opened)
- Media sharing: images, video, audio, documents
- Online presence: "last seen" and online/offline indicators
- Messages delivered to offline users when they come back online (with configurable TTL)

**Non-functional requirements**
- Message delivery latency: under 100ms for online recipients on good connections
- Delivery guarantee: at-least-once delivery with deduplication (exactly-once semantics from the user's perspective)
- Message retention on server: only while recipient is offline; deleted after delivery
- Strong security: forward secrecy — compromise of long-term keys does not expose past messages

**Scale estimates**
- 2 billion monthly active users, ~500 million daily active users
- 100 billion messages per day → ~1.16 million messages per second
- Average message size: 1 KB (text) to several MB (media)
- Group messages: a group of 500 members means one send triggers 499 deliveries

## High-Level Design

```
Sender Client → WebSocket → Chat Server → Message Queue → Delivery Service
                                ↓                                ↓
                         Key Distribution              Recipient Client (if online)
                           Service (KDS)                       or
                                                     Offline Store (if offline)
```

WhatsApp uses persistent WebSocket (or XMPP-based) connections. Each client maintains a long-lived connection to a chat server. Message routing goes: sender client → chat server → (queue) → delivery to recipient's connected chat server → recipient client.

The server never stores decrypted message content. It stores only encrypted ciphertext temporarily, until delivered.

## Deep Dive

**End-to-End Encryption with the Signal Protocol**

WhatsApp uses the Signal Protocol, which combines several cryptographic primitives to achieve both security and forward secrecy.

The key exchange mechanism is the **X3DH (Extended Triple Diffie-Hellman) protocol**. Each client registers a set of public keys with the Key Distribution Service:

1. **Identity Key (IK)**: long-term key pair, stable across the device's lifetime
2. **Signed Prekey (SPK)**: medium-term key pair, rotated every few weeks, signed by IK to prove authenticity
3. **One-Time Prekeys (OPKs)**: a batch of single-use key pairs uploaded to the server

When Alice wants to send Bob her first message, she fetches Bob's public IK, SPK, and one OPK from the KDS. She performs X3DH to derive a shared secret without Bob being online. The OPK is consumed — the server deletes it after Alice fetches it. This is what provides forward secrecy: even if Alice's long-term key is later compromised, the session key derived from the consumed OPK cannot be recomputed.

After the initial key exchange, the **Double Ratchet algorithm** manages ongoing message encryption. It combines a symmetric-key ratchet (new encryption key for every message) with a Diffie-Hellman ratchet (periodic fresh key exchange). The result: each message in a conversation uses a unique encryption key derived from a chain that incorporates both sides' recent key material.

```go
// Simplified representation of a message envelope
type MessageEnvelope struct {
    To          string    // recipient user ID (not encrypted)
    From        string    // sender user ID (not encrypted)
    MessageID   string    // UUID, used for dedup and delivery receipts
    SentAt      time.Time // server timestamp added on receipt
    Ciphertext  []byte    // encrypted with recipient's session key
    // The server sees only the envelope — never the plaintext
}
```

The server routes based on `To`, but cannot read `Ciphertext`. This is the architecture of genuine end-to-end encryption, as opposed to transport encryption (TLS only) which protects the wire but not the server.

**Message Delivery Guarantees**

WhatsApp's three-tick system (one grey tick = sent to server, two grey ticks = delivered to device, two blue ticks = read) requires careful tracking at each stage.

The protocol:
1. Sender sends message to chat server. Server acknowledges receipt and stores in a per-recipient queue. **One tick.**
2. When recipient's device receives the message (via WebSocket push), it sends a delivery acknowledgment to the server. Server deletes from queue, notifies sender. **Two ticks.**
3. When recipient opens the conversation, client sends a read receipt. Server forwards to sender. **Blue ticks.**

If the recipient is offline, the message stays in the server-side queue (encrypted) with a TTL (typically 30 days for text, 14 days for media). When the recipient connects, the queue is flushed to their device.

The at-least-once delivery guarantee: if the delivery ACK from the recipient's device is lost (e.g., device crashed right after receiving), the server will re-deliver on reconnect. The client deduplicates using the `MessageID` field — it records which IDs it has already processed in local storage.

```go
func (s *DeliveryService) flushQueue(ctx context.Context, userID string, conn WebSocketConn) error {
    msgs, err := s.queue.Pop(ctx, userID, batchSize)
    if err != nil {
        return fmt.Errorf("popping queue for %s: %w", userID, err)
    }
    for _, msg := range msgs {
        if err := conn.Send(msg); err != nil {
            // Re-enqueue on send failure; client will get it on next connect
            _ = s.queue.Requeue(ctx, userID, msg)
            return fmt.Errorf("sending message %s: %w", msg.MessageID, err)
        }
        // Wait for ACK before deleting from queue
        if err := conn.WaitForAck(ctx, msg.MessageID, ackTimeout); err != nil {
            _ = s.queue.Requeue(ctx, userID, msg)
            return fmt.Errorf("waiting for ack for %s: %w", msg.MessageID, err)
        }
        _ = s.queue.Delete(ctx, userID, msg.MessageID)
    }
    return nil
}
```

**Group Messages**

Group messaging is a fan-out problem. A message to a 500-person group triggers 499 individual deliveries. There are two strategies:

- **Server-side fan-out**: the server takes the message and enqueues it to each member's delivery queue. Simple for the client; heavy for the server at high group sizes.
- **Client-side encryption with multiple recipients**: in the Signal Protocol, group messages are actually encrypted individually for each recipient using pairwise sessions established via the Sender Key protocol. The Sender Key protocol reduces this: after the initial setup, a group member can broadcast to all others using a single encryption key distributed via the group's Sender Key. This scales much better than per-recipient encryption for large groups.

WhatsApp uses Sender Keys for groups. The first time Alice messages a group, she generates a Sender Key and distributes it (encrypted to each member) via the server. Subsequent messages from Alice are encrypted once with her Sender Key and distributed to the group. The server does a single-message fan-out to all members' queues — but the ciphertext is the same object, so storage is not multiplied by group size for the message body itself.

**Presence System**

Presence — online/offline status and "last seen" — is one of the most expensive features in a messaging system. Every connected client potentially cares about the status of dozens or hundreds of contacts.

WhatsApp's presence design:
- When a client connects, it registers its online status with a presence service and subscribes to presence updates for its contacts
- Presence updates are pushed over the WebSocket
- "Last seen" is stored in the user's profile, updated when they disconnect

The scale problem: if a user has 500 contacts and each of those contacts has 500 contacts, a single user coming online can trigger a cascade of presence notifications. WhatsApp throttles presence notifications aggressively, introduces coarse-grained buckets ("last seen recently" vs. precise timestamp), and lets users opt out of sharing last seen altogether. The privacy controls are also engineering controls.

## Scaling Challenges

**Connection management at 2 billion users**

500 million daily active users with persistent WebSocket connections — not all concurrent, but a substantial fraction. Managing connection state (which user is on which server) requires a distributed routing layer. WhatsApp uses Erlang/OTP on the backend (the WhatsApp founders were Erlang engineers), which handles millions of lightweight processes for connection management natively. The BEAM VM's actor model maps naturally to maintaining per-connection state.

**Media storage and client-side encryption**

Media files are encrypted on the client before upload. The server stores only ciphertext blobs in object storage (similar to S3). The media encryption key is embedded in the message sent to the recipient — the server cannot decrypt the media. This means deduplication (not re-storing the same image sent multiple times) is impossible on the server without breaking E2E encryption. WhatsApp accepts this tradeoff.

**Handling billions of ACKs**

Every delivered message generates a delivery ACK, and every read message generates a read receipt. That's potentially 200 billion acknowledgment events per day. These are handled as lightweight messages through the same WebSocket connections, batched where possible, and processed asynchronously — delivery status updates are not on the critical path of message delivery itself.

## Interview Tips

**Clarify the encryption model early.** "End-to-end encrypted" is a common interview requirement, and many candidates nod at it without addressing it. The interviewer wants to hear you explain what it means architecturally: the server stores ciphertext only, key exchange happens client-to-client via a KDS, and the Double Ratchet provides forward secrecy. Even a high-level correct answer here distinguishes strong candidates.

**Be specific about delivery receipts.** The three-state tick system has a clear protocol behind it. Walk through the happy path (sender → server → recipient → ACK → sender notified) and the failure path (recipient offline → queue → flush on reconnect with dedup).

**Separate group message fan-out from individual delivery.** A naive implementation that sends 499 unique encrypted messages for a group message doesn't scale. Mention Sender Keys as the efficient solution.

**Presence is expensive — say so.** Acknowledging the fan-out cost of presence notifications and explaining throttling/coarsening shows you've thought beyond the happy path.

## Key Takeaway

WhatsApp's architecture is a study in making hard trade-offs deliberately. Server-side E2E encryption means the server cannot deduplicate media — accepted. Presence notifications fan out aggressively — throttled and coarsened. At-least-once delivery with client deduplication is simpler than exactly-once delivery guarantees at the server — chosen. Each compromise is in service of the core constraints: strong encryption, reliable delivery, and operating at 2 billion users on a relatively small engineering team. The Signal Protocol is the cryptographic foundation, Erlang/OTP is the concurrency foundation, and a simple per-user message queue is the delivery foundation. Complexity is contained at the edges, not the center.

---

**Previous:** [Lesson 3: Design Google Docs](/post/fundamentals/sd-deep-google-docs/) | **Up next:** [Lesson 5: Design Twitter/X — Tweet fanout, timeline ranking, trending topics at 500M users](/post/fundamentals/sd-deep-twitter/)
