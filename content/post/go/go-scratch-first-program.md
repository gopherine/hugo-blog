---
title: "Lesson 1: Your First Go Program — Hello World is just the beginning"
author: Atharva Pandey
series: "Go from Scratch"
lesson: 1
keywords: [Go, golang, beginner, hello world, go install, go run, main package]
tags: [Go tutorial, golang, beginner]
date: '2024-01-03T00:00:00.000Z'
---

I remember the first time I ran a Go program. The compiler yelled at me for an unused import, and I thought, "Okay, this language has opinions." That's not a bad thing. Go's strictness is part of what makes it so readable and maintainable. Once you understand *why* Go works the way it does, it starts to feel less like friction and more like clarity.

In this lesson you're going to write your first Go program, understand every single line of it, and learn the basic tools you'll use every day. By the end, "Hello, World!" won't feel like a throwaway demo — it'll feel like a foundation.

---

## The Basics

### Installing Go

Before we write a single line, you need Go on your machine. Head to [go.dev/dl](https://go.dev/dl) and download the installer for your operating system. Run it, and when you're done, open a terminal and type:

```
go version
```

You should see something like `go version go1.22.0 darwin/amd64`. If you do, you're ready.

### Creating your first file

Go source files end in `.go`. Create a new folder somewhere — let's call it `hello` — and inside it create a file called `main.go`. Open it in any text editor and type this out (don't copy-paste yet, typing it builds muscle memory):

```go
package main

import "fmt"

func main() {
    fmt.Println("Hello, World!")
}
```

Now in your terminal, navigate to that folder and run:

```
go run main.go
```

You should see `Hello, World!` printed in your terminal. That's it — you just ran your first Go program. Let's talk about what every line actually does.

### Breaking it down, line by line

**`package main`**

Every Go file must start by declaring which *package* it belongs to. A package is just a way to group related code together — think of it like a namespace or a module in other languages.

The name `main` is special. When Go sees `package main`, it knows this file is meant to be an executable program (something you can actually run), not a library that other code imports. If you forget this line or name it something else, `go run` won't know where to start.

**`import "fmt"`**

This brings in the `fmt` package from Go's standard library. `fmt` stands for "format" and it gives you functions for printing output and reading input. You need to import every external package you use — Go won't let you use something you haven't explicitly imported. It also won't let you *import* something you don't use. Yes, an unused import is a compile error. This feels annoying at first, but it keeps your code tidy.

**`func main()`**

`func` is how you declare a function in Go. `main` is another special name — it's the entry point of your program. When you run a Go executable, it starts executing from the `main` function inside the `main` package. The empty parentheses `()` mean this function takes no parameters.

**`fmt.Println("Hello, World!")`**

This calls the `Println` function from the `fmt` package. `Println` prints a line of text followed by a newline character. The dot `.` is how you access something that belongs to a package — `fmt.Println` means "the `Println` function inside the `fmt` package."

The text `"Hello, World!"` is a *string* — a sequence of characters wrapped in double quotes. In Go, you always use double quotes for strings (single quotes are for something else entirely, which we'll cover later).

### `go run` vs `go build`

You've used `go run`. It compiles and immediately runs your program in one step — perfect for development. But if you want a binary you can distribute or run later without the Go toolchain, use:

```
go build -o hello main.go
```

This produces an executable file called `hello` (or `hello.exe` on Windows). Run it directly:

```
./hello
```

Same output, but now it's a standalone binary. Go compiles to a single binary with no runtime dependencies — this is one of Go's superpowers when it comes to deployment.

---

## Try It Yourself

Modify your `main.go` to print three things:

1. Your name
2. What language you're coming from
3. Why you're learning Go

Something like:

```go
package main

import "fmt"

func main() {
    fmt.Println("My name is Atharva.")
    fmt.Println("I'm coming from Python.")
    fmt.Println("I'm learning Go because it's fast and simple.")
}
```

Run it with `go run main.go`. Notice you can call `fmt.Println` as many times as you want — each call prints on its own line.

Now try using `fmt.Print` instead of `fmt.Println` for one of the lines. What's different? (`Print` doesn't add a newline at the end, so the next print continues on the same line.)

---

## Common Mistakes

**Forgetting `package main`**

If you delete that first line and run `go run`, you'll get an error about a missing package declaration. Every Go file needs it.

**Unused imports**

Try adding `import "os"` to your file without using anything from `os`. Go will refuse to compile with the error `"os" imported and not used`. This is a feature, not a bug — it prevents you from accumulating dead code. Delete unused imports immediately.

**Wrong quote style**

Coming from Python where single and double quotes are interchangeable? In Go, strings use double quotes. Single quotes (`'`) are for *rune literals* — a single Unicode character — which we'll cover when we talk about types. Using single quotes around a string will give you a cryptic error.

**Calling `Println` without the `fmt.` prefix**

`Println` doesn't exist on its own in Go. It lives inside the `fmt` package. You must write `fmt.Println`. If you try just `Println("hello")`, Go will tell you it's undefined.

---

## Key Takeaway

Every Go program starts with `package main`, imports only what it uses, and runs from a `func main()` entry point. The Go toolchain enforces these rules strictly — an unused import or a missing package declaration is a compile error, not a warning. This strictness might feel heavy-handed when you're just starting out, but it's what keeps Go codebases clean and readable even as they grow to hundreds of thousands of lines. You now understand every line of a Go program, not just how to copy it.

---

*[Course Index: Go from Scratch](/series/go-from-scratch) | [Next: Lesson 2 — Variables and Types →](/post/go/go-scratch-variables-types)*
