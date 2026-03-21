---
title: "Lesson 1: Error Handling — Your code is lying if it ignores errors"
author: Atharva Pandey
keywords:
  - Go
  - golang
  - error handling
  - errors.Is
  - errors.As
  - sentinel errors
  - fmt.Errorf
tags:
  - Go tutorial
  - golang
series: "Idiomatic Go"
lesson: 1
date: '2025-07-14T00:00:00.000Z'
---

If you're coming from Python, Java, or JavaScript, Go's error handling will feel strange at first. There's no `try/catch`. No exceptions bubbling up the call stack. Instead, errors are just values — and you deal with them right where they happen. Ignore them and your code doesn't crash loudly; it quietly lies to you, and you won't find out until 2am when production is on fire.

## The Problem

The blank identifier `_` is the most dangerous character in Go. Here's what it looks like when engineers first start writing Go:

```go
// WRONG — ignoring the error entirely
f, _ := os.Open("config.json")
data, _ := io.ReadAll(f)
fmt.Println(string(data))
```

This compiles. It runs. And if `config.json` doesn't exist, `f` is nil, `ReadAll` panics, and you get a cryptic runtime error with no context about what actually went wrong. I've seen this exact pattern ship to production — usually written by someone who "just wanted to get it working" and never came back to fix it.

The second failure mode is subtler: returning an error without wrapping it.

```go
// WRONG — losing context
func loadConfig(path string) (Config, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return Config{}, err  // caller sees: "open config.json: no such file or directory"
    }
    // ...
}
```

If `loadConfig` is called from five different places, you won't know which one failed. The error has no story. At 2am, you're staring at a log line with no idea where in your codebase it came from.

## The Idiomatic Way

Check every error. Wrap them with context as they travel up the stack.

```go
// RIGHT — checking every error
f, err := os.Open("config.json")
if err != nil {
    log.Fatalf("failed to open config: %v", err)
}
defer f.Close()

data, err := io.ReadAll(f)
if err != nil {
    log.Fatalf("failed to read config: %v", err)
}
fmt.Println(string(data))
```

Yes, it's more lines. That's the point. You know exactly what failed, where, and why.

For errors that travel up multiple call stack layers, `fmt.Errorf` with `%w` wraps the original error so callers can still inspect it:

```go
// RIGHT — wrapping with context
func loadConfig(path string) (Config, error) {
    data, err := os.ReadFile(path)
    if err != nil {
        return Config{}, fmt.Errorf("loadConfig: reading file %q: %w", path, err)
    }

    var cfg Config
    if err := json.Unmarshal(data, &cfg); err != nil {
        return Config{}, fmt.Errorf("loadConfig: parsing JSON from %q: %w", path, err)
    }

    return cfg, nil
}
```

Now when this surfaces at the top of your program, the error reads like a breadcrumb trail: `loadConfig: parsing JSON from "config.json": invalid character ',' looking for beginning of value`. You know exactly what happened without a stack trace.

The `%w` verb is important — it's not just formatting the error as a string. It wraps it, preserving the original error value so it can be unwrapped later with `errors.Is` and `errors.As`.

**Sentinel errors** let callers react differently based on what kind of error occurred:

```go
var ErrNotFound = errors.New("not found")

func GetUser(id string) (User, error) {
    user, exists := db[id]
    if !exists {
        return User{}, fmt.Errorf("GetUser %q: %w", id, ErrNotFound)
    }
    return user, nil
}
```

```go
// RIGHT — using errors.Is to match through the chain
user, err := GetUser("abc123")
if err != nil {
    if errors.Is(err, ErrNotFound) {
        http.Error(w, "user not found", http.StatusNotFound)
        return
    }
    http.Error(w, "internal server error", http.StatusInternalServerError)
    return
}
```

Don't use `==` to compare errors — it breaks the moment any function wraps the error. `errors.Is` walks the entire error chain.

When you need data from the error (not just a signal), define a custom type and use `errors.As`:

```go
type ValidationError struct {
    Field   string
    Message string
}

func (e *ValidationError) Error() string {
    return fmt.Sprintf("validation failed on field %q: %s", e.Field, e.Message)
}
```

```go
var ve *ValidationError
if errors.As(err, &ve) {
    fmt.Printf("Bad input on field: %s\n", ve.Field)
}
```

`errors.As` also walks the chain, so wrapping doesn't hide your custom error type.

## In The Wild

An HTTP handler that loads a user from a database. Without proper error handling, any failure in the chain returns a 500 with no useful diagnostics.

```go
// WRONG — treating all errors the same, losing specifics
func profileHandler(w http.ResponseWriter, r *http.Request) {
    userID := r.URL.Query().Get("id")
    user, err := db.GetUser(userID)
    if err != nil {
        http.Error(w, "something went wrong", 500)
        return
    }
    renderProfile(w, user)
}
```

```go
// RIGHT — different errors, different responses
func profileHandler(w http.ResponseWriter, r *http.Request) {
    userID := r.URL.Query().Get("id")

    user, err := db.GetUser(userID)
    if err != nil {
        switch {
        case errors.Is(err, ErrNotFound):
            http.Error(w, "user not found", http.StatusNotFound)
        case errors.Is(err, ErrPermissionDenied):
            http.Error(w, "forbidden", http.StatusForbidden)
        default:
            log.Printf("profileHandler: unexpected error for user %q: %v", userID, err)
            http.Error(w, "internal server error", http.StatusInternalServerError)
        }
        return
    }

    renderProfile(w, user)
}
```

The difference shows up in production dashboards. Your monitoring sees 404s for missing users instead of a flood of 500s. Your clients get useful status codes. Your on-call engineer gets context in the logs. This isn't boilerplate — it's operability.

## The Gotchas

**Wrapping without substance.** `fmt.Errorf("error: %w", err)` adds nothing. Every layer should include what operation failed and with what inputs. "loadConfig: reading file" tells a story; "error" does not.

**Logging and returning.** Pick one. If you log the error, handle it. If you return it, let the caller log it. Doing both means every error gets logged twice — once at each layer — and your logs become noise.

**Using `errors.New` for dynamic messages.** `errors.New("user 123 not found")` creates a new error value every time, so `errors.Is(err, thatError)` will never match it. Use sentinel errors for comparable values, and put dynamic context in the wrapping message around them, not in the sentinel itself.

## Key Takeaway

Go's error handling is explicit by design — errors are first-class values, not invisible exceptions. The pattern of checking every error immediately and wrapping it with context at each layer might feel like busywork when you're writing it, but it pays off every time something breaks in production and you can trace the failure in seconds instead of hours. The verbosity is the feature. Don't fight it.

---

[Course Index](/post/go/) | Next: [Lesson 2: defer for Cleanup](/post/go/go-idioms-defer-cleanup/) →
