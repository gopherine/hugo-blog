---
title: 'Lesson 19: Table-Driven Tests — One test function, fifty test cases'
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
series: "Idiomatic Go"
lesson: 19
---

Most engineers know to write tests. Fewer think about how the test code itself should scale. When you need to cover thirty input variations of a function, duplicating the test body thirty times produces something that's painful to read, painful to extend, and painful to debug when it fails. Table-driven tests are the pattern that scales. A slice of cases, one loop — your test code stays as clean as your production code.

The pattern is everywhere in the Go standard library. Once you recognize it, you'll start writing it naturally.

## The Problem

Without the table pattern, the instinct is to copy-paste assertions:

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
    // Add case 4, 5, 6... means more copy-paste
}
```

Add ten more edge cases and this becomes unreadable. When a test fails, you have to read the assertion message carefully to figure out what input triggered it. The structure doesn't tell you what the inputs are — you have to hunt.

There's a second problem: when a test fails without `t.Run`, the output just says `TestIsValidEmail` failed. You have to read the body to figure out which of the dozen cases broke.

## The Idiomatic Way

Define a slice of anonymous structs. Each struct is one test case. Loop and run.

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
        t.Run(tt.name, func(t *testing.T) {
            got := IsValidEmail(tt.email)
            if got != tt.want {
                t.Errorf("IsValidEmail(%q) = %v, want %v", tt.email, got, tt.want)
            }
        })
    }
}
```

Every case is one line. Adding a new case is trivial — you write one struct literal. When `TestIsValidEmail/missing_local` fails (Go replaces spaces with underscores in subtest names), you can run it in isolation:

```
go test -run TestIsValidEmail/missing_local
```

The naming convention: `name`, the specific input fields, and `want`. The `want` convention signals the expected outcome at a glance and is used consistently throughout the Go standard library and most production Go code I've read.

For functions that return errors, expand the struct:

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

The `(err != nil) != tt.wantErr` check is a compact idiom: true when you expected an error and didn't get one, or when you didn't expect one but got one. Either way, it's a failure.

When you need to check for a specific error, use `errors.Is`:

```go
tests := []struct {
    name    string
    input   string
    wantErr error
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

## In The Wild

For functions that produce large or complex outputs — rendered templates, formatted configs, generated code — comparing strings inline in test tables is impractical. Golden files store the expected output on disk alongside your tests.

```go
var update = flag.Bool("update", false, "update golden files")

func TestGenerateConfig(t *testing.T) {
    got := GenerateConfig(defaultOpts)
    goldenPath := filepath.Join("testdata", "config.golden")

    if *update {
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

When output changes intentionally, run `go test -update` to regenerate the files, then commit them. The `testdata/` directory is a Go convention — the toolchain ignores it during builds, so it's safe to put test fixtures there.

For parallel subtests, call `t.Parallel()` as the first line of the subtest function. It signals that this subtest can run concurrently with other parallel subtests in the same test binary. Only use it when test cases have no shared mutable state.

## The Gotchas

**Not using `t.Run`.** Without subtests, a failure tells you the top-level test failed, not which case. Always use `t.Run` — the individually-named subtests are how you actually debug failures at scale.

**Skipping the error message format.** A failing test that says `got false, want true` is useless. Include the input in every error message: `t.Errorf("IsValidEmail(%q) = %v, want %v", email, got, want)`. When you're staring at a CI failure at midnight, you want to see the input immediately.

**Reaching for testify when stdlib is enough.** For simple cases, `t.Errorf` with a good format string is exactly as informative as `assert.Equal`. The one genuinely useful external tool is `github.com/google/go-cmp/cmp` for deep struct comparisons — better diffs than testify, no assertion magic, from the Go team itself.

```go
if diff := cmp.Diff(want, got); diff != "" {
    t.Errorf("mismatch (-want +got):\n%s", diff)
}
```

## Key Takeaway

Table-driven tests are a mindset as much as a pattern. Every time you write a test, the question should be: what are the interesting input variations? What edge cases does this function need to handle? Write them down as table cases. Your tests become a specification — a living document of what the function is supposed to do under every condition you've thought about. That's worth more than a one-off assertion that gets forgotten as soon as the test passes. Once you write tests this way, going back to copy-pasted assertions feels genuinely painful.

---

← [Previous: sync.Mutex Is Often Simpler](/post/go/go-idioms-mutex-simpler) | [Course Index](/post/go/) | [Next: internal Package Is Underrated](/post/go/go-idioms-internal-package) →
