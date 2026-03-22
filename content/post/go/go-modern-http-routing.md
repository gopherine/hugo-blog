---
title: "Lesson 2: Enhanced HTTP Routing — Method patterns in net/http, no more gorilla/mux"
author: Atharva Pandey
series: "Modern Go: 1.21 to 1.26"
lesson: 2
keywords:
  - Go 1.22
  - net/http routing
  - HTTP method patterns
  - ServeMux
  - gorilla mux
  - path parameters
  - golang router
tags:
  - Go tutorial
  - golang
  - modern Go
date: "2024-08-11T00:00:00.000Z"
---

For most of my Go career, the standard library's `net/http.ServeMux` was something I used for toy services and immediately replaced with `gorilla/mux` for anything real. The standard mux could not match HTTP methods. It could not extract path parameters. It matched prefixes in ways that surprised people. The moment a production service needed `GET /users/{id}` and `POST /users`, you reached for a dependency.

Go 1.22 changed that. The enhanced `ServeMux` now supports method matching, wildcard path parameters, and precedence rules that actually make sense. I rewrote three internal services to drop `gorilla/mux` after reading the release notes, and every one of them got shorter and simpler. This lesson covers exactly what changed and when you still might want a third-party router.

## The Problem

The old `ServeMux` had two matching rules: exact paths and rooted subtrees (patterns ending in `/`). That meant:

```go
// Old ServeMux — this is the best you could do
mux.HandleFunc("/users/", usersHandler) // matches /users/, /users/123, /users/123/posts...
```

One handler for all of `/users/`. You would parse the path yourself inside the handler. And if a client sent `DELETE /users/123` when you only supported `GET`, your handler received it anyway. You had to check `r.Method` manually and return a 405. It was boilerplate everywhere.

People solved this with `gorilla/mux`, `chi`, `httprouter`, or `gin`. All fine libraries, but they are dependencies — they need maintenance, they have their own quirks, and for many services they are the *only* third-party dependency in the go.mod. Carrying a router just to get method matching and path parameters always felt like too much.

## How It Works

Go 1.22 extended the pattern syntax for `ServeMux` to support:

**Method matching** — prefix the pattern with the method and a space:

```go
mux.HandleFunc("GET /users", listUsers)
mux.HandleFunc("POST /users", createUser)
mux.HandleFunc("GET /users/{id}", getUser)
mux.HandleFunc("PUT /users/{id}", updateUser)
mux.HandleFunc("DELETE /users/{id}", deleteUser)
```

Each pattern is now `[METHOD ][HOST]/[PATH]`. The method part is optional — if omitted, the handler matches all methods, exactly as before.

**Wildcard path parameters** — `{name}` in a pattern binds the matching path segment. You retrieve it with `r.PathValue("name")`:

```go
func getUser(w http.ResponseWriter, r *http.Request) {
    id := r.PathValue("id")
    // id is now "123" for a request to GET /users/123
}
```

**Catch-all wildcards** — `{name...}` matches the rest of the path including slashes:

```go
mux.HandleFunc("GET /files/{path...}", serveFile)
// Matches /files/images/photo.jpg, /files/docs/2024/report.pdf, etc.
```

**Precedence is deterministic.** More specific patterns win over less specific ones. A pattern with a literal segment beats one with a wildcard at the same position. Method-specific patterns beat method-less patterns. The compiler panics at startup if two patterns would both match a request with the same specificity — no silent ambiguity.

**Automatic 405 responses.** If you register `GET /users/{id}` and a client sends `DELETE /users/123`, and you have no `DELETE /users/{id}` handler, the mux automatically returns `405 Method Not Allowed` with an `Allow` header listing the registered methods. You get this for free.

**Trailing slash redirect.** The mux still redirects `GET /users` to `GET /users/` if only the latter is registered. You can disable this by registering the exact path.

## In Practice

Here is a complete API router using only the standard library:

```go
package main

import (
    "encoding/json"
    "log"
    "net/http"
)

func main() {
    mux := http.NewServeMux()

    mux.HandleFunc("GET /api/users", listUsers)
    mux.HandleFunc("POST /api/users", createUser)
    mux.HandleFunc("GET /api/users/{id}", getUser)
    mux.HandleFunc("PUT /api/users/{id}", updateUser)
    mux.HandleFunc("DELETE /api/users/{id}", deleteUser)

    // Nested resources
    mux.HandleFunc("GET /api/users/{userID}/posts", listUserPosts)
    mux.HandleFunc("GET /api/users/{userID}/posts/{postID}", getUserPost)

    log.Fatal(http.ListenAndServe(":8080", mux))
}

func getUser(w http.ResponseWriter, r *http.Request) {
    id := r.PathValue("id")
    // fetch user by id...
    json.NewEncoder(w).Encode(map[string]string{"id": id})
}
```

This replaces what previously required importing gorilla/mux and its transitive dependencies.

**Middleware still composes the same way.** Since `ServeMux` implements `http.Handler`, you wrap it exactly as before:

```go
handler := loggingMiddleware(authMiddleware(mux))
log.Fatal(http.ListenAndServe(":8080", handler))
```

If you were building per-route middleware with gorilla, you can replicate that by wrapping individual handler functions before registering them:

```go
mux.Handle("GET /api/admin/users", adminOnly(http.HandlerFunc(listUsers)))
```

**Testing** is equally clean. `httptest.NewRecorder` and `httptest.NewRequest` work the same way — you do not need to change your test code at all.

## The Gotchas

**`r.PathValue` requires Go 1.22.** This is obvious but worth stating: if any environment in your pipeline is on Go 1.21 or older, path value extraction will not compile. Check your `go.mod` and CI build images.

**Host-specific patterns.** You can include a hostname in patterns: `"GET example.com/api/users"`. This matches requests where the `Host` header matches. It is a useful feature for virtual hosting but bites people who add it accidentally.

**The empty pattern.** Registering `"/"` still catches everything the mux does not match. This is a subtree match, not an exact path match. To match only the root, register `"/."` or check `r.URL.Path == "/"` inside the handler.

**Wildcards do not span slashes.** A `{id}` wildcard matches exactly one path segment — no slashes. `GET /users/{id}` will not match `/users/foo/bar`. Use `{rest...}` for that.

**Existing code using method checks manually still works.** If you have handlers that switch on `r.Method` internally, they continue to work. The new patterns are additive, not breaking.

**When to still use a third-party router.** If you need regex constraints on path parameters, complex route groups with shared prefix middleware, or you are on Go 1.21 or older, a library like `chi` or `httprouter` is still reasonable. For greenfield services on Go 1.22+, the standard mux handles the common cases without a dependency.

## Key Takeaway

Go 1.22's enhanced `ServeMux` brings method matching, path parameter extraction, automatic 405 responses, and deterministic precedence rules to the standard library. For the majority of HTTP services, this eliminates the need for a third-party router. The pattern syntax is compact, the behaviour is predictable, and middleware composition is unchanged. If you have a `go.mod` that imports a router purely for these features, it is worth evaluating whether you still need it.

---

**Previous:** [Lesson 1: Range Over Integers and Iterators](/post/go/go-modern-range-iterators/)

**Next:** [Lesson 3: Loop Variable Fix — The Go 1.22 change that fixed a decade of bugs](/post/go/go-modern-loop-var/)
