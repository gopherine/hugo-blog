---
title: "Lesson 21: JSON and HTTP — Reading and writing the web's language"
author: "Atharva Pandey"
series: "Go from Scratch"
lesson: 21
keywords: ["Go", "golang", "JSON", "encoding/json", "net/http", "Marshal", "Unmarshal", "struct tags", "HTTP request", "beginner"]
tags: ["Go tutorial", "golang", "beginner"]
date: "2024-04-21T00:00:00.000Z"
---

Almost every program I write these days talks to something over HTTP — a third-party API, a database service, a webhook. And almost everything speaks JSON. So learning how Go handles JSON and HTTP isn't an advanced topic; it's one of the most practical skills you can pick up early.

In this lesson I'll walk you through converting Go structs to JSON and back, decoding JSON from an API response, and making real HTTP requests — all using only Go's standard library. No third-party packages needed.

---

## The Basics

### Structs and struct tags

Go's `encoding/json` package works by looking at your struct fields. You control how they appear in JSON using *struct tags* — little annotations that sit next to each field.

```go
package main

import (
    "encoding/json"
    "fmt"
)

type User struct {
    Name  string `json:"name"`
    Email string `json:"email"`
    Age   int    `json:"age"`
}

func main() {
    u := User{Name: "Atharva", Email: "hi@example.com", Age: 28}

    data, err := json.Marshal(u)
    if err != nil {
        fmt.Println("error:", err)
        return
    }

    fmt.Println(string(data))
    // Output: {"name":"Atharva","email":"hi@example.com","age":28}
}
```

`json.Marshal` takes any value and returns `[]byte` — a slice of bytes representing the JSON. We call `string(data)` to print it as readable text.

The struct tags `json:"name"` tell the encoder to use `"name"` as the key instead of the Go field name `Name`. If you omit the tag, Go uses the field name as-is, including the capital letter. Tags keep your JSON looking clean and lowercase.

There's a handy extra option: `json:"age,omitempty"` will leave the field out of the JSON entirely if its value is the zero value (0 for int, "" for string, nil for a pointer). Very useful when you don't want to send empty fields to an API.

### Unmarshalling JSON into a struct

Going the other way — turning JSON bytes into a Go struct — uses `json.Unmarshal`:

```go
package main

import (
    "encoding/json"
    "fmt"
)

type Product struct {
    ID    int    `json:"id"`
    Title string `json:"title"`
    Price float64 `json:"price"`
}

func main() {
    raw := `{"id":42,"title":"Go Programming Book","price":29.99}`

    var p Product
    err := json.Unmarshal([]byte(raw), &p)
    if err != nil {
        fmt.Println("error:", err)
        return
    }

    fmt.Printf("Product: %s costs $%.2f\n", p.Title, p.Price)
    // Output: Product: Go Programming Book costs $29.99
}
```

Notice two things: we pass a pointer `&p` to `Unmarshal` (because it needs to modify `p`), and we convert the string to `[]byte` using `[]byte(raw)`. If the JSON has extra fields your struct doesn't know about, Go quietly ignores them — no error.

### Streaming with json.NewEncoder and json.NewDecoder

`Marshal` and `Unmarshal` work on byte slices held in memory. When you're writing to an `http.ResponseWriter` or reading from an `http.Response.Body`, it's more efficient to stream using an encoder or decoder directly:

```go
// Writing JSON to a response writer (in an HTTP handler)
json.NewEncoder(w).Encode(myStruct)

// Reading JSON from an HTTP response body
var result MyStruct
json.NewDecoder(resp.Body).Decode(&result)
```

The encoder writes directly to any `io.Writer` without buffering the whole thing in memory first. The decoder reads from any `io.Reader` the same way. For large payloads this matters; for small ones either approach works fine.

### Making HTTP requests with net/http

Go's `net/http` package includes a ready-to-use HTTP client. A simple GET request looks like this:

```go
package main

import (
    "encoding/json"
    "fmt"
    "net/http"
)

type Post struct {
    UserID int    `json:"userId"`
    ID     int    `json:"id"`
    Title  string `json:"title"`
    Body   string `json:"body"`
}

func main() {
    resp, err := http.Get("https://jsonplaceholder.typicode.com/posts/1")
    if err != nil {
        fmt.Println("request failed:", err)
        return
    }
    defer resp.Body.Close()

    var post Post
    if err := json.NewDecoder(resp.Body).Decode(&post); err != nil {
        fmt.Println("decode failed:", err)
        return
    }

    fmt.Printf("Title: %s\n", post.Title)
}
```

The pattern is always: make the request → check the error → defer closing the body → decode the body. The `defer resp.Body.Close()` is important — it ensures the connection is released back to the pool even if something goes wrong later.

---

## Try It Yourself

**Exercise 1:** Define a `City` struct with fields `Name`, `Country`, and `Population`. Create an instance, marshal it to JSON with lowercase keys, and print the result.

**Exercise 2:** Take this JSON string and unmarshal it into a matching struct:

```json
{"username":"gopher","score":1500,"active":true}
```

Then print each field individually.

**Exercise 3:** Hit the free API at `https://jsonplaceholder.typicode.com/users/1`, decode the response into a struct that captures at least `id`, `name`, and `email`, and print a greeting like "Hello, Leanne Graham! Your email is Sincere@april.biz."

---

## Common Mistakes

**Forgetting the pointer in Unmarshal**

`json.Unmarshal(data, p)` will not work — you must pass `&p`. Without the pointer, Unmarshal can't modify your struct. Go won't crash; it just won't change anything, and you'll be left wondering why your struct is empty.

**Not closing the response body**

`resp.Body` is an open network connection. If you never close it, you leak that connection. Always `defer resp.Body.Close()` immediately after checking the error from `http.Get` or `http.Do`.

**Unexported fields are invisible to the JSON package**

The `encoding/json` package can only see exported (capitalised) fields. A field named `name` (lowercase) will always be ignored, even if you give it a tag. If your struct field is lowercase, your JSON will silently be missing that data.

**Ignoring the HTTP status code**

`http.Get` returns `nil` for `err` even when the server responds with a 404 or 500. Always check `resp.StatusCode` after checking the error:

```go
if resp.StatusCode != http.StatusOK {
    fmt.Println("unexpected status:", resp.Status)
    return
}
```

---

## Key Takeaway

Go's `encoding/json` and `net/http` packages handle the two most common tasks in modern programs — serialising data and talking to the outside world — without any extra dependencies. The core pattern to memorise is: define a struct, add `json:"..."` tags, marshal to send, unmarshal to receive, and always close the response body. Once this pattern is in your fingers, you can consume or produce any JSON API Go encounters.

---

*[← Previous: Lesson 20 — Testing](/post/go/go-scratch-testing) | [Course Index: Go from Scratch](/series/go-from-scratch) | [Next: Lesson 22 — File I/O →](/post/go/go-scratch-files)*
