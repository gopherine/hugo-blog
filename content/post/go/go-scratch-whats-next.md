---
title: "Lesson 25: What's Next — Your roadmap from beginner to production Go"
author: "Atharva Pandey"
series: "Go from Scratch"
lesson: 25
keywords: ["Go", "golang", "roadmap", "next steps", "Effective Go", "Go Blog", "concurrency", "idiomatic Go", "beginner", "learning path"]
tags: ["Go tutorial", "golang", "beginner"]
date: "2024-05-11T00:00:00.000Z"
---

You made it. Twenty-five lessons ago you didn't know what `package main` meant. Now you understand variables, types, functions, error handling, interfaces, goroutines, testing, JSON, file I/O, the type system, and the entire toolchain. That is not beginner knowledge anymore.

But there's a gap between knowing the language and writing Go the way experienced Go developers write it. This lesson is about that gap — what it is, how to close it, and what to build while you do.

---

## The Basics

### Where you are right now

You can write Go programs that work. You understand the syntax, the standard library basics, and how to structure a project. That puts you ahead of most people who say they want to learn Go but never get past "Hello, World."

What you don't yet have is *feel*. That intuition for how to structure a package, when to use an interface vs a concrete type, how to handle errors in a way that gives callers useful information, how to design an API that's pleasant to use. That comes from reading more Go, writing more Go, and having your code reviewed by people who know Go well.

The good news is there's a clear path.

### Course 2: Idiomatic Go

The next course I'd recommend is focused on writing Go the way the language was intended to be written. Idiomatic Go is about more than just formatting — it's about understanding why the standard library is designed the way it is and applying those same principles to your own code.

Key topics that course covers:

- Error wrapping with `fmt.Errorf` and `%w` — giving errors context without hiding them
- Designing interfaces that are small and composable (the io.Reader/io.Writer philosophy)
- Using the `context` package to propagate cancellation and deadlines
- Writing table-driven tests properly
- Knowing when `nil` is a valid return value and when it isn't

If you start [Idiomatic Go](/post/go/go-idioms-error-handling/) after this, you'll find most of the concepts feel familiar — you have the vocabulary now. The course just teaches you to use them with more precision.

### Course 3: Concurrency in Go

Go's concurrency model — goroutines and channels — is what makes the language genuinely special for building servers and pipelines. You got a taste of it in this course. The concurrency course goes deep:

- The sync package: Mutex, RWMutex, WaitGroup, Once
- Channel directions and the pipeline pattern
- Context cancellation in concurrent code
- Worker pools and fan-out/fan-in patterns
- The race detector and how to use it to find bugs

Concurrency is one of those topics that's best learned after you're comfortable with everything else. If you try to learn goroutines and channels while still getting comfortable with interfaces and error handling, it's too much at once. You're at exactly the right point now.

### Course 4: Error Design

This is a focused course on error handling done properly — one of Go's most distinctive and most misunderstood features. It covers sentinel errors, custom error types, the `errors.As`/`errors.Is` API, when to panic vs when to return an error, and how to design error hierarchies that make debugging a pleasure rather than a nightmare.

### Course 5: API Design in Go

Building an HTTP API in Go is where most people eventually land. This course covers the `net/http` package in depth, routing, middleware, request validation, structured logging, and connecting to a database. By the end you'll have a production-grade REST API that you can actually deploy.

### Required reading

**Effective Go** (go.dev/doc/effective_go) is the canonical guide to writing idiomatic Go, written by the Go team. It's not a tutorial — it assumes you already know the language, which you now do. Read it once, bookmark it, and come back when you encounter patterns you don't recognise. It's short enough to read in an afternoon.

**The Go Blog** (go.dev/blog) is where the Go team publishes deep dives on everything from language changes to best practices. Some articles I'd start with:

- "Error handling and Go" — the original articulation of how Go error handling is meant to work
- "Go Concurrency Patterns: Pipelines and cancellation" — the best intro to real concurrent Go
- "The Laws of Reflection" — for when you eventually need to understand `reflect`
- "Using Go Modules" — the definitive guide to `go.mod` and `go.sum`

**The Go standard library source code** is surprisingly readable and follows its own idioms perfectly. When you're unsure how to implement something, look at how the standard library does it. The `net/http`, `encoding/json`, and `bufio` packages are all excellent examples of Go design.

### Community resources

**Gophers Slack** (invite at gophersinvite.com) has channels for beginners, job hunting, and every major framework. The `#golang-newbies` channel is friendly and active.

**Go Forum** (forum.golangbridge.org) is the mailing-list-style community for longer discussions.

**r/golang** on Reddit has a daily "newbie questions" thread where no question is too basic.

For staying up to date: **Golang Weekly** (golangweekly.com) is a newsletter that curates the best articles, releases, and discussions every week. Subscribe to it and you'll absorb a lot just from the headlines.

### Build something real

No amount of reading replaces building. Here's a progression of projects that will cement everything you've learned, in order of difficulty:

1. **A command-line tool** that reads from stdin, processes text, and writes to stdout. Simple but forces you to handle real I/O and errors.

2. **A JSON REST API** with two or three endpoints, backed by an in-memory store first (a map protected by a mutex), then a real database later.

3. **A file processor** — something that reads a directory of CSV or JSON files, processes them concurrently using goroutines, and writes a summary report.

4. **A scraper or API client** that hits a real external API, handles pagination, and stores the results somewhere.

Each of these will expose gaps in your knowledge that no lesson can predict. Fill those gaps as you hit them, and you'll grow faster than any structured course can take you.

---

## Try It Yourself

**Exercise 1:** Go to go.dev/doc/effective_go right now and read the first three sections: Formatting, Commentary, and Names. These three sections alone will improve your Go immediately.

**Exercise 2:** Look at the source of a small standard library package — try `strings` or `strconv`. Read two or three functions and notice the style: how errors are returned, how the code is commented, how functions are named. You'll see many patterns from this course in action.

**Exercise 3:** Write down one real project you want to build with Go. It doesn't have to be original — a URL shortener, a task manager, a GitHub stats CLI. Write down the first three files you'd create and what each one would contain. Just thinking it through concretely will make the first step much easier.

---

## Common Mistakes

**Jumping to frameworks before the standard library**

Go's standard library is powerful enough to build production web services. Before you reach for Gin or Echo or Fiber, spend time with `net/http`. Understanding the standard library makes every framework easier to use and easier to debug. Many Go developers I know write services with no router framework at all.

**Treating Go like a language you already know**

If you came from Python, you'll want to write Python in Go syntax. If you came from Java, you'll want to write Java. The fastest way to get good at Go is to accept that it has its own way of doing things — often simpler than what you're used to — and learn that way rather than fighting it.

**Not reading other people's Go code**

Browse GitHub, read the standard library, look at well-maintained open-source Go projects. `github.com/cli/cli` (the GitHub CLI), `github.com/charmbracelet/bubbletea`, and the Go standard library itself are all excellent examples of idiomatic, real-world Go. You'll learn more from reading good code than from any tutorial.

**Stopping when it gets hard**

Concurrency will confuse you at first. Error design will feel like overkill for a while. That's normal. Push through it. The concepts click, and when they do they feel completely natural. Every Go developer I respect went through the same confused phase.

---

## Key Takeaway

You have a complete foundation in Go. The language will not surprise you in the ways it surprised you at lesson one. What's ahead of you now is deepening — learning to write Go with intention, to make deliberate design choices, to handle errors and concurrency in ways that make your programs robust and maintainable. That deepening happens through reading, writing, and building. You have everything you need to start.

---

*[← Previous: Lesson 24 — The Go Toolchain](/post/go/go-scratch-toolchain) | [Course Index: Go from Scratch](/series/go-from-scratch) | 🎓 Foundation Complete! Start with [Idiomatic Go](/post/go/go-idioms-error-handling/) →*
