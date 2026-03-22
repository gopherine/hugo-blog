---
title: "Lesson 13: Cryptographic Primitives — Hashing, HMAC, and never rolling your own"
author: "Atharva Pandey"
keywords:
  - cryptographic hashing
  - HMAC
  - SHA256
  - bcrypt
  - JWT
  - cryptography
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 13
date: "2024-10-17T00:00:00.000Z"
---

There is a joke in security circles: every developer thinks they can write their own crypto. The punchline is that everyone who has tried has been wrong. Cryptography is the one area of computer science where being 99% correct is the same as being completely wrong. A subtle timing vulnerability, a nonce reuse, or a hash function with the wrong properties can completely destroy a security guarantee that looks solid on paper.

What I have learned from a few close calls and many post-mortems is this: you do not need to understand the internals of AES or SHA-256 to use cryptography correctly. But you do need to understand which primitive is appropriate for which problem, and what the failure modes are when you use the wrong one.

## How It Works

Cryptographic hash functions take arbitrary input and produce a fixed-size output (digest) with these properties:

- **Deterministic**: same input always produces same output
- **One-way (preimage resistance)**: given the hash, you cannot compute the input
- **Collision resistant**: it is computationally infeasible to find two inputs with the same hash
- **Avalanche effect**: a small change in input completely changes the output

These are different from non-cryptographic hash functions (like FNV or xxHash) which are fast but do not provide cryptographic security.

```go
package main

import (
    "crypto/hmac"
    "crypto/rand"
    "crypto/sha256"
    "encoding/hex"
    "fmt"
    "golang.org/x/crypto/bcrypt"
)

// SHA-256 hash of data — for integrity checking, NOT for passwords
func sha256Hash(data []byte) string {
    h := sha256.Sum256(data)
    return hex.EncodeToString(h[:])
}

// Verify data integrity — did this file arrive without corruption?
func verifyIntegrity(data []byte, expectedHash string) bool {
    actual := sha256Hash(data)
    // Use hmac.Equal for constant-time comparison (prevents timing attacks)
    return hmac.Equal([]byte(actual), []byte(expectedHash))
}
```

**HMAC** (Hash-based Message Authentication Code) adds a secret key to the hash. It proves not just that data was not tampered with (integrity) but that it was produced by someone with the key (authenticity).

```go
// HMAC-SHA256 — for authenticating messages, signing tokens
func hmacSign(message, secret []byte) string {
    mac := hmac.New(sha256.New, secret)
    mac.Write(message)
    return hex.EncodeToString(mac.Sum(nil))
}

func hmacVerify(message, secret []byte, signature string) bool {
    expected := hmacSign(message, secret)
    // CRITICAL: constant-time comparison to prevent timing attacks
    // strings.Compare or == are NOT safe here
    return hmac.Equal([]byte(expected), []byte(signature))
}

// Generate a cryptographically random secret key
func generateKey(length int) ([]byte, error) {
    key := make([]byte, length)
    if _, err := rand.Read(key); err != nil {
        return nil, fmt.Errorf("generating key: %w", err)
    }
    return key, nil
}
```

The constant-time comparison is not optional. If you use `==` or `strings.Compare`, an attacker can measure response times to determine how many characters of a correct signature they have guessed — a **timing attack**.

## When You Need It

The right primitive for each use case:

| Use case | Correct primitive | Common wrong choice |
|---|---|---|
| Password storage | bcrypt / argon2 | SHA-256, MD5 |
| API request signing | HMAC-SHA256 | SHA-256 alone |
| Data integrity check | SHA-256 / SHA-512 | CRC32, MD5 |
| Session tokens | crypto/rand | math/rand |
| Deduplication fingerprints | SHA-256, xxHash | Any hash |
| Webhook verification | HMAC-SHA256 | Comparing plain tokens |

Passwords are the most common source of cryptographic mistakes. SHA-256 is the wrong choice for passwords because it is too fast — an attacker can compute billions of SHA-256 hashes per second with commodity hardware. bcrypt and argon2 are intentionally slow and include salting automatically.

```go
// Password hashing — use bcrypt, not SHA-256
const bcryptCost = 12 // increase with hardware improvements; 10-14 is typical

func hashPassword(plaintext string) (string, error) {
    hash, err := bcrypt.GenerateFromPassword([]byte(plaintext), bcryptCost)
    if err != nil {
        return "", fmt.Errorf("hashing password: %w", err)
    }
    return string(hash), nil
}

func checkPassword(plaintext, hash string) bool {
    err := bcrypt.CompareHashAndPassword([]byte(hash), []byte(plaintext))
    return err == nil
    // bcrypt.CompareHashAndPassword is constant-time
}

// NEVER do this — SHA-256 is reversible with rainbow tables
// and precomputed lookup tables for common passwords
func wrongPasswordHash(pw string) string {
    h := sha256.Sum256([]byte(pw))
    return hex.EncodeToString(h[:])
}
```

## Production Example

Webhook delivery systems need to prove to receivers that a webhook came from the expected sender. GitHub, Stripe, Twilio, and every serious webhook provider use HMAC-SHA256 for this. Here is a production-grade webhook sender and verifier:

```go
// Webhook sender — sign every delivery
type WebhookSender struct {
    secret []byte
    client *http.Client
}

func (ws *WebhookSender) Send(url string, payload []byte) error {
    sig := hmacSign(payload, ws.secret)

    req, err := http.NewRequest("POST", url, bytes.NewReader(payload))
    if err != nil {
        return fmt.Errorf("creating webhook request: %w", err)
    }
    req.Header.Set("Content-Type", "application/json")
    req.Header.Set("X-Signature-SHA256", "sha256="+sig)
    // Include timestamp to prevent replay attacks
    timestamp := strconv.FormatInt(time.Now().Unix(), 10)
    req.Header.Set("X-Timestamp", timestamp)

    resp, err := ws.client.Do(req)
    if err != nil {
        return fmt.Errorf("sending webhook: %w", err)
    }
    defer resp.Body.Close()
    if resp.StatusCode >= 400 {
        return fmt.Errorf("webhook delivery failed: status %d", resp.StatusCode)
    }
    return nil
}

// Webhook receiver — verify before processing
type WebhookReceiver struct {
    secret        []byte
    maxAgeSeconds int64
}

func (wr *WebhookReceiver) Verify(r *http.Request, body []byte) error {
    // 1. Check timestamp to prevent replay attacks
    tsStr := r.Header.Get("X-Timestamp")
    ts, err := strconv.ParseInt(tsStr, 10, 64)
    if err != nil {
        return fmt.Errorf("missing or invalid timestamp")
    }
    age := time.Now().Unix() - ts
    if age > wr.maxAgeSeconds || age < -60 { // allow 60s clock skew
        return fmt.Errorf("webhook too old or from the future: age=%ds", age)
    }

    // 2. Verify signature
    sigHeader := r.Header.Get("X-Signature-SHA256")
    if !strings.HasPrefix(sigHeader, "sha256=") {
        return fmt.Errorf("missing or malformed signature header")
    }
    received := strings.TrimPrefix(sigHeader, "sha256=")
    expected := hmacSign(body, wr.secret)

    if !hmac.Equal([]byte(expected), []byte(received)) {
        return fmt.Errorf("signature verification failed")
    }
    return nil
}
```

The timestamp prevents **replay attacks**: an attacker who captures a valid webhook cannot resend it later, because the timestamp check will reject it after the max age window.

For API request signing (AWS-style), the signature typically covers the request method, path, headers, and body hash:

```go
// Simple API request signing
func signAPIRequest(method, path string, body []byte, secret []byte) string {
    bodyHash := sha256Hash(body)
    timestamp := time.Now().UTC().Format(time.RFC3339)
    // String to sign: deterministic, covers all relevant request fields
    stringToSign := fmt.Sprintf("%s\n%s\n%s\n%s", method, path, timestamp, bodyHash)
    return hmacSign([]byte(stringToSign), secret)
}
```

## The Tradeoffs

**Speed vs. security for passwords.** bcrypt at cost 12 takes ~100–300ms on modern hardware. That is intentional — it makes brute-force attacks expensive. But it also means your authentication endpoint can only handle ~3–10 logins per second per CPU core. Use goroutine pools or offload bcrypt operations to avoid blocking your main request handlers.

**Key rotation.** HMAC is only as secure as the secret key. Rotate keys periodically. When rotating, support both old and new keys during a transition window: try verifying with the new key first, fall back to the old key, reject if neither matches.

**MD5 and SHA-1 are deprecated for security purposes.** They are still fine for non-security uses (checksums, deduplication fingerprints where collisions are not a concern). For any security context — signatures, integrity checks where tampering is possible — use SHA-256 or SHA-512.

**Never implement your own.** This lesson is about understanding primitives, not implementing them. Use Go's `crypto` standard library and audited packages like `golang.org/x/crypto`. The implementation of bcrypt, HMAC, and SHA-256 in Go's standard library has been audited and battle-tested. The one you write based on a paper has not.

## Key Takeaway

Cryptographic primitives are specialized tools with specific properties. SHA-256 is for integrity, not passwords. HMAC is for authentication and signing. bcrypt/argon2 is for passwords. `crypto/rand` is for random secrets. Always use constant-time comparison for secrets. Never implement these from scratch. The standard library has everything you need, and using it correctly is mostly about picking the right primitive for the right problem.

---

← [Lesson 12: Compression Basics](/post/fundamentals/algo-compression/) | [Course Index](/post/fundamentals/) | Next: [Lesson 14: Randomized Algorithms](/post/fundamentals/algo-randomized/) →
