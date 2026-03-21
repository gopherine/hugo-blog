---
title: "Lesson 20: Testing in Go — Every Go file gets a test file"
author: Atharva Pandey
series: "Go from Scratch"
lesson: 20
keywords: [Go, golang, beginner, testing, test file, TestXxx, t.Error, t.Fatal, t.Run, table driven tests, go test, coverage]
tags: [Go tutorial, golang, beginner]
date: '2024-04-17T00:00:00.000Z'
---

I used to think testing was something you did after you finished writing code — a checkbox you ticked before committing. Go changed that attitude for me, not by lecturing me about best practices, but by making testing so straightforward that there was no excuse not to do it. The tooling is built in, the conventions are clear, and writing a test in Go takes about as long as writing the function itself. After a few weeks with Go, I started writing tests alongside my code, then slightly before it. The feedback loop became addictive.

In this lesson we'll go from the very first test file all the way to table-driven tests and coverage reports. Everything here uses the standard library — no third-party testing frameworks needed.

---

## The Basics

### `_test.go` files

Go's test tooling looks for files whose names end in `_test.go`. These files are compiled and run only when you execute `go test`. They don't get included in your regular binary. The convention is to create one test file per source file: `mathutil.go` gets `mathutil_test.go`, `greet.go` gets `greet_test.go`.

```
mathutil/
├── mathutil.go
└── mathutil_test.go
```

Test files typically declare the same package as the code they test (so they can access unexported names), though you can use a `_test` suffix on the package name (`package mathutil_test`) to test only the exported API, like an external user would.

### `func TestXxx(t *testing.T)`

Every test function must:
1. Start with the word `Test` followed by a name that starts with a capital letter
2. Take exactly one parameter: `t *testing.T`
3. Return nothing

```go
// mathutil/mathutil_test.go
package mathutil

import "testing"

func TestAbs(t *testing.T) {
    result := Abs(-5)
    if result != 5 {
        t.Errorf("Abs(-5) = %d; want 5", result)
    }
}
```

Run the tests with:

```
go test ./...
```

The `./...` pattern means "this directory and all subdirectories." You'll see output like:

```
ok      example.com/myapp/mathutil  0.001s
```

### `t.Error`, `t.Errorf`, `t.Fatal`, `t.Fatalf`

There are two categories of failure functions:

- **`t.Error` / `t.Errorf`**: marks the test as failed and logs a message, but lets the test function continue running. Use this when you want to collect multiple failures in one run.
- **`t.Fatal` / `t.Fatalf`**: marks the test as failed, logs a message, and immediately stops the current test function. Use this when continuing makes no sense — for example, if the function returned an unexpected error and the rest of the test would panic trying to use the nil result.

```go
func TestClamp(t *testing.T) {
    got, err := ClampWithError(15, 0, 10)
    if err != nil {
        t.Fatalf("unexpected error: %v", err) // stop here if there's an error
    }
    if got != 10 {
        t.Errorf("ClampWithError(15, 0, 10) = %d; want 10", got)
    }
}
```

The `f` variants work like `fmt.Sprintf` — you pass a format string and arguments. Prefer them over `t.Error("got " + strconv.Itoa(got))` — the formatting is cleaner and the output is more readable.

### `t.Run` — subtests

`t.Run` lets you define named subtests inside a single test function. Each subtest gets its own name, its own pass/fail status, and can be run individually.

```go
func TestAbs(t *testing.T) {
    t.Run("negative number", func(t *testing.T) {
        if Abs(-5) != 5 {
            t.Errorf("got %d; want 5", Abs(-5))
        }
    })

    t.Run("positive number", func(t *testing.T) {
        if Abs(3) != 3 {
            t.Errorf("got %d; want 3", Abs(3))
        }
    })

    t.Run("zero", func(t *testing.T) {
        if Abs(0) != 0 {
            t.Errorf("got %d; want 0", Abs(0))
        }
    })
}
```

When one subtest fails, the output tells you exactly which case broke: `--- FAIL: TestAbs/negative_number`. You can also run a specific subtest by name: `go test -run TestAbs/negative_number`.

### Table-driven tests

Once you have `t.Run`, the next step is to express a group of related test cases as a table — a slice of structs. This is the most common test pattern in the Go standard library and most professional Go codebases.

```go
func TestAbs(t *testing.T) {
    tests := []struct {
        name  string
        input int
        want  int
    }{
        {"negative", -5, 5},
        {"positive", 3, 3},
        {"zero", 0, 0},
        {"large negative", -1000, 1000},
    }

    for _, tc := range tests {
        t.Run(tc.name, func(t *testing.T) {
            got := Abs(tc.input)
            if got != tc.want {
                t.Errorf("Abs(%d) = %d; want %d", tc.input, got, tc.want)
            }
        })
    }
}
```

The advantage is clarity: you can see every test case at a glance, add new cases by appending a line to the slice, and all the test scaffolding stays in one place. Adding a new edge case doesn't mean writing a new function.

### `go test ./...` — running all tests

```
go test ./...          # run all tests
go test -v ./...       # verbose: show each test's name and result
go test -run TestAbs   # run only tests matching the pattern
go test -count=1 ./... # disable test caching
```

By default, `go test` caches results. If you run the same tests with no code changes, it shows the cached result instantly. Pass `-count=1` to force a fresh run.

### Test coverage with `-cover`

```
go test -cover ./...
```

This prints a coverage percentage for each package:

```
ok      example.com/myapp/mathutil  0.001s  coverage: 87.5% of statements
```

For a detailed breakdown of which lines are covered, use:

```
go test -coverprofile=coverage.out ./...
go tool cover -html=coverage.out
```

The second command opens a browser showing your source files with covered lines in green and uncovered lines in red. It's surprisingly motivating to see a large uncovered block and immediately know what to test next.

Don't chase 100% coverage mechanically — some code (like error paths for "this should never happen" conditions) isn't worth testing. Aim for coverage on your core logic and anything with branching behaviour.

---

## Try It Yourself

Take the `mathutil` package from lesson 16 (or any simple package you've written) and add a `_test.go` file. Write at least three test cases for each exported function using the table-driven pattern. Run `go test -v ./...` and verify all tests pass.

Then deliberately introduce a bug in one function — say, return `-n` instead of `n` from `Abs`. Run the tests again and confirm the right test case fails with a clear message.

Finally, run `go test -cover ./...` and see what percentage of your code the tests exercise.

---

## Common Mistakes

**Test function name doesn't start with capital letter after `Test`**

`func Testabs(t *testing.T)` won't be recognized as a test. It needs to be `func TestAbs(t *testing.T)` — capital letter after the word `Test`.

**Using `t.Fatal` when you mean `t.Error`**

If you `t.Fatal` on the first failure in a table-driven test loop, the rest of the table cases never run. Use `t.Errorf` inside the loop body so all cases execute, and you see all failures at once.

**Not using `t.Run` for table tests**

Without `t.Run`, a failure in case 3 of 10 gives you output like `TestAbs failed`. With `t.Run`, you get `TestAbs/negative` failed. The name makes the failure instantly actionable.

**Putting test files in a different package without a reason**

`package mathutil_test` (the external test package) is useful for testing only the public API, but it means your tests can't access unexported helpers. Start with the same package name and switch to the external style only when you want to enforce API boundary discipline.

---

## Key Takeaway

Testing in Go is built into the language toolchain. Test files end in `_test.go`, test functions follow the `TestXxx(t *testing.T)` signature, and you run everything with `go test ./...`. Use `t.Errorf` for non-fatal failures that let the test continue, `t.Fatalf` when continuing is pointless, and `t.Run` to give your test cases clear names. Table-driven tests are the standard idiom for exercising multiple inputs against the same function. Add `-cover` to see how much of your code your tests actually execute. Write tests as you write code — not after.

---

*[Course Index: Go from Scratch](/series/go-from-scratch) | [← Lesson 19: Context](/post/go/go-scratch-context) | [Lesson 21: JSON in Go →](/post/go/go-scratch-json)*
