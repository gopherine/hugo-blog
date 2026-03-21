---
title: 'Lesson 11: Nil Slice vs Empty Slice — Same length, different meaning'
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
series: "Idiomatic Go"
lesson: 11
---

Go has two ways to have a slice with zero elements, and they are not the same thing. Developers coming from Python, Ruby, or JavaScript expect an empty collection to just be an empty collection. In Go, the distinction between a nil slice and an empty slice is subtle enough that you can miss it for months — right up until a frontend engineer files a bug because your API is returning `null` instead of `[]`.

## The Problem

The problem isn't that nil slices exist. The problem is that most Go code treats them as interchangeable with empty slices, which they almost are — until they aren't.

```go
// Both have length 0. Both can be appended to. Looks the same, right?
var s []string        // nil slice
s2 := []string{}     // empty slice

fmt.Println(len(s), len(s2))   // 0 0
fmt.Println(s == nil)          // true
fmt.Println(s2 == nil)         // false

// Now marshal them both
b1, _ := json.Marshal(s)
b2, _ := json.Marshal(s2)

fmt.Println(string(b1)) // null   <-- uh oh
fmt.Println(string(b2)) // []
```

That `null` vs `[]` is a real API contract bug. A JavaScript frontend doing `data.items.map(...)` will throw a runtime exception when `items` is `null`. Your Go code compiled and ran fine. Your tests passed. Your users got a 500.

## The Idiomatic Way

The rule I follow: use a nil slice when you're accumulating results in a loop. Use an empty slice at API boundaries where the value will be JSON-encoded.

```go
// Internal — nil slice is idiomatic here. append works on nil.
func findActiveUsers(users []User) []User {
    var result []User
    for _, u := range users {
        if u.Active {
            result = append(result, u)
        }
    }
    return result
}

// API boundary — initialize empty so JSON produces [] not null
func handleListUsers(w http.ResponseWriter, r *http.Request) {
    users := findActiveUsers(allUsers)

    response := struct {
        Users []User `json:"users"`
    }{
        Users: users,
    }

    // Normalize at the boundary
    if response.Users == nil {
        response.Users = []User{}
    }

    json.NewEncoder(w).Encode(response)
}
```

Alternatively, initialize the function return as empty from the start if it's always going to be JSON-encoded:

```go
func getItems(db *sql.DB) []string {
    items := []string{} // not: var items []string

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

The append still works perfectly. The only thing that changed is what gets serialized when the query returns nothing.

## In The Wild

I've seen this bite teams building microservices that aggregate data from multiple sources. Say you have a `/recommendations` endpoint that queries a recommendation engine. If the engine has no results for a new user, the Go handler returns a nil slice, your JSON response looks like `{"recommendations": null}`, and the mobile app — which was written assuming the field is always an array — crashes on first launch for new users.

The fix ends up being a one-liner, but you only find it after a Sentry alert at 2am. Initializing to `[]string{}` at the function that touches the database would have prevented the whole thing.

## The Gotchas

**reflect.DeepEqual distinguishes them in tests.** This one trips people up constantly:

```go
func TestFilter(t *testing.T) {
    got := findActiveUsers([]User{}) // returns nil if no active users
    want := []User{}                 // explicitly empty

    if !reflect.DeepEqual(got, want) {
        t.Errorf("got %v, want %v", got, want) // this FAILS
    }
}
```

`reflect.DeepEqual` treats nil and empty as different. Either compare with `len(got) != len(want)` or be consistent about which form your functions return.

**The nil check you forgot.** Some codebases use nil slices to signal "no data" and empty slices to signal "we checked and there's nothing." That's a valid convention — but only if you enforce it everywhere. Mixed conventions produce code where callers can't trust nil checks:

```go
if users == nil {
    // does this mean "we didn't query" or "we queried and got nothing"?
}
```

Pick one meaning and document it.

**append changes nil to non-nil.** After `s = append(s, item)`, `s` is no longer nil even if it was before. Code that relies on a nil check after appending will get wrong results.

## Key Takeaway

Nil and empty slices are functionally identical for most operations in Go — ranging, appending, passing to functions. The difference only matters at two boundaries: JSON serialization and nil checks. My default is to use nil slices internally (they're idiomatic with append loops) and normalize to empty slices at the edges of your system before any marshaling happens. If you're writing a function whose output will eventually be JSON-encoded, just initialize with `[]T{}` and save yourself the debugging session.

---

← [Lesson 10: Zero Values Are Useful](/post/go/go-idioms-zero-values) | [Course Index](#) | [Lesson 12: Value vs Pointer Receivers](/post/go/go-idioms-value-vs-pointer-receivers) →
