---
title: "Lesson 3: Design Google Docs — Real-time collaboration, OT vs CRDT, conflict resolution"
author: "Atharva Pandey"
series: "System Design Deep Dives"
lesson: 3
tags:
  - fundamentals
  - system design
  - interviews
keywords:
  - Google Docs system design
  - operational transformation
  - CRDT conflict-free replicated data types
  - real-time collaboration
  - collaborative editing
date: "2024-07-15T00:00:00.000Z"
---

Google Docs is the problem I recommend to every engineer who thinks they understand distributed systems. The surface looks trivial: multiple users editing a document simultaneously. The depth is staggering. When two users type at the same position in a document at the same millisecond, what does each user see? How do you converge on a consistent state without a central lock? How do you preserve the intention behind each edit, not just the characters?

This problem sits at the intersection of distributed consensus, text data structures, and real-time networking. Working through it thoroughly changed how I think about consistency trade-offs.

## Requirements

**Functional requirements**
- Multiple users can edit the same document concurrently in real time
- All users see each other's changes within ~100ms on a low-latency connection
- Cursor positions and selections of other users are visible
- The document state must be consistent across all clients after any sequence of edits
- Full edit history with undo/redo support
- Offline editing with sync on reconnect

**Non-functional requirements**
- Operational latency: edits applied locally immediately (no round-trip required before local display)
- Conflict resolution: automatic, with no data loss and no user intervention required
- Strong eventual consistency: all clients converge to the same state when all messages are delivered
- Availability: document editing works offline; sync happens when connectivity resumes

**Scale estimates**
- Google Docs has ~1 billion documents
- A live document session might have up to 100 concurrent editors (common case is 2–5)
- Each keystroke generates one operation; a fast typist produces ~10 ops/second
- 100 users × 10 ops/second = 1,000 operations/second per document at peak

## High-Level Design

The core challenge is that every client maintains a local copy of the document and applies edits locally before they're acknowledged by the server. This is called **optimistic local application** — it's what makes the editor feel instantaneous. But optimistic application in a distributed setting creates conflicts that must be resolved.

```
Client A (local edit) → WebSocket → Collaboration Server → Broadcast → Client B
         ↓                               ↓
  Apply locally                   Transform & persist
  immediately                     to document store
```

The server acts as an **authority**: it receives operations from all clients, applies a transformation layer to resolve conflicts, persists them in order, and broadcasts the resolved operations to all other clients. Clients that receive server-resolved operations must reconcile them with any optimistically applied local changes.

## Deep Dive

**Operational Transformation (OT)**

OT was the approach Google Docs originally used (and Wave before it). The idea: rather than sending the final document state, clients send *operations* — "insert character 'a' at position 47" or "delete 3 characters starting at position 12". When operations from two clients conflict, you *transform* one operation relative to the other so that applying both in either order produces the same result.

The canonical example: two users, document is "Hello".

- User A inserts " World" at position 5 → document should become "Hello World"
- User B inserts "!" at position 5 → document should become "Hello! World" (not "Hello !World")

Without transformation: if A's operation is applied first on the server, B's position 5 insertion now needs to account for the 6 characters A inserted. B's operation must be transformed from "insert '!' at 5" to "insert '!' at 11" to produce the correct result.

```go
type Op struct {
    Type     string // "insert" or "delete"
    Position int
    Content  string // for inserts
    Length   int    // for deletes
    ClientID string
    SeqNum   int
}

// Transform b relative to a (a was applied first)
func transform(a, b Op) Op {
    if a.Type == "insert" && b.Type == "insert" {
        if a.Position <= b.Position {
            b.Position += len(a.Content)
        }
    }
    if a.Type == "delete" && b.Type == "insert" {
        if a.Position < b.Position {
            deleted := min(a.Length, b.Position-a.Position)
            b.Position -= deleted
        }
    }
    if a.Type == "insert" && b.Type == "delete" {
        if a.Position <= b.Position {
            b.Position += len(a.Content)
        }
    }
    if a.Type == "delete" && b.Type == "delete" {
        if a.Position < b.Position {
            b.Position -= min(a.Length, b.Position-a.Position)
        }
    }
    return b
}

func min(a, b int) int {
    if a < b {
        return a
    }
    return b
}
```

OT works but has a known problem: ensuring convergence requires that the transformation function satisfy a set of algebraic properties (TP1 and TP2). For plain text with two concurrent operations, TP1 suffices. For three or more concurrent operations, you need TP2, which is significantly harder to prove and implement correctly. Several early collaborative editors had subtle convergence bugs that appeared only with three or more concurrent users.

**Conflict-Free Replicated Data Types (CRDTs)**

CRDTs take a different approach: instead of transforming operations, design the data structure itself so that concurrent updates *cannot* conflict by construction. For text, the dominant CRDT approach is to assign a unique, stable identifier to each character rather than using positional indexing.

In a CRDT text model (like LSEQ or RGA — Replicated Growable Array), each character has a globally unique ID based on the inserting client's ID and a logical clock. "Insert character 'a' with ID (client3, clock47) after character with ID (client1, clock12)" is an operation that remains valid regardless of what other insertions have happened. The position in the final document is determined by the ordering of IDs, not numeric indices.

The tradeoff: CRDTs eliminate the complexity of the transformation function, but they increase the per-character metadata overhead. In a CRDT document, you're storing a character plus its ID plus its predecessor ID for every character. For plain text documents this is manageable. For rich-text documents with formatting spans, it gets more complex.

Modern systems like Notion and Linear use CRDTs (specifically Yjs, which implements a variant of YATA — Yet Another Transformation Approach). Google Docs has partially migrated away from pure OT as well.

**The Server's Role**

Even with CRDTs, the server is not eliminated — it's still the source of truth for conflict resolution ordering and persistence. The server:

1. Receives operations from clients over WebSocket connections
2. Assigns a monotonically increasing server sequence number to each operation
3. Persists the operation log to a durable store (this is the canonical document history)
4. Broadcasts the operation with its server sequence number to all other connected clients

Clients use the server sequence number to detect gaps (missed operations) and request a catchup. If a client goes offline and reconnects, it sends its last-known server sequence number; the server replays all operations since then.

**Cursor and Presence**

Cursor positions are not document state — they're ephemeral presence information. They're handled via a separate lightweight pub/sub channel, not the operation log. When User A moves their cursor, a presence update is broadcast to all other clients in the session. These updates are not persisted; they're only for the live session. If a client disconnects, their cursor disappears.

## Scaling Challenges

**Horizontal scaling of the collaboration server**

WebSocket connections are stateful — a client maintains a persistent connection to a specific server. This means you can't trivially load-balance across servers without routing all clients editing the same document to the same server. The standard approach: consistent hashing on document ID routes all connections for a given document to the same server (or server group). If that server fails, clients reconnect and are routed to a replica that has replayed the operation log.

**Operation log persistence and compaction**

The operation log grows indefinitely. For documents with years of edit history, replaying the entire log on reconnect is impractical. The solution is periodic snapshotting: every N operations, compute the full document state and store it as a snapshot. On reconnect, load the most recent snapshot and replay only the operations since that snapshot.

**Offline editing and three-way merges**

When a client edits offline for an extended period and reconnects, it has a local operation log that diverged from the server log at some point in the past. This is equivalent to a git merge — you have a common ancestor (the state at disconnect), the server's changes since then, and the client's local changes. The client transforms its local operations relative to everything the server received during the offline period. For large divergences with many conflicting edits, this can be computationally expensive.

## Interview Tips

The Google Docs interview question is about demonstrating that you understand the fundamental tension between **optimistic local application** and **eventual consistency**, and that you know the two dominant approaches to resolving it.

**Name OT and CRDTs explicitly.** Don't just say "conflict resolution" — name the paradigms, explain the core idea of each, and articulate the trade-off: OT requires a correct transformation function (hard to get right), CRDTs require unique character IDs (metadata overhead but mathematically guaranteed convergence).

**Explain why you can't use locking.** A global document lock that every user acquires before typing would make collaborative editing feel like Google Forms. The system must allow optimistic local application. This is the premise from which everything else follows.

**Talk about the operation log as the source of truth.** The document isn't stored as a snapshot (or not *only* as a snapshot) — it's stored as a log of operations. The current state is derived from replaying the log (possibly starting from a periodic snapshot). This is the same insight as event sourcing.

**Address presence separately.** Cursor positions are ephemeral and don't need the same durability guarantees as document operations. Showing that you separate concerns here is a sign of good architectural thinking.

## Key Takeaway

Google Docs teaches you that some distributed systems problems require rethinking the data structure, not just the infrastructure. OT transforms operations to resolve conflicts; CRDTs design away conflicts entirely through unique character identifiers. Both require a server that serializes operations, assigns sequence numbers, and acts as the canonical log. The local display feels instant because clients apply operations optimistically before the server confirms them — and the conflict resolution algorithm ensures that optimistic local state and server-reconciled state converge. The hard problems are not the networking or the storage; they're the mathematics of concurrent operation semantics.

---

**Previous:** [Lesson 2: Design Uber](/post/fundamentals/sd-deep-uber/) | **Up next:** [Lesson 4: Design WhatsApp — End-to-end encryption, message delivery guarantees, presence](/post/fundamentals/sd-deep-whatsapp/)
