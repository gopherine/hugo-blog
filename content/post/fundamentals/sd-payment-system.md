---
title: "Lesson 13: Design a Payment System — Idempotency, Reconciliation, Double-Entry"
author: "Atharva Pandey"
series: "System Design from First Principles"
lesson: 13
tags:
  - fundamentals
  - system design
keywords:
  - payment system design
  - idempotency
  - double-entry bookkeeping
  - reconciliation
  - financial transactions
  - distributed systems
  - system design
date: "2024-10-21T00:00:00.000Z"
---

Payment systems have a property that almost no other software has: the cost of a bug isn't a bad user experience — it's a legal liability and a business catastrophe. Charging a customer twice, losing a transfer in a network failure, or crediting the wrong account can result in millions of dollars of loss and destroyed trust. Every other system we've covered tolerates a degree of eventual inconsistency. Payment systems, in most cases, do not. This lesson is about building for that level of correctness.

## The Core Concept

**The fundamental problem: distributed systems are unreliable**

When you call a payment processor (Stripe, Visa, a bank API), the call goes over a network. Networks fail. Timeouts happen. You may not know if the payment succeeded:
- Did the network fail before reaching the processor? (safe to retry — payment never happened)
- Did the network fail after the processor processed it but before returning the response? (dangerous to retry — payment already happened, retry would charge twice)

This uncertainty — "did it happen or not?" — is the core problem in payment systems. The solution is **idempotency**.

**Idempotency keys**

An idempotency key is a unique identifier for an operation that the server uses to de-duplicate requests. If you send the same payment request twice with the same idempotency key, the server processes it once and returns the same result on subsequent calls.

```
POST /payments
Idempotency-Key: a3f4c2d1-8b9e-4f2a-b6c7-d8e9f0a1b2c3
{
  "amount": 5000,
  "currency": "USD",
  "customer_id": "cus_123",
  "description": "Order #456"
}
```

On the server side:

```go
func ProcessPayment(ctx context.Context, req PaymentRequest) (*Payment, error) {
    // Check idempotency store first
    existing, err := db.GetByIdempotencyKey(ctx, req.IdempotencyKey)
    if err == nil {
        // Already processed — return stored result
        return existing, nil
    }

    // Begin transaction
    return db.WithTransaction(ctx, func(tx *sql.Tx) error {
        // Store idempotency key FIRST (with status "processing")
        if err := insertIdempotencyRecord(tx, req.IdempotencyKey); err != nil {
            return err // key already exists from concurrent request — bail out
        }

        // Call external payment processor
        result, err := paymentProcessor.Charge(ctx, req)
        if err != nil {
            // Mark as failed — don't retry the external call
            updateIdempotencyRecord(tx, req.IdempotencyKey, "failed", err)
            return err
        }

        // Record payment and mark idempotency key as completed
        payment := recordPayment(tx, req, result)
        updateIdempotencyRecord(tx, req.IdempotencyKey, "completed", nil)
        return payment
    })
}
```

The idempotency key is stored in the same transaction as the payment record. This makes the operation atomic: either both succeed or neither does.

**Double-entry bookkeeping**

Every financial system that handles real money uses double-entry bookkeeping, developed in 15th-century Florence. Every transaction has equal and opposite entries: for every debit, there's a credit somewhere else. The sum of all accounts is always zero.

```sql
-- A ledger entry table
CREATE TABLE ledger_entries (
    id              BIGSERIAL PRIMARY KEY,
    transaction_id  UUID NOT NULL,
    account_id      BIGINT NOT NULL,
    amount          BIGINT NOT NULL,  -- positive = credit, negative = debit
    currency        VARCHAR(3) NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Transfer $50 from customer wallet to merchant wallet:
INSERT INTO ledger_entries (transaction_id, account_id, amount, currency) VALUES
  ('txn-abc', customer_wallet_id, -5000, 'USD'),  -- debit customer $50
  ('txn-abc', merchant_wallet_id, +5000, 'USD');   -- credit merchant $50
```

The invariant: for any transaction_id, the sum of all entries must be zero. If it's not, something is wrong. This makes bugs detectable: run `SELECT SUM(amount) FROM ledger_entries GROUP BY transaction_id` and any non-zero result is an error.

## How to Design It

**System components**

```
[Client] → [Payment API] → [Idempotency Layer]
                               ↓
                        [Payment Processor]
                        (Stripe, Braintree, internal)
                               ↓
                        [Ledger Service]
                               ↓
                        [Reconciliation Service]
```

**The Payment API** accepts requests, validates them, and routes to the appropriate processor. It enforces idempotency.

**The Ledger Service** maintains the authoritative record of all account balances via double-entry. It never deletes records — only inserts. This creates an immutable audit log.

**The Reconciliation Service** periodically compares your internal ledger against the external processor's records (Stripe's dashboard, bank statements). Discrepancies trigger alerts for human review. Even with idempotency and transactions, reconciliation is necessary — external systems have their own failures.

**Handling external payment processor failures**

When you call Stripe and get a timeout, you don't know if the charge happened. The correct behavior:

1. Store the charge request with status "pending" before calling Stripe
2. Call Stripe
3. On success: update to "completed"
4. On failure/timeout: update to "failed" or "unknown"
5. A background job retries "unknown" charges using the idempotency key — Stripe will return the original result if it processed, or process it if it didn't

This is the "outbox pattern" applied to payments: write the intent to the DB, then process it, then update the status. If the process crashes between steps, the background job picks it up.

**Avoiding double charges**

The combination of idempotency keys + a unique constraint on the idempotency key column in the DB makes double charges impossible at the application layer:

```sql
CREATE TABLE payments (
    id                BIGSERIAL PRIMARY KEY,
    idempotency_key   VARCHAR(128) UNIQUE NOT NULL,  -- prevents duplicates
    status            VARCHAR(20) NOT NULL,
    amount            BIGINT NOT NULL,
    ...
);
```

Even if two requests arrive simultaneously with the same key, the DB unique constraint means only one will succeed. The other will get a unique constraint violation and return the existing payment.

**Currency handling**

Never use floating-point numbers for money. `0.1 + 0.2 = 0.30000000000000004` in floating point. Use integers representing the smallest currency unit: cents for USD, pence for GBP, paise for INR.

```go
// WRONG
amount := 49.99 // floating point — precision errors

// RIGHT
amount := int64(4999) // 4999 cents = $49.99
```

## Real-World Example

Stripe's architecture is the canonical model. Every API call requires an idempotency key (they generate one for you if you don't provide it). Their internal ledger is built on double-entry bookkeeping. Reconciliation runs automatically against bank statements. Their reliability engineering goes deep: they use optimistic locking to prevent concurrent balance modifications, and their charge pipeline is designed to be safely retried at every step.

Square's payment terminal architecture is interesting because the terminal itself must handle network failures: a card swipe captures authorization locally and syncs when connectivity returns. The terminal is a local idempotency store.

Wise (formerly TransferWise) processes cross-currency transfers. Their ledger maintains balances in multiple currencies. Their reconciliation runs against dozens of banking partners worldwide. The complexity of international payments — correspondent banking, SWIFT delays, currency conversion at different rates — makes their reconciliation system one of the most sophisticated in fintech.

## Interview Tips

"What happens if the payment succeeds at Stripe but your server crashes before recording it?" This is the phantom payment problem. The outbox pattern addresses it: you write intent before calling the external processor, and a reconciliation job catches discrepancies. In practice, Stripe's idempotency keys let you safely re-query the status.

"How do you handle refunds?" A refund is a reverse ledger entry. Debit the merchant, credit the customer. But if the charge was partially refunded, the ledger accurately reflects the net: original charge - refund = current balance.

"How do you prevent a user from sending money they don't have?" Before any debit, check the current balance with a row-level lock (`SELECT FOR UPDATE`), verify it's sufficient, then insert the ledger entries — all within one transaction. The lock prevents concurrent overdrafts.

"How do you handle currency conversion?" Fix the exchange rate at the time of the transaction and store it alongside the ledger entry. Never recompute historical transactions at current rates. This is both correct and what regulators require.

## Key Takeaway

Payment systems demand correctness that other systems can afford to relax. Idempotency keys make payment operations safely retryable under network uncertainty. Double-entry bookkeeping creates an immutable audit trail where errors are detectable by invariant violation. The outbox pattern decouples intent recording from external processor calls, ensuring no payment is lost in a crash. Reconciliation against external systems catches discrepancies that application-layer correctness can't prevent. Integer arithmetic for currency amounts eliminates floating-point precision bugs. These aren't clever hacks — they're techniques developed over decades of building financial systems that users trust with their money.

---

**Previous:** [Lesson 12: Design a Search Engine](/post/fundamentals/sd-search-engine/)
**Next:** [Lesson 14: Designing for Failure — Circuit Breakers, Bulkheads, Chaos](/post/fundamentals/sd-designing-failure/)
