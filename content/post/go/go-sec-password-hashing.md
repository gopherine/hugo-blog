---
title: "Lesson 6: Password Hashing — bcrypt or argon2, nothing else"
author: Atharva Pandey
series: "Go Security in Production"
lesson: 6
keywords:
  - Go
  - golang
  - security
tags:
  - Go tutorial
  - golang
  - security
date: '2024-12-02T00:00:00.000Z'
---

Password hashing is one of those topics where the correct answer is short and clear, the wrong answers are numerous and subtle, and developers who confidently implement one of the wrong answers often do not know they did anything wrong until a database is leaked and journalists start writing about it. I have sat in a post-mortem where the words "we were using MD5" were spoken in a conference room full of very quiet people. That was not my code, but I understood how it happened — MD5 was the "hash function" the developer knew, and they did not realize it was entirely unsuitable for passwords.

This lesson is about the only two functions you should use for password hashing in Go in 2024 and beyond, and why everything else is the wrong answer.

## The Problem

Using a general-purpose hash function for passwords is the most common mistake:

```go
// WRONG — MD5, SHA-1, SHA-256 are not password hashes
import "crypto/sha256"

func hashPassword(password string) string {
    h := sha256.Sum256([]byte(password))
    return hex.EncodeToString(h[:])
}

func checkPassword(password, stored string) bool {
    return hashPassword(password) == stored
}
```

SHA-256 is fast — that is exactly the problem. A modern GPU can compute billions of SHA-256 hashes per second. An attacker who obtains your database can run a brute-force or dictionary attack and crack short or common passwords in seconds. There is no salt, so identical passwords produce identical hashes, enabling rainbow table attacks.

Adding a salt manually is better but still wrong if the underlying algorithm is fast:

```go
// WRONG — salted SHA-256 is still wrong because SHA-256 is too fast
func hashPasswordWithSalt(password string) (string, string) {
    salt := make([]byte, 16)
    rand.Read(salt)
    saltHex := hex.EncodeToString(salt)

    combined := saltHex + password
    h := sha256.Sum256([]byte(combined))
    return saltHex, hex.EncodeToString(h[:])
}
```

Better — no rainbow tables — but still crackable at GPU speed. Password hashing algorithms are designed to be deliberately slow and memory-intensive, making brute-force attacks computationally expensive even with specialized hardware.

A third failure is storing passwords in a recoverable form — encryption instead of hashing:

```go
// WRONG — encrypted passwords can be decrypted if the key is compromised
func storePassword(password string) (string, error) {
    key := []byte(os.Getenv("ENCRYPTION_KEY"))
    // AES-GCM encryption... but you can decrypt this later
    // If the key leaks, all passwords are exposed
    return encrypt(key, password)
}
```

Passwords should never be recoverable. If a user forgets their password, reset it — never decrypt and show the original.

## The Idiomatic Way

Use `golang.org/x/crypto/bcrypt` for most applications. It is battle-tested, handles salt generation internally, and the cost factor controls computational expense:

```go
// RIGHT — bcrypt with appropriate cost
import "golang.org/x/crypto/bcrypt"

const bcryptCost = 12 // adjust based on your latency budget; 12 is ~250ms on a modern server

func HashPassword(password string) (string, error) {
    hash, err := bcrypt.GenerateFromPassword([]byte(password), bcryptCost)
    if err != nil {
        return "", fmt.Errorf("hash password: %w", err)
    }
    return string(hash), nil
}

func VerifyPassword(password, hash string) error {
    return bcrypt.CompareHashAndPassword([]byte(hash), []byte(password))
    // returns nil on match, bcrypt.ErrMismatchedHashAndPassword on mismatch
}
```

The cost of 12 means 2^12 iterations — about 250ms on a modern server CPU. That is slow enough to make brute-force expensive and fast enough not to noticeably impact login latency.

For new applications where you want the strongest available algorithm, use Argon2id — the winner of the Password Hashing Competition. It is more memory-hard than bcrypt, which makes GPU and ASIC attacks much harder:

```go
// RIGHT — argon2id for highest-security password hashing
import (
    "crypto/rand"
    "crypto/subtle"
    "encoding/base64"
    "fmt"
    "golang.org/x/crypto/argon2"
    "strings"
)

type Argon2Params struct {
    Memory      uint32
    Iterations  uint32
    Parallelism uint8
    SaltLength  uint32
    KeyLength   uint32
}

var DefaultArgon2Params = Argon2Params{
    Memory:      64 * 1024, // 64MB
    Iterations:  3,
    Parallelism: 2,
    SaltLength:  16,
    KeyLength:   32,
}

func HashPasswordArgon2(password string) (string, error) {
    p := DefaultArgon2Params
    salt := make([]byte, p.SaltLength)
    if _, err := rand.Read(salt); err != nil {
        return "", err
    }

    hash := argon2.IDKey([]byte(password), salt, p.Iterations, p.Memory, p.Parallelism, p.KeyLength)

    encoded := fmt.Sprintf("$argon2id$v=%d$m=%d,t=%d,p=%d$%s$%s",
        argon2.Version,
        p.Memory, p.Iterations, p.Parallelism,
        base64.RawStdEncoding.EncodeToString(salt),
        base64.RawStdEncoding.EncodeToString(hash),
    )
    return encoded, nil
}

func VerifyPasswordArgon2(password, encoded string) (bool, error) {
    parts := strings.Split(encoded, "$")
    if len(parts) != 6 {
        return false, errors.New("invalid hash format")
    }

    var p Argon2Params
    _, err := fmt.Sscanf(parts[3], "m=%d,t=%d,p=%d", &p.Memory, &p.Iterations, &p.Parallelism)
    if err != nil {
        return false, fmt.Errorf("parse params: %w", err)
    }

    salt, err := base64.RawStdEncoding.DecodeString(parts[4])
    if err != nil {
        return false, err
    }
    storedHash, err := base64.RawStdEncoding.DecodeString(parts[5])
    if err != nil {
        return false, err
    }

    p.KeyLength = uint32(len(storedHash))
    computedHash := argon2.IDKey([]byte(password), salt, p.Iterations, p.Memory, p.Parallelism, p.KeyLength)

    return subtle.ConstantTimeCompare(storedHash, computedHash) == 1, nil
}
```

## In The Wild

**Cost calibration.** The right bcrypt cost or Argon2 parameters depend on your hardware. The rule of thumb is that password hashing should take 100–500ms on your production hardware. Write a benchmark and calibrate:

```go
func BenchmarkBcrypt(b *testing.B) {
    for i := 0; i < b.N; i++ {
        bcrypt.GenerateFromPassword([]byte("testpassword"), 12)
    }
}
```

If it runs in 50ms, increase the cost. If it runs in 1 second, decrease it. Re-run this benchmark when you change hardware.

**Migrating from legacy hashes.** If you have a legacy system using MD5 or SHA-1, migrate on login: when a user successfully logs in with the old hash, re-hash their password with bcrypt and store the new hash. Mark old-format hashes with a prefix so you know which algorithm was used. Over time, all active users are migrated; inactive users with old hashes can be force-reset.

**Password length limits.** bcrypt silently truncates passwords longer than 72 bytes. If you want to support longer passwords, pre-hash the password with SHA-256 before bcrypt — but be careful to document this behavior in your codebase. Argon2 does not have this limitation.

## The Gotchas

**`bcrypt.ErrMismatchedHashAndPassword` vs error.** `bcrypt.CompareHashAndPassword` returns `bcrypt.ErrMismatchedHashAndPassword` for a wrong password — this is not an error in the operational sense. Only return HTTP 500 for errors that are not this sentinel value. A wrong password is a 401.

**Timing on verification.** Both bcrypt and argon2 verification take the same amount of time regardless of whether the hash matches — they are constant-time at the algorithm level. Do not add any early-return logic that might introduce a timing difference.

**Concurrent hashing load.** Argon2 with 64MB memory and a concurrency factor of 2 means a burst of 100 simultaneous login requests could allocate 12.8GB of memory. Make sure your concurrency model accounts for this. Rate limiting login endpoints is both a security control and a resource protection measure.

## Key Takeaway

There are exactly two acceptable password hashing algorithms in Go: bcrypt (via `golang.org/x/crypto/bcrypt`) and Argon2id (via `golang.org/x/crypto/argon2`). SHA-256 is a hash function, not a password hash — it is orders of magnitude too fast. MD5 and SHA-1 should not appear anywhere near your authentication code. bcrypt is the safe, widely-audited default; Argon2id is the better choice for new systems where you have control over the infrastructure.

---

**Go Security in Production**

Previous: [Lesson 5: Auth Middleware — Authentication is not authorization](/post/go/go-sec-auth-middleware/)
Next: [Lesson 7: JWT Caveats — JWTs are not sessions](/post/go/go-sec-jwt/)
