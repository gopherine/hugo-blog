---
title: "Lesson 7: JWT Caveats — JWTs are not sessions"
author: Atharva Pandey
series: "Go Security in Production"
lesson: 7
keywords:
  - Go
  - golang
  - security
tags:
  - Go tutorial
  - golang
  - security
date: '2025-01-18T00:00:00.000Z'
---

JWT has become the default answer to "how should I handle authentication tokens?" in the Go community. I have shipped JWTs in production and I have also shipped systems that would have been much simpler and more secure with server-side sessions. The point of this lesson is not that JWTs are bad — it is that they carry specific security risks that are easy to overlook, and they solve a specific problem (stateless authentication across services) that not every application actually has.

The bugs I have seen most often: tokens signed with the "none" algorithm, long-lived tokens with no revocation mechanism, and tokens with an overly broad audience claim. All of them come from treating JWT as a cookie replacement when it is something quite different.

## The Problem

The most dangerous JWT bug is accepting tokens signed with the "none" algorithm — which some JWT libraries supported (and some still do) for testing:

```go
// WRONG — library that accepts "none" algorithm
// An attacker constructs: eyJhbGciOiJub25lIn0.eyJzdWIiOiJhZG1pbiJ9.
// and it passes validation because "none" means no signature required

import "github.com/dgrijalva/jwt-go" // deprecated, has the "none" vulnerability in old versions

func parseToken(tokenStr string) (*jwt.Claims, error) {
    token, err := jwt.Parse(tokenStr, func(token *jwt.Token) (interface{}, error) {
        return []byte("secret"), nil // this callback is NOT called for "none" alg
    })
    // if "none" alg tokens are accepted, an attacker is now "admin"
    return &token.Claims, err
}
```

A second common mistake is not validating claims after signature verification — specifically `exp` (expiry), `aud` (audience), and `iss` (issuer):

```go
// WRONG — only checks signature, ignores expiry and audience
func validateToken(tokenStr string) (string, error) {
    token, err := jwtlib.ParseWithClaims(tokenStr, &Claims{}, keyFunc)
    if err != nil {
        return "", err
    }
    claims, ok := token.Claims.(*Claims)
    if !ok {
        return "", errors.New("invalid claims")
    }
    // forgot to check claims.ExpiresAt
    // forgot to check claims.Audience
    // forgot to check claims.Issuer
    return claims.Subject, nil
}
```

An expired token that still passes signature verification will be accepted. A token issued for a different service (say, your mobile app's API) will be accepted by your internal admin API.

The third mistake: treating JWT as a session and not planning for revocation:

```go
// WRONG — no revocation, token is valid until expiry (sometimes 30 days)
type Claims struct {
    jwt.RegisteredClaims
    UserID string `json:"user_id"`
    // no jti (JWT ID) field, so you cannot revoke individual tokens
}
```

If a user logs out or their account is suspended, a token with a 30-day expiry continues to be valid. There is nothing you can do about it without a revocation check.

## The Idiomatic Way

Use a well-maintained library with explicit algorithm enforcement. `github.com/golang-jwt/jwt/v5` is the actively maintained successor to the deprecated `dgrijalva/jwt-go`:

```go
// RIGHT — explicit algorithm, full claims validation
import "github.com/golang-jwt/jwt/v5"

type AppClaims struct {
    jwt.RegisteredClaims
    UserID string `json:"user_id"`
    Roles  []string `json:"roles"`
}

func GenerateToken(userID string, roles []string, secret []byte) (string, error) {
    now := time.Now()
    claims := AppClaims{
        RegisteredClaims: jwt.RegisteredClaims{
            Subject:   userID,
            Issuer:    "myapp-auth",
            Audience:  jwt.ClaimStrings{"myapp-api"},
            IssuedAt:  jwt.NewNumericDate(now),
            ExpiresAt: jwt.NewNumericDate(now.Add(15 * time.Minute)), // short expiry
            ID:        uuid.New().String(), // jti for revocation
        },
        UserID: userID,
        Roles:  roles,
    }
    token := jwt.NewWithClaims(jwt.SigningMethodHS256, claims)
    return token.SignedString(secret)
}

func ValidateToken(tokenStr string, secret []byte) (*AppClaims, error) {
    parser := jwt.NewParser(
        jwt.WithValidMethods([]string{"HS256"}), // explicitly reject everything else
        jwt.WithAudience("myapp-api"),
        jwt.WithIssuer("myapp-auth"),
        jwt.WithLeeway(5*time.Second), // allow 5s clock skew
    )

    token, err := parser.ParseWithClaims(tokenStr, &AppClaims{}, func(t *jwt.Token) (interface{}, error) {
        if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
            return nil, fmt.Errorf("unexpected signing method: %v", t.Header["alg"])
        }
        return secret, nil
    })
    if err != nil {
        return nil, fmt.Errorf("token validation: %w", err)
    }

    claims, ok := token.Claims.(*AppClaims)
    if !ok || !token.Valid {
        return nil, errors.New("invalid token claims")
    }
    return claims, nil
}
```

For short-lived tokens (15 minutes) with refresh tokens, implement a token revocation blocklist using Redis or a database. Check the `jti` claim against the blocklist on every request:

```go
// RIGHT — revocation check via jti blocklist
func validateWithRevocation(ctx context.Context, tokenStr string, secret []byte, revoked RevocationStore) (*AppClaims, error) {
    claims, err := ValidateToken(tokenStr, secret)
    if err != nil {
        return nil, err
    }

    isRevoked, err := revoked.IsRevoked(ctx, claims.ID)
    if err != nil {
        return nil, fmt.Errorf("revocation check: %w", err)
    }
    if isRevoked {
        return nil, errors.New("token has been revoked")
    }

    return claims, nil
}
```

## In The Wild

**RS256 vs HS256.** HS256 uses a symmetric secret — the same key signs and verifies. Every service that verifies tokens must have the secret, which means it can also forge tokens. RS256 uses asymmetric keys: the auth service holds the private key and signs; all other services hold only the public key and verify. For a system with multiple services, RS256 is significantly more secure because a compromised downstream service cannot forge tokens.

**Refresh token rotation.** A secure refresh token flow works like this: the access token has a 15-minute lifetime; the refresh token has a 7-day lifetime and is single-use. When you exchange a refresh token for a new access token, invalidate the old refresh token and issue a new one. If the old refresh token is presented again (replay attack), invalidate the entire token family.

**JWT vs opaque session tokens.** If your application is a single monolith that owns all its own state, opaque session tokens stored server-side are often simpler and more secure than JWTs. You can revoke them instantly by deleting a row. The complexity of JWT is worth it when you have multiple services that all need to validate the same token without calling a central auth server.

## The Gotchas

**`exp` precision.** JWT `exp` is a Unix timestamp in seconds. `time.Now().Add(15 * time.Minute).Unix()` is correct. Using milliseconds by mistake will produce expiry times in the year 2554, which means tokens never expire.

**Storing JWTs in `localStorage`.** This is a frontend concern, but if you built the API, you probably designed the auth flow: `localStorage` is accessible to any JavaScript on the page, including XSS payloads. Prefer `HttpOnly` cookies for token storage. `HttpOnly` cookies cannot be read by JavaScript, which eliminates the XSS-to-token-theft path.

**Algorithm confusion attacks.** The `kid` (key ID) header in a JWT is controlled by the attacker. If your `keyFunc` uses `kid` to look up the signing key, an attacker can potentially supply a `kid` that causes your server to load an unexpected key. Validate that the `kid` value is in an explicit allowlist before using it.

**Claims are not encrypted.** JWT payload is base64-encoded, not encrypted. Anyone with the token can decode and read the claims. Do not put sensitive information — SSNs, credit card numbers, passwords — in JWT claims.

## Key Takeaway

JWT is a signed assertion, not a session. Explicitly restrict allowed signing algorithms, validate all standard claims (exp, aud, iss), use short expiry times, include a `jti` for revocation, and never store sensitive data in claims. If your application does not need stateless token verification across multiple services, a server-side session with an opaque token is probably simpler and more secure.

---

**Go Security in Production**

Previous: [Lesson 6: Password Hashing — bcrypt or argon2, nothing else](/post/go/go-sec-password-hashing/)
Next: [Lesson 8: Secure HTTP Defaults — Your production server needs these headers](/post/go/go-sec-http-defaults/)
