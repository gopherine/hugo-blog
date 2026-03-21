---
title: "Lesson 2: Giant God Structs — If your struct has 30 fields, it has 30 problems"
author: Atharva Pandey
series: "Go Anti-Patterns & Code Smells"
lesson: 2
keywords:
  - Go
  - golang
  - anti-patterns
  - code quality
tags:
  - Go tutorial
  - golang
  - code quality
date: '2024-07-25T00:00:00.000Z'
---

I inherited a Go service where the primary domain object was a struct with 47 fields. Some were database columns, some were computed from other fields, some were HTTP response projections, some were used only during a specific workflow stage, and a handful were there because someone once needed a flag and adding a field to the God struct was the path of least resistance. The struct was everywhere — passed between functions, serialized to JSON, written to the database, and used as a GraphQL response type. Every change to it rippled through the entire codebase.

God structs in Go are a particular kind of technical debt because Go's lack of inheritance makes them worse than they would be in other languages. There is no subtyping to give you partial views. There is no lazy loading to defer field population. What you have is one flat bag of fields that everyone uses for everything, and the fields that are "not applicable" in a given context are just left zero-valued or nil.

## The Problem

The God struct: one struct that tries to represent an entity in every context it could appear in:

```go
// WRONG — one struct used for database model, API request, API response, and domain logic
type User struct {
    // Identity
    ID        string    `json:"id"        db:"id"`
    CreatedAt time.Time `json:"created_at" db:"created_at"`
    UpdatedAt time.Time `json:"updated_at" db:"updated_at"`

    // Profile
    Name     string `json:"name"     db:"name"`
    Email    string `json:"email"    db:"email"`
    Bio      string `json:"bio"      db:"bio"`
    AvatarURL string `json:"avatar_url" db:"avatar_url"`

    // Auth — but only relevant in login flow
    PasswordHash string `json:"-"         db:"password_hash"`
    LastLoginAt  *time.Time `json:"last_login_at" db:"last_login_at"`
    FailedAttempts int `json:"-"         db:"failed_attempts"`

    // Subscription — but nil for free users
    StripeCustomerID *string `json:"-" db:"stripe_customer_id"`
    PlanID           *string `json:"plan_id" db:"plan_id"`
    PlanExpiresAt    *time.Time `json:"plan_expires_at" db:"plan_expires_at"`

    // Organization membership — but only relevant in multi-tenant context
    OrgID   *string `json:"org_id"   db:"org_id"`
    OrgRole *string `json:"org_role" db:"org_role"`

    // API response only — not in database
    IsOnline      bool   `json:"is_online"      db:"-"`
    UnreadCount   int    `json:"unread_count"   db:"-"`
    DisplayName   string `json:"display_name"   db:"-"` // computed from Name

    // Admin fields — should never be in a user-facing response
    InternalNotes string `json:"-" db:"internal_notes"`
    IsBanned      bool   `json:"is_banned" db:"is_banned"`
    BannedReason  string `json:"-"         db:"banned_reason"`
}
```

Forty-seven fields later and this struct is simultaneously too much and too little in any given context. An API response includes `PasswordHash` fields marked `json:"-"` that should never, under any circumstances, be serializable. A database insert populates `IsOnline` which has `db:"-"` and is just noise. Creating a new user requires populating optional fields with zero values or nils that mean nothing during registration.

## The Idiomatic Way

Separate your concerns into distinct types. At minimum: a database model, a domain entity, and one or more API representations:

```go
// RIGHT — separate types for separate concerns

// database/user.go — database model, maps directly to schema
type UserRow struct {
    ID             string     `db:"id"`
    CreatedAt      time.Time  `db:"created_at"`
    UpdatedAt      time.Time  `db:"updated_at"`
    Name           string     `db:"name"`
    Email          string     `db:"email"`
    PasswordHash   string     `db:"password_hash"`
    IsBanned       bool       `db:"is_banned"`
    StripeCustomerID *string  `db:"stripe_customer_id"`
}

// domain/user.go — domain entity with behavior
type User struct {
    ID        string
    Name      string
    Email     string
    IsBanned  bool
    CreatedAt time.Time
}

func (u *User) CanLogin() bool {
    return !u.IsBanned
}

// api/user.go — API response projection
type UserResponse struct {
    ID          string `json:"id"`
    Name        string `json:"name"`
    DisplayName string `json:"display_name"`
    CreatedAt   time.Time `json:"created_at"`
}

func UserResponseFrom(u *domain.User) UserResponse {
    return UserResponse{
        ID:          u.ID,
        Name:        u.Name,
        DisplayName: strings.Split(u.Name, " ")[0], // computed here, not stored
        CreatedAt:   u.CreatedAt,
    }
}
```

For the subscription concern, introduce a separate aggregate rather than embedding everything in `User`:

```go
// RIGHT — subscription is a separate domain concept
type Subscription struct {
    UserID       string
    PlanID       string
    ExpiresAt    time.Time
    StripeID     string
}

func (s *Subscription) IsActive() bool {
    return time.Now().Before(s.ExpiresAt)
}

// Use composition when you need both
type UserWithSubscription struct {
    User         *domain.User
    Subscription *domain.Subscription // nil if no subscription
}
```

For API requests vs responses — registration vs profile view — use distinct types that only carry the fields relevant to the operation:

```go
// RIGHT — separate types for distinct operations
type RegisterUserRequest struct {
    Name     string `json:"name"     validate:"required,min=1,max=100"`
    Email    string `json:"email"    validate:"required,email"`
    Password string `json:"password" validate:"required,min=8"`
}

type UpdateProfileRequest struct {
    Name string `json:"name" validate:"required,min=1,max=100"`
    Bio  string `json:"bio"  validate:"max=500"`
}

type UserProfileResponse struct {
    ID        string    `json:"id"`
    Name      string    `json:"name"`
    Bio       string    `json:"bio"`
    AvatarURL string    `json:"avatar_url"`
    CreatedAt time.Time `json:"created_at"`
}
```

## In The Wild

**The mapping overhead is a feature.** Writing `UserResponseFrom(user)` takes more code than returning the struct directly. Teams resist this because it feels like busywork. But that explicit mapping is your defense against accidentally exposing `PasswordHash` in an API response, or including `InternalNotes` in a JSON payload. The mapping is where you make explicit decisions about what each representation contains. Letting the struct implicitly serve all purposes means those decisions are made by whoever adds the next struct tag.

**Value objects for fields that belong together.** If you find the same cluster of fields appearing in multiple structs — say, `Street`, `City`, `State`, `PostalCode`, `Country` — that cluster deserves its own type:

```go
type Address struct {
    Street     string
    City       string
    State      string
    PostalCode string
    Country    string
}
```

This is cheaper than you think. A Go struct with no pointer receivers is a value type; copying it is cheap. The type gives the cluster a name, which makes your code more readable.

**Protobuf and generated types.** If you are using gRPC, your generated types are already separate from your domain types. The temptation is to use the protobuf-generated struct as the domain type to avoid mapping. Resist it. Protobuf types have serialization semantics baked in; your domain types should not.

## The Gotchas

**Struct embedding.** Go's struct embedding can help decompose a God struct into embedded parts that can be tested and maintained independently. But embedding is inheritance-lite — the parent struct inherits all the embedded type's fields and methods, which can create confusion about ownership. Use embedding for genuine "is-a" relationships; use composition (fields that hold other structs) for "has-a" relationships.

**JSON marshaling of embedded structs.** When you embed a struct in another and marshal to JSON, all the embedded fields are promoted to the top level. If two embedded structs have a field with the same name, the outer struct's version wins silently. This is an easy source of subtle bugs when you are trying to compose structs across API layers.

**Nil pointer fields.** Optional fields in God structs are often `*SomeType` pointers. Accessing a nil pointer panics. Code that uses a God struct sprinkles nil checks throughout its logic. Separate types eliminate this: a `Subscription` type simply is not present when the user has no subscription — you deal with the nil at the boundary, not inside every function that touches the user.

## Key Takeaway

A struct that serves every context serves none of them well. Database models, domain entities, API request/response types, and admin projections are different concerns and deserve different types. The mapping code between them is not overhead — it is the place where you make explicit decisions about representation. A codebase with twenty well-scoped types is far healthier than one with five God structs that contain everything imaginable.

---

**Go Anti-Patterns & Code Smells**

Previous: [Lesson 1: Interface Everywhere Syndrome — Not every dependency needs an interface](/post/go/go-anti-interface-everywhere/)
Next: [Lesson 3: Global Mutable State — The variable that breaks every test](/post/go/go-anti-global-state/)
