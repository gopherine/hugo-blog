---
title: "Lesson 23: Distributed vs Local Concurrency — Channels don''t cross process boundaries"
author: Atharva Pandey
series: "Go Concurrency Masterclass"
lesson: 23
keywords:
  - Go
  - golang
  - concurrency
  - distributed systems
  - message queues
  - idempotent consumers
  - two generals problem
  - channels
tags:
  - Go tutorial
  - golang
  - concurrency
date: '2025-12-22T00:00:00.000Z'
---

There's a particular kind of confidence that comes after you've spent a few months writing Go. You've learned channels, you understand the happens-before guarantees, you've built a worker pool that hums along beautifully. Then someone says "we need to scale this across multiple pods" and you think: easy, I'll just make the channels bigger. That thought has caused more production incidents than I care to remember — including one where we lost about 3,000 job records because a pod restarted mid-processing and nobody had thought about what "at-least-once delivery" actually means.

Local concurrency and distributed concurrency look similar on the surface. Both are about coordinating work between concurrent actors. But the failure modes are completely different, and the tools that work for one actively mislead you about the other.

## The Problem

Here's what happens when you naively try to scale a channel-based worker pool:

```go
// WRONG — channel-based worker pool that "scales" across pods
// This pattern works fine in a single process, breaks completely across machines

var jobQueue = make(chan Job, 1000)

// Pod 1 produces
func producer() {
    for {
        job := fetchNextJobFromDB()
        jobQueue <- job // sends into the channel
    }
}

// Pod 2 "consumes" — but this is a DIFFERENT channel on a different machine
func consumer() {
    for job := range jobQueue {
        process(job)
    }
}
```

This doesn't work at all across pods. `jobQueue` in Pod 1 and `jobQueue` in Pod 2 are entirely separate data structures in separate memory spaces. There's no shared memory between processes on different machines. The channel in Pod 1 is not connected to the channel in Pod 2. They might as well be in different universes.

But here's the more subtle version — the one that actually makes it into production:

```go
// WRONG — goroutine-based fan-out that silently loses work on crash
func startWorkers(ctx context.Context, db *sql.DB) {
    jobs := make(chan Job, 500)

    // In-memory queue — if this pod crashes, these 500 jobs are gone
    go func() {
        rows, _ := db.QueryContext(ctx, "SELECT id FROM jobs WHERE status='pending'")
        for rows.Next() {
            var j Job
            rows.Scan(&j.ID)
            jobs <- j // loaded into memory — now vulnerable
        }
        close(jobs)
    }()

    for i := 0; i < 10; i++ {
        go func() {
            for job := range jobs {
                // If we crash here, job is lost — no one knows it was in-flight
                processJob(job)
            }
        }()
    }
}
```

This version runs fine on a single machine. The moment you add a second replica — or the moment your pod crashes mid-flight — you lose work. The jobs were dequeued from the database and loaded into an in-memory channel. The database thinks they're being processed. Nobody's watching the in-memory channel. The pod restarts, the channel disappears, and those jobs are silently gone.

## The Idiomatic Way

The right model for distributed work coordination is a message queue with acknowledgement semantics. The principle is: **don't dequeue work until you've done it**.

```go
// RIGHT — polling-based job processing with explicit status transitions
func runWorker(ctx context.Context, db *sql.DB) error {
    for {
        select {
        case <-ctx.Done():
            return ctx.Err()
        default:
        }

        job, err := claimNextJob(ctx, db)
        if errors.Is(err, sql.ErrNoRows) {
            time.Sleep(500 * time.Millisecond)
            continue
        }
        if err != nil {
            return fmt.Errorf("claiming job: %w", err)
        }

        if err := processJob(ctx, job); err != nil {
            // Put it back — or increment retry count
            markJobFailed(ctx, db, job.ID, err)
            continue
        }

        markJobDone(ctx, db, job.ID)
    }
}

// claimNextJob atomically claims ownership — safe for concurrent workers
func claimNextJob(ctx context.Context, db *sql.DB) (Job, error) {
    var job Job
    err := db.QueryRowContext(ctx, `
        UPDATE jobs
        SET status = 'processing',
            claimed_at = NOW(),
            worker_id = $1
        WHERE id = (
            SELECT id FROM jobs
            WHERE status = 'pending'
              AND (claimed_at IS NULL OR claimed_at < NOW() - INTERVAL '5 minutes')
            ORDER BY created_at
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, payload
    `, workerID).Scan(&job.ID, &job.Payload)
    return job, err
}
```

`FOR UPDATE SKIP LOCKED` is the Postgres idiom that lets multiple workers compete for jobs without blocking each other. Each worker atomically claims one job and gets a row lock. If a worker crashes, the `claimed_at < NOW() - 5 minutes` condition will make the job visible again to other workers after a timeout. The database is now the source of truth about what's in-flight.

This is the pattern that works across any number of replicas. No shared memory needed. No channels crossing process boundaries.

## In The Wild

The harder problem in distributed systems is idempotency — what happens when your job runs twice. Networks fail. Processes restart. Message queues deliver at-least-once. Your job processor *will* run the same job more than once. The question is whether that causes problems.

```go
// RIGHT — idempotent job processor using upsert semantics
func processPaymentJob(ctx context.Context, db *sql.DB, job PaymentJob) error {
    // Idempotency key: the job ID itself
    // If we've already processed this exact job, the upsert is a no-op
    _, err := db.ExecContext(ctx, `
        INSERT INTO payments (idempotency_key, user_id, amount, status)
        VALUES ($1, $2, $3, 'completed')
        ON CONFLICT (idempotency_key) DO NOTHING
    `, job.ID, job.UserID, job.Amount)
    if err != nil {
        return fmt.Errorf("recording payment: %w", err)
    }

    // Notify downstream — also idempotent (duplicate notification is harmless)
    return notifyUser(ctx, job.UserID, job.Amount)
}
```

The Two Generals Problem is why this matters. In distributed systems, there's no reliable way to know if a message was received *and acted upon*. You can send a message. The other side can receive it and process it. But the acknowledgement might get lost. So from your perspective, the message might have failed. You retry. Now it runs twice. An idempotent receiver handles this correctly. A non-idempotent receiver charges the user twice.

The practical rule: **every operation that crosses a process boundary should be idempotent**. Assign an idempotency key before you send. On receipt, check if you've seen that key before. If yes, return success without doing work. Database `ON CONFLICT DO NOTHING` (or `DO UPDATE`) is your friend.

## The Gotchas

**Channels between goroutines within one process are fine.** Don't throw out everything you know about channels. They're still the right tool for coordinating goroutines within a single binary. The issue arises when you try to use in-process channels as a substitute for a real queue when work needs to survive process restarts or be distributed across machines.

**At-least-once vs exactly-once.** Exactly-once delivery doesn't exist in distributed systems without coordination — and that coordination is expensive. Accept that you'll get at-least-once delivery, and make your consumers idempotent. This is a mindset shift: instead of preventing duplicates at the delivery layer, you tolerate duplicates and make processing them safe.

**In-memory queues between services are invisible.** If you have a goroutine loading items from a database and putting them into an in-memory channel, and your process crashes, those items are gone — and your database might think they're "processing." Always prefer leaving work visible in its source-of-truth storage until it's actually complete.

**Poll intervals and thundering herds.** If you have 20 workers all polling every 100ms and there are no jobs, that's 200 queries per second doing nothing. Use exponential backoff with jitter on idle polls, or better yet, use `LISTEN/NOTIFY` in Postgres to wake workers when new jobs arrive instead of constant polling.

**Distributed locks are not process-local mutexes.** `sync.Mutex` is fast and reliable because it's in-process memory with strict happens-before guarantees. Distributed locks (Redis SETNX, Zookeeper, etcd) have failure modes that `sync.Mutex` doesn't — the lock holder can crash while holding the lock, the lock server can become unavailable, network partitions can make you unsure if you hold the lock. Use distributed locks sparingly, prefer the DB-level `FOR UPDATE SKIP LOCKED` pattern when you can.

## Key Takeaway

Channels are a process-local coordination primitive. The moment work needs to cross process boundaries — survive pod restarts, be processed by multiple replicas — you need a durable queue with acknowledgement semantics. The simplest version of that is a `jobs` table in your database with a `FOR UPDATE SKIP LOCKED` claim pattern. Accept at-least-once delivery as the baseline, and make your processors idempotent. The Two Generals Problem means you can never be certain a remote operation happened exactly once — but with idempotency keys and upsert semantics, running it twice is safe.

Local concurrency is about speed. Distributed concurrency is about correctness in the face of failure. They're different problems with different tools — and confusing them is how you lose data at 2 AM.

---

← [Lesson 22: Rate Limiting and Load Shedding](/post/go/go-concurrency-rate-limiting) | [Lesson 24: Testing Concurrent Code →](/post/go/go-concurrency-testing)
