---
title: "Lesson 6: Idempotency in APIs — Every POST should be safe to retry"
author: Atharva Pandey
series: "Go API and Service Design"
lesson: 6
keywords: [Go, golang, API, backend, idempotency, retry, idempotency key, distributed systems]
tags: [Go tutorial, golang, backend]
date: '2024-12-08T00:00:00.000Z'
---

A client sends a payment request. The network times out. The client does not know if the payment was processed. Should it retry? If it retries and the payment already went through, the customer gets charged twice. If it does not retry and the payment failed, the order never ships. Without idempotency, there is no safe answer to this question.

This is not a theoretical concern. It happens every time a mobile app retries a failed request, every time a message queue delivers a message at least once, every time a load balancer retries on a 503.

## The Problem

HTTP GET and DELETE are naturally idempotent — calling them multiple times has the same effect as calling them once. HTTP POST is not. A `POST /payments` endpoint creates a new payment every time it is called with the same body. That is the semantic of POST, and it is correct — until your network becomes unreliable or your message queue delivers duplicates.

The naive workaround is to check for duplicates in business logic: "does a payment with this amount from this user already exist?" But that is a business rule check, not an idempotency guarantee. Two requests with the same content but different intent (a user legitimately paying twice) should both succeed. Two requests that are literally the same operation retried should succeed once.

## The Idiomatic Way

The industry-standard solution is an idempotency key. The client generates a unique key (typically a UUID) for each logical operation and sends it in a request header. The server stores the key and its result. If the same key arrives again, the server returns the stored result instead of executing the operation again.

Here is a complete implementation using a Redis-backed idempotency store:

```go
package idempotency

import (
    "context"
    "crypto/sha256"
    "encoding/json"
    "errors"
    "fmt"
    "net/http"
    "time"

    "github.com/redis/go-redis/v9"
)

const (
    KeyHeader = "Idempotency-Key"
    TTL       = 24 * time.Hour
)

type StoredResponse struct {
    StatusCode int               `json:"status_code"`
    Headers    map[string]string `json:"headers"`
    Body       []byte            `json:"body"`
}

type Store interface {
    Get(ctx context.Context, key string) (*StoredResponse, error)
    Set(ctx context.Context, key string, resp *StoredResponse, ttl time.Duration) error
    Lock(ctx context.Context, key string, ttl time.Duration) (bool, error)
    Unlock(ctx context.Context, key string) error
}

// RedisStore implements Store using Redis
type RedisStore struct {
    client *redis.Client
}

func (s *RedisStore) Get(ctx context.Context, key string) (*StoredResponse, error) {
    data, err := s.client.Get(ctx, "idem:resp:"+key).Bytes()
    if errors.Is(err, redis.Nil) {
        return nil, nil
    }
    if err != nil {
        return nil, err
    }
    var resp StoredResponse
    if err := json.Unmarshal(data, &resp); err != nil {
        return nil, err
    }
    return &resp, nil
}

func (s *RedisStore) Set(ctx context.Context, key string, resp *StoredResponse, ttl time.Duration) error {
    data, err := json.Marshal(resp)
    if err != nil {
        return err
    }
    return s.client.Set(ctx, "idem:resp:"+key, data, ttl).Err()
}

// Lock acquires a distributed lock to prevent concurrent duplicate processing
func (s *RedisStore) Lock(ctx context.Context, key string, ttl time.Duration) (bool, error) {
    ok, err := s.client.SetNX(ctx, "idem:lock:"+key, "1", ttl).Result()
    return ok, err
}

func (s *RedisStore) Unlock(ctx context.Context, key string) error {
    return s.client.Del(ctx, "idem:lock:"+key).Err()
}
```

Now the middleware that wraps any non-idempotent endpoint:

```go
// captureWriter records the response so it can be stored for replay
type captureWriter struct {
    http.ResponseWriter
    statusCode int
    body       []byte
    headers    map[string]string
}

func (cw *captureWriter) WriteHeader(code int) {
    cw.statusCode = code
    cw.ResponseWriter.WriteHeader(code)
}

func (cw *captureWriter) Write(b []byte) (int, error) {
    cw.body = append(cw.body, b...)
    return cw.ResponseWriter.Write(b)
}

// Idempotent wraps a handler with idempotency key enforcement
func Idempotent(store Store) func(http.Handler) http.Handler {
    return func(next http.Handler) http.Handler {
        return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
            key := r.Header.Get(KeyHeader)
            if key == "" {
                http.Error(w, `{"error":"Idempotency-Key header is required"}`, http.StatusBadRequest)
                return
            }

            // Validate key format (UUID)
            if len(key) > 128 {
                http.Error(w, `{"error":"Idempotency-Key too long"}`, http.StatusBadRequest)
                return
            }

            ctx := r.Context()

            // Check for existing stored response
            stored, err := store.Get(ctx, key)
            if err == nil && stored != nil {
                // Replay the stored response
                for k, v := range stored.Headers {
                    w.Header().Set(k, v)
                }
                w.Header().Set("Idempotent-Replayed", "true")
                w.WriteHeader(stored.StatusCode)
                w.Write(stored.Body)
                return
            }

            // Acquire lock to prevent concurrent execution with same key
            locked, err := store.Lock(ctx, key, 30*time.Second)
            if err != nil || !locked {
                http.Error(w, `{"error":"request in progress"}`, http.StatusConflict)
                return
            }
            defer store.Unlock(ctx, key)

            // Double-check after acquiring lock
            stored, _ = store.Get(ctx, key)
            if stored != nil {
                for k, v := range stored.Headers {
                    w.Header().Set(k, v)
                }
                w.WriteHeader(stored.StatusCode)
                w.Write(stored.Body)
                return
            }

            // Execute the handler with a capturing writer
            cw := &captureWriter{
                ResponseWriter: w,
                statusCode:     http.StatusOK,
                headers:        map[string]string{},
            }
            next.ServeHTTP(cw, r)

            // Only cache successful responses — do not cache 5xx errors
            if cw.statusCode < 500 {
                _ = store.Set(ctx, key, &StoredResponse{
                    StatusCode: cw.statusCode,
                    Body:       cw.body,
                    Headers:    map[string]string{"Content-Type": w.Header().Get("Content-Type")},
                }, TTL)
            }
        })
    }
}
```

## In The Wild

At the handler level, usage is clean:

```go
func (s *Server) routes() {
    idem := idempotency.Idempotent(s.idempStore)
    s.mux.Handle("POST /api/v1/payments", idem(http.HandlerFunc(s.handleCreatePayment)))
    s.mux.Handle("POST /api/v1/orders", idem(http.HandlerFunc(s.handleCreateOrder)))
}
```

Clients send requests with a header:

```
POST /api/v1/payments
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000
Content-Type: application/json

{"amount": 5000, "currency": "USD", "recipient_id": "user_123"}
```

The first call processes the payment and stores the response. Any subsequent call with the same key — whether from a timeout retry, a network retry, or a duplicate message — returns the identical stored response with an `Idempotent-Replayed: true` header so clients can detect the replay if needed.

## The Gotchas

**Do not cache 5xx responses.** If your handler panics or your database is unavailable, a `503` is transient. Storing it means the client's retry (which might succeed once the database recovers) will forever receive the cached error. Only cache responses with status codes below 500.

**The lock window must exceed your maximum request duration.** If processing a payment takes up to 10 seconds, your lock TTL must be longer than 10 seconds. If the lock expires while the handler is still running, a second concurrent request will acquire the lock, execute the handler again, and you will have a double execution despite the idempotency key.

**Key scope matters.** An idempotency key should be scoped to a user and an operation type, not just a UUID. Without scoping, a key generated by one user could theoretically be replayed as a different user's operation if you do not validate the request context against the stored response. Store the requesting user's ID alongside the response and verify it on replay.

**Idempotency is not deduplication.** A deduplication system tries to detect and reject semantically identical requests. Idempotency guarantees that retrying an operation with the same explicit key is safe. They solve different problems. Use idempotency keys for retry safety; use business logic for preventing duplicate data.

## Key Takeaway

Any endpoint that creates state — payments, orders, notifications, subscriptions — should support idempotency keys. The pattern is not complicated: store the response against the key, replay it on duplicate key, acquire a lock to prevent concurrent duplicate execution. The client generates the key; the server owns the guarantee. Once you add this to your critical write endpoints, you can safely retry from clients, message queues, and orchestration systems without fear of duplicate effects.

---

**Series: Go API and Service Design**

[← Lesson 5: Pagination Done Right](/post/go/go-api-pagination) | [Lesson 7: Timeouts and Retries →](/post/go/go-api-timeouts-retries)
