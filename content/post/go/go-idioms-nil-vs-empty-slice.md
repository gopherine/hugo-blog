---
title: 'Go Idioms: Nil Slice vs Empty Slice'
author: Atharva Pandey
keywords:
  - Go
  - golang
  - nil slice
  - empty slice
  - slice idioms
  - json marshal
tags:
  - Go tutorial
  - golang
date: '2025-11-03T00:00:00.000Z'
---
"wait, what?" moments in Go is discovering that there are two ways to have a slice with zero elements, and they are not the same thing. This surprises developers coming from languages where an empty collection is just an empty collection. In Go, the difference between a nil slice and an empty slice is subtle, but it shows up in real code in ways that bite people.

Let's break down exactly what is happening, where it matters, and when to use each one.

## The Two Ways to Have a Zero-Length Slice

```go
// Nil slice — declared but not initialized
var s []int

// Empty slice — explicitly initialized with no elements
s2 := []int{}

// Also an empty slice
s3 := make([]int, 0)
```

Both `s` and `s2` have a length of zero and a capacity of zero. You can call `len()` and `cap()` on either one and get back `0`. Appending to either one works identically. At first glance they seem interchangeable, and that is exactly why the distinction trips people up.

## Where They Actually Differ

### The nil check

This is the most obvious difference:

```go
var s []int
s2 := []int{}

fmt.Println(s == nil)  // true
fmt.Println(s2 == nil) // false
```

A nil slice is, well, nil. An empty slice is a real slice value that happens to have no elements. If some function in your codebase returns a nil slice to signal "nothing found" and something else returns an empty slice to signal the same thing, you will have inconsistent nil checks scattered throughout the code. Pick one convention and stick to it.

### JSON encoding — the sneaky one

This is where the difference really matters in production. JSON encoding treats nil slices and empty slices differently:

```go
// WRONG — or at least surprising
type Response struct {
    Items []string
}

r1 := Response{} // Items is nil
r2 := Response{Items: []string{}} // Items is empty

b1, _ := json.Marshal(r1)
b2, _ := json.Marshal(r2)

fmt.Println(string(b1)) // {"Items":null}
fmt.Println(string(b2)) // {"Items":[]}
```

That `null` vs `[]` difference is a real API contract issue. If you are returning a list of results from an API endpoint and there are no results, most API consumers expect `[]`, not `null`. A frontend developer parsing `null` as a list will get a runtime error. The fix is to initialize with an empty slice explicitly when you know you are building a response that will be JSON-encoded:

```go
// RIGHT — when building API responses
func getItems(db *sql.DB) []string {
    items := []string{} // not var items []string

    rows, err := db.Query("SELECT name FROM items")
    if err != nil {
        return items // returns [], not null
    }
    defer rows.Close()

    for rows.Next() {
        var name string
        rows.Scan(&name)
        items = append(items, name)
    }

    return items
}
```

## Append Works on Both — This Is Intentional

One thing that confuses newcomers is that `append` happily works on a nil slice. There is no nil pointer panic here:

```go
var s []int

// This is perfectly fine
s = append(s, 1, 2, 3)

fmt.Println(s)    // [1 2 3]
fmt.Println(s == nil) // false — append returned a new slice
```

Go's runtime handles the nil case in `append` by allocating a new backing array. This means the common pattern of declaring a nil slice and then building it up with `append` in a loop is totally idiomatic Go. You do not need to initialize with `make` first unless you know the capacity ahead of time.

```go
// Idiomatic — no pre-initialization needed
func filterEven(nums []int) []int {
    var result []int // nil slice, that's fine
    for _, n := range nums {
        if n%2 == 0 {
            result = append(result, n)
        }
    }
    return result
}
```

If there are no even numbers, `result` stays nil. Whether that is the right behavior depends on whether the caller will JSON-encode the result or just range over it.

## The reflect.DeepEqual Gotcha

Here is one that shows up in tests:

```go
// WRONG — this test will fail
func TestFilter(t *testing.T) {
    got := filterEven([]int{1, 3, 5})
    want := []int{}

    if !reflect.DeepEqual(got, want) {
        t.Errorf("got %v, want %v", got, want)
    }
}
```

`reflect.DeepEqual` distinguishes between nil and empty slices. If `filterEven` returns a nil slice (because it found nothing and used `var result []int`) and you compare it to `[]int{}`, the test fails even though both have zero elements and both behave the same way in every practical sense.

The fix is to be consistent. Either:

```go
// Option A: compare against nil
want := []int(nil)

// Option B: use len check instead of DeepEqual
if len(got) != len(want) {
    t.Errorf(...)
}

// Option C: initialize result as empty slice in filterEven
var result = []int{}
```

Option C is often the cleanest for functions that are returning data to callers — initialize as empty so the behavior is predictable regardless of how many items were found.

## When to Use Which

Here is a practical mental model:

**Use a nil slice (`var s []int`) when:**
- You are accumulating results in a loop and will use `append` to build the slice. The nil start is idiomatic.
- The nil state has meaning in your domain — for example, "the field was not set" versus "the field was set to an empty list."
- You are writing internal logic where JSON encoding is not involved.

**Use an empty slice (`s := []int{}` or `make([]int, 0)`) when:**
- The value will be JSON-encoded and you want `[]` instead of `null`.
- You are writing a function that returns a collection and want callers to get a consistent, non-nil value.
- You are working with a library or protocol that differentiates between null and empty.

```go
// Practical example: domain layer vs API layer

// Internal — nil slice is fine here
func findMatchingUsers(users []User, role string) []User {
    var result []User
    for _, u := range users {
        if u.Role == role {
            result = append(result, u)
        }
    }
    return result
}

// API handler — initialize empty to control JSON output
func handleListUsers(w http.ResponseWriter, r *http.Request) {
    users := findMatchingUsers(allUsers, r.URL.Query().Get("role"))

    response := struct {
        Users []User `json:"users"`
    }{
        Users: users,
    }

    // If users is nil, JSON will produce {"users":null}
    // Fix: normalize at the boundary
    if response.Users == nil {
        response.Users = []User{}
    }

    json.NewEncoder(w).Encode(response)
}
```

## The Core Mental Model

Think of a nil slice as the zero value of the slice type — it exists, it is valid, you can use it, but it has not been explicitly created. An empty slice has been explicitly created; someone said "I want a list with zero items."

In most loop-and-append patterns, nil slices are perfectly idiomatic. At API boundaries — especially JSON — normalize to an empty slice to avoid surprising consumers. Keep that distinction in mind and you will avoid most of the headaches this topic causes.
