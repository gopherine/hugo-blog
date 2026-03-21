---
title: 'Go Idioms: Error Handling'
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
date: '2025-07-14T00:00:00.000Z'
---
re coming from Python, Java, or JavaScript, Go's error handling will feel strange at first. There's no `try/catch`. No exceptions bubbling up the call stack. Instead, errors are just values — and you deal with them right where they happen. Once that clicks, you start to appreciate the clarity it forces on you.

## The Basic Pattern

Every function that can fail returns an error as its last return value. The caller checks it immediately.

```go
// WRONG — ignoring the error entirely
f, _ := os.Open("config.json")
data, _ := io.ReadAll(f)
fmt.Println(string(data))
```

This compiles. It runs. And if `config.json` doesn't exist, `f` is nil, `ReadAll` panics, and you get a cryptic runtime error with no context about what actually went wrong. The blank identifier `_` is silently swallowing a failure.

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

Yes, it's more lines. That's the point. You know exactly what failed, where, and why. There's no guessing which layer threw an exception.

## Wrapping Errors with Context

When you return an error up the call stack, add context at each layer. `fmt.Errorf` with the `%w` verb wraps the original error so callers can still inspect it.

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

If `loadConfig` is called from five different places in your codebase, you won't know which call failed. The error has no story.

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

Now when this surfaces at the top of your program, the error message reads like a breadcrumb trail: `loadConfig: parsing JSON from "config.json": invalid character ',' looking for beginning of value`. You know exactly what happened without a stack trace.

The `%w` verb (introduced in Go 1.13) is important — it's not just formatting the error as a string. It wraps it, preserving the original error value so it can be unwrapped later.

## Sentinel Errors

Sometimes callers need to react differently based on what kind of error occurred. Sentinel errors are package-level variables that represent specific failure conditions.

```go
// In your package
var ErrNotFound = errors.New("not found")
var ErrPermissionDenied = errors.New("permission denied")

func GetUser(id string) (User, error) {
    user, exists := db[id]
    if !exists {
        return User{}, fmt.Errorf("GetUser %q: %w", id, ErrNotFound)
    }
    return user, nil
}
```

The caller uses `errors.Is` to check for a specific sentinel, even through wrapping layers:

```go
// RIGHT — using errors.Is to match through the chain
user, err := GetUser("abc123")
if err != nil {
    if errors.Is(err, ErrNotFound) {
        http.Error(w, "user not found", http.StatusNotFound)
        return
    }
    http.Error(w, "internal server error", http.StatusInternalServerError)
    log.Printf("unexpected error: %v", err)
    return
}
```

```go
// WRONG — comparing errors directly
if err == ErrNotFound {  // breaks if the error is wrapped
    // ...
}
```

Direct equality fails the moment any intermediate function wraps the error. `errors.Is` walks the entire error chain, so your sentinel check works regardless of how many layers of wrapping exist.

## Structured Error Types with errors.As

Sometimes you need more than a signal — you need data from the error. Define a custom error type and use `errors.As` to extract it.

```go
type ValidationError struct {
    Field   string
    Message string
}

func (e *ValidationError) Error() string {
    return fmt.Sprintf("validation failed on field %q: %s", e.Field, e.Message)
}

func validateAge(age int) error {
    if age < 0 || age > 150 {
        return &ValidationError{Field: "age", Message: "must be between 0 and 150"}
    }
    return nil
}
```

```go
// RIGHT — using errors.As to extract the structured error
err := validateAge(-5)
if err != nil {
    var ve *ValidationError
    if errors.As(err, &ve) {
        fmt.Printf("Bad input on field: %s\n", ve.Field)
    } else {
        log.Printf("unexpected error: %v", err)
    }
}
```

`errors.As` also walks the chain, so if `validateAge` is called deep inside another function that wraps the error with `fmt.Errorf("%w", err)`, `errors.As` still finds the `ValidationError` buried inside.

## Why This Beats try/catch

In languages with exceptions, you often write code like this:

```python
# Python
try:
    config = load_config("config.json")
    user = fetch_user(config.user_id)
    send_email(user.email)
except FileNotFoundError:
    # is this from load_config? from fetch_user hitting a missing cache file?
    handle_missing_file()
except Exception as e:
    log.error(e)
```

The catch block is far from the failure. You don't know which line threw. The exception might have been swallowed and re-raised by some library somewhere. Tracing it requires reading documentation or source code for every function in the try block.

In Go, there's no ambiguity:

```go
cfg, err := loadConfig("config.json")
if err != nil {
    // this error is only from loadConfig — we know exactly what failed
    log.Fatalf("startup failed: %v", err)
}

user, err := fetchUser(cfg.UserID)
if err != nil {
    log.Fatalf("failed to load user: %v", err)
}

if err := sendEmail(user.Email); err != nil {
    log.Printf("email delivery failed (non-fatal): %v", err)
}
```

Each error is local. Each check is immediate. You can make different decisions at each step — fatal for config problems, non-fatal for email delivery. That granularity is hard to achieve cleanly with exceptions.

## Real-World Scenario: HTTP Middleware

Consider an HTTP handler that loads a user from a database and renders a profile page. Without proper error handling, any failure in the chain returns a 500 with no useful diagnostics.

```go
// WRONG — catching all errors the same way, losing specifics
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

The difference shows up in production. Your monitoring sees 404s for missing users instead of a flood of 500s. Your clients get useful status codes. Your logs have context. Error handling done right isn't boilerplate — it's the difference between a system you can operate and one you're constantly guessing at.

## Common Mistakes to Avoid

**Wrapping without context** — `fmt.Errorf("error: %w", err)` adds nothing. Include what operation failed and with what inputs.

**Returning errors you never log** — if you return an error and the caller returns it too, someone has to log it eventually. Make sure that happens at the top level.

**Using `errors.New` for dynamic messages** — `errors.New("user 123 not found")` creates a new error value every time, so `errors.Is` won't match it. Use sentinel errors for comparable values, and reserve dynamic text for the message context around a sentinel.

Go's error handling is explicit by design. It makes failures first-class citizens of your code, not afterthoughts. The verbosity is the feature.
