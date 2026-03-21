---
title: "Lesson 22: File I/O — Reading and writing files the Go way"
author: "Atharva Pandey"
series: "Go from Scratch"
lesson: 22
keywords: ["Go", "golang", "file I/O", "os.Open", "os.Create", "bufio.Scanner", "os.ReadFile", "os.WriteFile", "beginner"]
tags: ["Go tutorial", "golang", "beginner"]
date: "2024-04-26T00:00:00.000Z"
---

At some point every program needs to persist something — a config file, a log, a data export. File I/O is one of those topics that sounds tedious but is genuinely satisfying once it clicks. Go gives you a few different layers to work with, from the low-level `os` package to convenient one-liners. I'll show you all of them so you can pick the right tool for the job.

---

## The Basics

### Reading a file the simple way

For most cases where the file is small enough to fit in memory, `os.ReadFile` is all you need:

```go
package main

import (
    "fmt"
    "os"
)

func main() {
    data, err := os.ReadFile("hello.txt")
    if err != nil {
        fmt.Println("could not read file:", err)
        return
    }

    fmt.Println(string(data))
}
```

`os.ReadFile` opens the file, reads everything, and closes it — all in one call. It returns `[]byte`, so we convert to `string` to print it. If the file doesn't exist you get a clear error. Easy.

### Writing a file the simple way

The mirror image is `os.WriteFile`:

```go
package main

import (
    "fmt"
    "os"
)

func main() {
    content := []byte("Hello from Go!\nSecond line here.\n")

    err := os.WriteFile("output.txt", content, 0644)
    if err != nil {
        fmt.Println("could not write file:", err)
        return
    }

    fmt.Println("File written successfully.")
}
```

The third argument `0644` is the Unix file permission. `0644` means the owner can read and write; everyone else can only read. If the file already exists, `WriteFile` overwrites it completely. If you just need to dump something to disk quickly, this is the function to reach for.

### Opening files manually with os.Open and os.Create

When you want more control — reading line by line, appending instead of overwriting, or handling very large files — you open the file yourself:

```go
package main

import (
    "bufio"
    "fmt"
    "os"
)

func main() {
    file, err := os.Open("hello.txt")
    if err != nil {
        fmt.Println("could not open file:", err)
        return
    }
    defer file.Close()

    scanner := bufio.NewScanner(file)
    lineNum := 1
    for scanner.Scan() {
        fmt.Printf("Line %d: %s\n", lineNum, scanner.Text())
        lineNum++
    }

    if err := scanner.Err(); err != nil {
        fmt.Println("scanner error:", err)
    }
}
```

`os.Open` opens a file for reading only. `defer file.Close()` is placed immediately after the error check — this is a convention in Go that ensures the file is always closed when the function returns, no matter what happens.

`bufio.NewScanner` wraps the file in a buffered reader. Each call to `scanner.Scan()` advances to the next line and returns `true` as long as there are lines to read. `scanner.Text()` gives you the current line as a string, without the trailing newline.

### Writing with os.Create and fmt.Fprintf

To create a new file (or truncate an existing one) for writing:

```go
package main

import (
    "fmt"
    "os"
)

func main() {
    file, err := os.Create("log.txt")
    if err != nil {
        fmt.Println("could not create file:", err)
        return
    }
    defer file.Close()

    fmt.Fprintf(file, "User: %s\n", "Atharva")
    fmt.Fprintf(file, "Score: %d\n", 99)
    fmt.Fprintf(file, "Status: %s\n", "active")

    fmt.Println("Log written.")
}
```

`fmt.Fprintf` is just like `fmt.Printf` but it writes to any `io.Writer` instead of standard output. Since `*os.File` implements `io.Writer`, you can pass it directly. This is clean and readable — one line of code per thing you want to write.

### Appending to an existing file

`os.Create` always starts fresh. To append without overwriting, use `os.OpenFile` with flags:

```go
file, err := os.OpenFile("log.txt", os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
```

`os.O_APPEND` positions the write cursor at the end. `os.O_CREATE` creates the file if it doesn't exist. `os.O_WRONLY` opens it write-only. The flags are combined with `|` (bitwise OR). After that, you write to `file` exactly the same way as before.

---

## Try It Yourself

**Exercise 1:** Write a program that creates a file called `numbers.txt` and writes the numbers 1 through 10, one per line using a loop.

**Exercise 2:** Write a program that reads `numbers.txt` back, parses each line as an integer using `strconv.Atoi`, and prints the sum.

**Exercise 3:** Write a simple logger that appends a timestamped message to `app.log` every time it's called. Use `time.Now().Format(time.RFC3339)` for the timestamp. Run the program three times and verify all entries are in the file.

---

## Common Mistakes

**Forgetting defer file.Close()**

If you open a file but never close it, you hold onto a file descriptor until your program exits. On short programs this doesn't matter. On long-running services it will eventually exhaust the operating system's file descriptor limit. Always `defer file.Close()` right after you confirm the file opened successfully.

**Calling defer before checking the error**

This is a subtle one I've made myself:

```go
file, err := os.Open("hello.txt")
defer file.Close() // WRONG — file is nil if err != nil
if err != nil { ... }
```

If `os.Open` fails, `file` is `nil`, and calling `file.Close()` on nil will panic. Always check the error first, then defer.

**Ignoring scanner.Err() after the loop**

`scanner.Scan()` returns false both when the file ends normally and when it hits an error. If you don't call `scanner.Err()` after the loop, you'll silently swallow read errors. It's a two-line habit worth building.

**Assuming os.ReadFile works for huge files**

`os.ReadFile` loads the whole file into memory at once. For a 10 MB config file, that's fine. For a 10 GB log file, you'll run out of memory fast. Use `bufio.Scanner` or `bufio.Reader` when the file might be large — they read in chunks.

---

## Key Takeaway

Go gives you two levels for file I/O: the convenience functions `os.ReadFile` and `os.WriteFile` for simple cases, and the manual `os.Open`/`os.Create` with `bufio.Scanner` and `fmt.Fprintf` for when you need more control. Whichever level you use, the pattern is always the same — open, defer close, do your work, check errors. Get comfortable with `defer file.Close()` being the very next line after you open a file, and you'll avoid most of the pain this topic can cause.

---

*[← Previous: Lesson 21 — JSON and HTTP](/post/go/go-scratch-json) | [Course Index: Go from Scratch](/series/go-from-scratch) | [Next: Lesson 23 — The Type System →](/post/go/go-scratch-type-system)*
