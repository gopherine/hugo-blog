---
title: 'Go Idioms: Table-Driven Tests'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - table-driven tests
  - testing
  - t.Run
  - subtests
  - t.Parallel
  - golden files
  - test patterns
tags:
  - Go tutorial
  - golang
date: '2026-02-09T00:00:00.000Z'
---
re the pattern that scales. When you need to test a function against thirty inputs, duplicating the test function thirty times is unreadable. Writing a slice of test cases and looping over them keeps your test code as clean as your production code.

The pattern is everywhere in the Go standard library. Once you recognize it, you'll start writing it naturally.

## The Basic Pattern

Start with a function worth testing. Let's use a simple email validator:

```go
// validator.go
func IsValidEmail(email string) bool {
    parts := strings.Split(email, "@")
    if len(parts) != 2 {
        return false
    }
    local, domain := parts[0], parts[1]
    if len(local) == 0 || len(domain) == 0 {
        return false
    }
    if !strings.Contains(domain, ".") {
        return false
    }
    return true
}
```

Without table-driven tests, you'd write something like this:

```go
// WRONG — repetitive, hard to extend
func TestIsValidEmail(t *testing.T) {
    if !IsValidEmail("user@example.com") {
        t.Error("expected valid email to pass")
    }
    if IsValidEmail("notanemail") {
        t.Error("expected invalid email to fail")
    }
    if IsValidEmail("@example.com") {
        t.Error("expected missing local part to fail")
    }
    // Adding case 4, 5, 6... means more copy-paste
}
```

Add ten more edge cases and this becomes a maintenance nightmare. What's the input for each failure? You have to read carefully.

```go
// RIGHT — table-driven test
func TestIsValidEmail(t *testing.T) {
    tests := []struct {
        name  string
        email string
        want  bool
    }{
        {"valid email", "user@example.com", true},
        {"valid with subdomain", "user@mail.example.com", true},
        {"no at sign", "notanemail", false},
        {"missing local", "@example.com", false},
        {"missing domain", "user@", false},
        {"domain without dot", "user@localhost", false},
        {"empty string", "", false},
        {"multiple at signs", "a@b@c.com", false},
    }

    for _, tt := range tests {
        got := IsValidEmail(tt.email)
        if got != tt.want {
            t.Errorf("IsValidEmail(%q) = %v, want %v", tt.email, got, tt.want)
        }
    }
}
```

Every case is one line. Adding a new case is trivial. When a test fails, `t.Errorf` with `%q` and the actual/expected values tells you exactly what went wrong without reading the test body.

One naming convention worth adopting: name your test case struct fields `name`, `input` (or the specific field names), and `want`. The `want` convention is idiomatic Go — it signals the expected outcome at a glance.

## t.Run for Subtests

The table above works, but when a test fails, the output just says `TestIsValidEmail` failed. You don't immediately know which case. Use `t.Run` to create subtests — each case becomes its own named test.

```go
// RIGHT — subtests with t.Run
func TestIsValidEmail(t *testing.T) {
    tests := []struct {
        name  string
        email string
        want  bool
    }{
        {"valid email", "user@example.com", true},
        {"no at sign", "notanemail", false},
        {"missing local", "@example.com", false},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got := IsValidEmail(tt.email)
            if got != tt.want {
                t.Errorf("IsValidEmail(%q) = %v, want %v", tt.email, got, tt.want)
            }
        })
    }
}
```

Now when "missing local" fails, the test output says `TestIsValidEmail/missing_local` failed (Go replaces spaces with underscores in subtest names). You can run a single subtest in isolation:

```
go test -run TestIsValidEmail/missing_local
```

This is invaluable when debugging a specific case in a large test table.

**The loop variable gotcha.** In Go versions before 1.22, there was a classic bug with goroutines in loops: the loop variable `tt` was shared across iterations, so by the time the goroutine ran, `tt` might have moved to the next case. In Go 1.22+, loop variables have per-iteration scope, so this is no longer a problem. If you're on an older version, capture the variable:

```go
// Go < 1.22: capture tt before the goroutine
for _, tt := range tests {
    tt := tt  // creates a new variable scoped to this iteration
    t.Run(tt.name, func(t *testing.T) {
        t.Parallel()
        // use tt safely
    })
}
```

## t.Parallel for Speed

Mark subtests as parallel to run them concurrently. This can significantly speed up tests that do I/O, sleep, or otherwise block.

```go
func TestIsValidEmail(t *testing.T) {
    tests := []struct {
        name  string
        email string
        want  bool
    }{
        {"valid email", "user@example.com", true},
        {"no at sign", "notanemail", false},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            t.Parallel()  // this subtest runs concurrently with others
            got := IsValidEmail(tt.email)
            if got != tt.want {
                t.Errorf("IsValidEmail(%q) = %v, want %v", tt.email, got, tt.want)
            }
        })
    }
}
```

`t.Parallel()` must be the first call in the subtest function. It signals that this subtest can run concurrently with other parallel subtests in the same test binary. Only use it when your test is truly parallel-safe — no shared mutable state between cases.

## Testing Error Cases

When the function under test returns an error, the table expands to include what error you expect.

```go
// The function under test
func ParseAge(s string) (int, error) {
    n, err := strconv.Atoi(s)
    if err != nil {
        return 0, fmt.Errorf("ParseAge: %q is not a number: %w", s, err)
    }
    if n < 0 || n > 150 {
        return 0, fmt.Errorf("ParseAge: %d is out of range [0, 150]", n)
    }
    return n, nil
}
```

```go
func TestParseAge(t *testing.T) {
    tests := []struct {
        name    string
        input   string
        want    int
        wantErr bool
    }{
        {"valid age", "25", 25, false},
        {"zero", "0", 0, false},
        {"max age", "150", 150, false},
        {"negative", "-1", 0, true},
        {"too large", "200", 0, true},
        {"not a number", "abc", 0, true},
        {"empty string", "", 0, true},
    }

    for _, tt := range tests {
        t.Run(tt.name, func(t *testing.T) {
            got, err := ParseAge(tt.input)
            if (err != nil) != tt.wantErr {
                t.Errorf("ParseAge(%q) error = %v, wantErr %v", tt.input, err, tt.wantErr)
                return
            }
            if !tt.wantErr && got != tt.want {
                t.Errorf("ParseAge(%q) = %d, want %d", tt.input, got, tt.want)
            }
        })
    }
}
```

The `(err != nil) != tt.wantErr` check is a compact way to say "I expected an error but didn't get one, or I didn't expect an error but got one." Either situation is a test failure.

When you need to check for a specific error value (not just any error), use `errors.Is`:

```go
tests := []struct {
    name    string
    input   string
    wantErr error  // nil means no error expected
}{
    {"not found", "unknown-id", ErrNotFound},
    {"forbidden", "locked-id", ErrForbidden},
    {"valid", "active-id", nil},
}

for _, tt := range tests {
    t.Run(tt.name, func(t *testing.T) {
        _, err := GetResource(tt.input)
        if !errors.Is(err, tt.wantErr) {
            t.Errorf("GetResource(%q) error = %v, want %v", tt.input, err, tt.wantErr)
        }
    })
}
```

## Golden File Testing

For functions that produce large or complex outputs — rendered HTML, formatted JSON, generated code — comparing strings inline in test tables is impractical. Golden files store the expected output in files alongside your tests.

```go
// The function under test — generates a config file
func GenerateConfig(opts Options) string {
    // ... returns a multi-line string
}
```

```go
// WRONG — hardcoding large expected output
func TestGenerateConfig(t *testing.T) {
    got := GenerateConfig(defaultOpts)
    want := `# Generated config
host: localhost
port: 8080
# ... 50 more lines
`
    if got != want {
        t.Errorf("output mismatch:\ngot:\n%s\nwant:\n%s", got, want)
    }
}
```

```go
// RIGHT — golden file testing
var update = flag.Bool("update", false, "update golden files")

func TestGenerateConfig(t *testing.T) {
    got := GenerateConfig(defaultOpts)

    goldenPath := filepath.Join("testdata", "config.golden")

    if *update {
        // Run with -update to regenerate golden files
        os.MkdirAll("testdata", 0755)
        os.WriteFile(goldenPath, []byte(got), 0644)
        return
    }

    want, err := os.ReadFile(goldenPath)
    if err != nil {
        t.Fatalf("reading golden file %s: %v", goldenPath, err)
    }

    if got != string(want) {
        t.Errorf("output mismatch with golden file %s\ngot:\n%s", goldenPath, got)
    }
}
```

When the output format changes intentionally, run `go test -update` to regenerate the golden files, then commit them. The golden files live in `testdata/` which is a convention the `go` tool understands — it ignores `testdata/` directories during normal builds.

## Prefer stdlib Assertions

There's a common instinct to reach for `testify/assert` or `testify/require`. For simple cases, resist it. The standard library is all you need and the error messages are good when you use them right.

```go
// Using testify — adds a dependency for this
assert.Equal(t, want, got, "email validation result")
require.NoError(t, err)

// stdlib equivalents — no dependency, just as readable
if got != want {
    t.Errorf("IsValidEmail(%q) = %v, want %v", email, got, want)
}
if err != nil {
    t.Fatalf("unexpected error: %v", err)
}
```

The key is the error message. `t.Errorf("got %v, want %v", got, want)` is exactly as informative as `assert.Equal`. The difference is that `t.Fatal`/`t.Fatalf` stops the current test immediately (equivalent to `require`), while `t.Error`/`t.Errorf` marks the test as failed but continues running.

Use `t.Fatal` when subsequent lines depend on a previous step succeeding. Use `t.Error` when you want to collect multiple failures in one test run. That's the whole decision.

When your test suite genuinely needs deep equality comparisons on complex structs with good diffs, `github.com/google/go-cmp/cmp` from the Go team is the right choice — better diffs than testify with no assertion magic.

```go
import "github.com/google/go-cmp/cmp"

if diff := cmp.Diff(want, got); diff != "" {
    t.Errorf("mismatch (-want +got):\n%s", diff)
}
```

Table-driven tests are a mindset as much as a pattern. Every time you write a test, ask: what are the interesting input variations? What edge cases does this function need to handle? Write them down as table cases. Your tests become a specification, not just a safety net.
