---
title: "Lesson 7: Golden File Testing — Store expected output, compare on run"
author: Atharva Pandey
series: "Go Testing Masterclass"
lesson: 7
keywords:
  - Go
  - golang
  - testing
  - golden files
  - snapshot testing
  - testdata
  - -update flag
  - output testing
tags:
  - Go tutorial
  - golang
  - testing
date: '2024-12-05T00:00:00.000Z'
---

Some functions produce output that's too large or too structured to assert inline. A template renderer, a code generator, a JSON serializer for a deeply nested type, a CLI help text formatter — any of these can produce hundreds of lines of output that you need to verify is correct. Hardcoding that expected output inside the test function creates a wall of string literals. The golden file pattern solves this by storing the expected output in a file, comparing against it at test time, and offering a flag to regenerate it when the output intentionally changes.

I started using golden files after spending two hours updating inline `want` strings in a test for a code generator. The second time I had to do it, I stopped and wrote a golden file helper. I haven't looked back.

## The Problem

The inline approach breaks down fast when output is large:

```go
// WRONG — hardcoded expected output inline
func TestGenerateStruct(t *testing.T) {
    got := GenerateStruct("User", []Field{
        {Name: "ID", Type: "int64"},
        {Name: "Email", Type: "string"},
        {Name: "CreatedAt", Type: "time.Time"},
    })

    // Good luck reading this, and good luck updating it when the template changes
    want := `type User struct {
	ID        int64
	Email     string
	CreatedAt time.Time
}

func NewUser(id int64, email string, createdAt time.Time) User {
	return User{
		ID:        id,
		Email:     email,
		CreatedAt: createdAt,
	}
}
`
    if got != want {
        t.Errorf("output mismatch:\ngot:\n%s\nwant:\n%s", got, want)
    }
}
```

This is readable for small output. For a code generator that produces 200 lines, it's unusable. When the output format changes — you add a comment, fix indentation, add a validation method — you have to manually update the `want` string. That's error-prone and tedious. And `diff` output in test failures for long strings is unreadable.

A worse pattern is not testing the output at all because it's "too complex to compare":

```go
// WRONG — skipping verification because output comparison feels hard
func TestRenderTemplate(t *testing.T) {
    out, err := RenderTemplate("welcome", map[string]string{
        "Name": "Alice",
    })
    if err != nil {
        t.Fatal(err)
    }
    // "Just make sure it doesn't error" — we're not testing the actual output
    _ = out
}
```

Functions that produce complex output and are only tested for "doesn't crash" are the source of subtle regressions that nobody notices until a user reports them.

## The Idiomatic Way

Golden files live in `testdata/` (which Go's tooling treats as non-compiled data). A `-update` flag regenerates them when expected output changes intentionally.

First, a reusable helper:

```go
// testhelpers/golden.go
package testhelpers

import (
    "flag"
    "os"
    "path/filepath"
    "testing"
)

var update = flag.Bool("update", false, "regenerate golden files")

// AssertGolden compares got against the contents of testdata/<name>.golden.
// If -update is passed, it writes got to the file instead.
func AssertGolden(t *testing.T, name string, got []byte) {
    t.Helper()
    path := filepath.Join("testdata", name+".golden")

    if *update {
        if err := os.MkdirAll("testdata", 0755); err != nil {
            t.Fatalf("mkdir testdata: %v", err)
        }
        if err := os.WriteFile(path, got, 0644); err != nil {
            t.Fatalf("write golden file: %v", err)
        }
        t.Logf("updated golden file: %s", path)
        return
    }

    want, err := os.ReadFile(path)
    if err != nil {
        t.Fatalf("read golden file %s: %v\n(run with -update to create it)", path, err)
    }

    if string(got) != string(want) {
        t.Errorf("output mismatch for %s\n--- want ---\n%s\n--- got ---\n%s",
            name, want, got)
    }
}
```

Now the test:

```go
// RIGHT — golden file comparison
func TestGenerateStruct(t *testing.T) {
    tests := []struct {
        name   string
        struct_ string
        fields []Field
    }{
        {
            name:    "simple_struct",
            struct_: "User",
            fields: []Field{
                {Name: "ID", Type: "int64"},
                {Name: "Email", Type: "string"},
                {Name: "CreatedAt", Type: "time.Time"},
            },
        },
        {
            name:    "empty_struct",
            struct_: "Config",
            fields:  nil,
        },
        {
            name:    "many_fields",
            struct_: "Order",
            fields: []Field{
                {Name: "ID", Type: "string"},
                {Name: "UserID", Type: "int64"},
                {Name: "Items", Type: "[]Item"},
                {Name: "Status", Type: "OrderStatus"},
                {Name: "Total", Type: "decimal.Decimal"},
                {Name: "CreatedAt", Type: "time.Time"},
                {Name: "UpdatedAt", Type: "time.Time"},
            },
        },
    }

    for _, tt := range tests {
        tt := tt
        t.Run(tt.name, func(t *testing.T) {
            got := GenerateStruct(tt.struct_, tt.fields)
            testhelpers.AssertGolden(t, "generate_struct/"+tt.name, []byte(got))
        })
    }
}
```

First run: `go test -run TestGenerateStruct -update`. This creates:
- `testdata/generate_struct/simple_struct.golden`
- `testdata/generate_struct/empty_struct.golden`
- `testdata/generate_struct/many_fields.golden`

Review those files. Commit them. From now on, `go test` compares output against the committed golden files. When you intentionally change the output format, run with `-update` again, review the diff in git, and commit the updated goldens.

## In The Wild

Golden files shine for CLI output, template rendering, and code generation. Here's a pattern for testing a CLI command's stdout:

```go
func TestHelp Command(t *testing.T) {
    var buf bytes.Buffer
    cmd := NewRootCommand()
    cmd.SetOut(&buf)
    cmd.SetArgs([]string{"--help"})

    if err := cmd.Execute(); err != nil {
        t.Fatalf("Execute: %v", err)
    }

    testhelpers.AssertGolden(t, "help_output", buf.Bytes())
}
```

And for HTTP response bodies where you want to lock down the exact JSON shape:

```go
func TestListOrdersResponse(t *testing.T) {
    store := newFakeOrderStore()
    store.seed([]Order{
        {ID: "ord-1", UserID: 1, Status: "pending", Total: "29.99"},
        {ID: "ord-2", UserID: 1, Status: "shipped", Total: "49.99"},
    })
    srv := newTestServer(t, store)

    resp, err := srv.Client().Get(srv.URL + "/users/1/orders")
    if err != nil {
        t.Fatal(err)
    }
    defer resp.Body.Close()

    body, _ := io.ReadAll(resp.Body)
    // Normalize timestamps before golden comparison if needed
    normalized := normalizeTimestamps(body)
    testhelpers.AssertGolden(t, "list_orders_response", normalized)
}
```

## The Gotchas

**Non-deterministic output.** Timestamps, random IDs, and map iteration order will produce different output on every run. Normalize them before golden comparison — strip or replace timestamps, sort map keys, replace random IDs with fixed placeholders. Otherwise the golden test is always flaky.

**Committing golden files.** They must be committed to the repository. They are source code, not build artifacts. If they're in `.gitignore`, the next developer to run `go test` without `-update` will get "golden file not found" errors for every case.

**The review step is mandatory.** The entire value of golden files is that you review the diff when you run `-update`. If you mindlessly run `-update` and commit without reviewing, you've just committed a regression as a "golden" baseline. Always `git diff testdata/` before committing updated goldens.

**Large binary or unstable golden files.** If your golden file contains binary data or is extremely large (megabytes), the pattern loses value — diffs become unreadable. Golden files work best for text-format output: JSON, YAML, generated source code, formatted text.

## Key Takeaway

The golden file pattern is the right tool for any function that produces complex, structured output where you care about the exact result, not just a summary. The workflow is: generate once, review, commit, then compare on every test run. The `-update` flag makes intentional changes painless — a single command, a diff review, and a commit. That's it. No more manual string updates, no more "just check it doesn't error" tests for functions that clearly produce meaningful output.

---

[Course Index](/series/go-testing-masterclass) | [← Lesson 6](/post/go/go-testing-mocking) | [Next → Lesson 8: Race-Aware Tests](/post/go/go-testing-race-aware)
