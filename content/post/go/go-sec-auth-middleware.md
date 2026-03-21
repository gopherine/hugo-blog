---
title: "Lesson 5: Auth Middleware — Authentication is not authorization"
author: Atharva Pandey
series: "Go Security in Production"
lesson: 5
keywords:
  - Go
  - golang
  - security
tags:
  - Go tutorial
  - golang
  - security
date: '2024-10-22T00:00:00.000Z'
---

A few years ago I audited a Go API where every endpoint was protected by an authentication middleware. The middleware checked for a valid JWT, extracted the user ID, and set it in the request context. The developer was proud of it — every route was secured. The problem was that the product had a concept of "organizations" — users belonged to organizations — and the API let you fetch any organization's data as long as you were authenticated. The authentication was solid. The authorization was completely absent.

This lesson is about the architectural separation between the two, and why collapsing them into a single middleware is one of the most common security mistakes in Go web applications.

## The Problem

The classic conflation: a single middleware that does both authentication (who are you?) and authorization (what are you allowed to do?):

```go
// WRONG — mixing authentication and authorization into one middleware
func requireAuth(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        token := r.Header.Get("Authorization")
        if token == "" {
            http.Error(w, "unauthorized", http.StatusUnauthorized)
            return
        }

        userID, err := validateToken(token)
        if err != nil {
            http.Error(w, "unauthorized", http.StatusUnauthorized)
            return
        }

        // "authorization" check — but it only checks if the user exists, not what they can do
        if !userExists(userID) {
            http.Error(w, "forbidden", http.StatusForbidden)
            return
        }

        ctx := context.WithValue(r.Context(), "user_id", userID)
        next.ServeHTTP(w, r.WithContext(ctx))
    })
}
```

This middleware authenticates correctly. But when applied to `GET /orgs/{orgID}/members`, it does nothing to check whether `userID` is a member of `orgID`. Any authenticated user can read any organization's member list.

A second problem is reading the user ID from context with an untyped string key:

```go
// WRONG — string key in context causes type collisions
func getOrganizationMembers(w http.ResponseWriter, r *http.Request) {
    userID := r.Context().Value("user_id").(string) // type assertion can panic
    // ...
}
```

String keys in context are a collision hazard — any other middleware that sets `"user_id"` in context will silently overwrite yours. And the type assertion panics if the value is absent or the wrong type.

## The Idiomatic Way

Separate authentication (middleware, runs on every request) from authorization (per-handler or per-route logic, runs on specific operations):

```go
// RIGHT — authentication middleware: only identifies the caller
type contextKey string

const contextKeyUserID contextKey = "userID"

type AuthenticatedUser struct {
    ID    string
    Email string
    Roles []string
}

func AuthMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        token := extractBearerToken(r)
        if token == "" {
            http.Error(w, "missing token", http.StatusUnauthorized)
            return
        }

        user, err := verifyAndDecodeToken(token)
        if err != nil {
            http.Error(w, "invalid token", http.StatusUnauthorized)
            return
        }

        ctx := context.WithValue(r.Context(), contextKeyUserID, user)
        next.ServeHTTP(w, r.WithContext(ctx))
    })
}

func UserFromContext(ctx context.Context) (AuthenticatedUser, bool) {
    user, ok := ctx.Value(contextKeyUserID).(AuthenticatedUser)
    return user, ok
}
```

Authorization happens in the handler or a dedicated authorization layer — not in the authentication middleware:

```go
// RIGHT — authorization check in handler, scoped to the resource
func handleGetOrgMembers(w http.ResponseWriter, r *http.Request) {
    orgID := chi.URLParam(r, "orgID") // or mux.Vars(r)["orgID"]

    user, ok := UserFromContext(r.Context())
    if !ok {
        http.Error(w, "unauthenticated", http.StatusUnauthorized)
        return
    }

    // Authorization: is this user a member of the requested org?
    member, err := orgStore.IsMember(r.Context(), orgID, user.ID)
    if err != nil {
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }
    if !member {
        http.Error(w, "forbidden", http.StatusForbidden)
        return
    }

    members, err := orgStore.ListMembers(r.Context(), orgID)
    if err != nil {
        http.Error(w, "internal error", http.StatusInternalServerError)
        return
    }

    json.NewEncoder(w).Encode(members)
}
```

For role-based access control, build an authorization helper that can be reused across handlers without coupling to HTTP:

```go
// RIGHT — reusable authorization helper decoupled from HTTP
type Authorizer struct {
    store OrgStore
}

func (a *Authorizer) RequireMembership(ctx context.Context, orgID, userID string) error {
    ok, err := a.store.IsMember(ctx, orgID, userID)
    if err != nil {
        return fmt.Errorf("authorization check: %w", err)
    }
    if !ok {
        return ErrForbidden
    }
    return nil
}

func (a *Authorizer) RequireRole(ctx context.Context, orgID, userID, role string) error {
    userRole, err := a.store.GetMemberRole(ctx, orgID, userID)
    if err != nil {
        return fmt.Errorf("authorization check: %w", err)
    }
    if userRole != role {
        return ErrForbidden
    }
    return nil
}
```

## In The Wild

**IDOR — Insecure Direct Object Reference.** The failure to authorize resource access by object ID is so common it has a name. Whenever your route takes an `{id}` parameter and uses it to look up a record, that lookup must include a ownership or membership check. A `userID` filter in your database query is one of the most reliable ways to enforce this:

```go
// RIGHT — ownership enforced at the query level
const query = `SELECT * FROM documents WHERE id = $1 AND owner_id = $2`
row := db.QueryRow(ctx, query, documentID, authenticatedUser.ID)
```

If the document belongs to a different user, the query returns no rows. You do not need a separate authorization call.

**Middleware order matters.** The authentication middleware must run before any handler that requires the user identity. When registering routes, be explicit:

```go
r.Group(func(r chi.Router) {
    r.Use(AuthMiddleware)
    r.Get("/orgs/{orgID}/members", handleGetOrgMembers)
    r.Post("/orgs/{orgID}/invite", handleInviteMember)
})
// Public routes — outside the group, no auth middleware
r.Get("/healthz", handleHealthz)
```

**Admin routes.** Admin endpoints that bypass normal authorization checks are a common source of privilege escalation bugs. Treat admin authentication as a separate concern: a separate token, a separate JWT claim, or a separate authentication flow. Do not add a single `isAdmin` field to the user struct and check it in handlers — that field will eventually be set wrong.

## The Gotchas

**401 vs 403.** Return 401 when the request is not authenticated (no token, invalid token, expired token). Return 403 when the request is authenticated but not authorized (valid user, wrong permissions). Getting these backwards is a minor information leak: a 403 on an unauthenticated request tells attackers that the resource exists.

**Timing attacks on token comparison.** When comparing tokens, tokens that differ in length return immediately — which leaks information about expected token length. Use `subtle.ConstantTimeCompare` for any token or secret comparison:

```go
import "crypto/subtle"

func tokensEqual(a, b string) bool {
    return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}
```

**Caching authorization decisions.** If your authorization checks hit the database on every request, they can add significant latency at scale. A short-lived in-process cache (or Redis) for membership lookups is reasonable, but the cache must be invalidated when membership changes — or have a TTL short enough that stale data is tolerable.

## Key Takeaway

Authentication answers "who are you?" Authorization answers "what are you allowed to do?" These are different questions, enforced in different places. A middleware that authenticates every request is correct and necessary. Authorization must happen per-resource, per-operation, with an explicit check that the authenticated identity is allowed to access the specific object being requested. Skipping that check is IDOR, and it is one of the most frequently exploited vulnerability classes in web applications.

---

**Go Security in Production**

Previous: [Lesson 4: SSRF and Injection — The URL your user gave you might be localhost](/post/go/go-sec-ssrf-injection/)
Next: [Lesson 6: Password Hashing — bcrypt or argon2, nothing else](/post/go/go-sec-password-hashing/)
