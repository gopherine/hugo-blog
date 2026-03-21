---
title: 'Lesson 4: Signal Handling — Catch SIGTERM or lose your work'
author: Atharva Pandey
series: "Go CLI & Tooling"
lesson: 4
keywords:
  - Go
  - golang
  - CLI
  - signal handling
  - SIGTERM
  - SIGINT
  - graceful shutdown
tags:
  - Go tutorial
  - golang
  - CLI
date: '2024-10-30T00:00:00.000Z'
---

Most CLI tools work perfectly on the happy path and fail silently on the unhappy one. The user presses Ctrl-C, the OS sends SIGINT, and the process dies immediately — leaving a temp file half-written, a database connection open, or a progress bar frozen mid-operation. The problem is invisible because most of the time nobody looks at what gets left behind. Until a deployment script depends on that temp file being complete, or a database hits its connection limit, or a batch job loses six hours of progress because the container was terminated between checkpoints.

Signal handling in Go is straightforward once you understand the mechanism. The `os/signal` package lets you intercept OS signals and decide what to do with them before the process exits.

## The Problem

The default behavior when a process receives SIGTERM or SIGINT is immediate termination. For short-lived tools that do not modify external state, this is fine. For any tool that writes files, talks to databases, or runs long operations that should be checkpointed, it is a bug waiting to happen.

```go
// WRONG — no signal handling, state can be corrupted on shutdown
func main() {
    tmpFile, err := os.CreateTemp("", "output-*.csv")
    if err != nil {
        log.Fatal(err)
    }
    defer os.Remove(tmpFile.Name()) // never runs if killed with SIGTERM

    processRecords(tmpFile) // writes 50,000 records to tmpFile

    // Rename temp file to final name — atomic on most filesystems
    if err := os.Rename(tmpFile.Name(), "output.csv"); err != nil {
        log.Fatal(err)
    }
}
```

If the user presses Ctrl-C during `processRecords`, the process is killed. `defer os.Remove` does not run. The half-written temp file sits on disk. The next invocation either reads corrupted data or fails because the file exists.

## The Idiomatic Way

The idiomatic pattern uses `signal.NotifyContext` (Go 1.16+), which creates a context that is cancelled when the process receives a signal:

```go
// RIGHT — context cancelled on SIGINT/SIGTERM, shutdown is graceful
func main() {
    ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer stop()

    if err := run(ctx); err != nil {
        fmt.Fprintln(os.Stderr, err)
        os.Exit(1)
    }
}

func run(ctx context.Context) error {
    tmpFile, err := os.CreateTemp("", "output-*.csv")
    if err != nil {
        return fmt.Errorf("creating temp file: %w", err)
    }
    tmpName := tmpFile.Name()
    // Always clean up the temp file if we don't finish
    defer func() {
        tmpFile.Close()
        os.Remove(tmpName) // only removed if we didn't rename it
    }()

    if err := processRecords(ctx, tmpFile); err != nil {
        return fmt.Errorf("processing records: %w", err)
    }

    // Close before rename (required on Windows, good practice everywhere)
    if err := tmpFile.Close(); err != nil {
        return err
    }

    const finalName = "output.csv"
    if err := os.Rename(tmpName, finalName); err != nil {
        return fmt.Errorf("finalizing output: %w", err)
    }
    // Remove the deferred cleanup — rename succeeded, don't delete the final file
    // (In practice, track a "completed" flag to skip the deferred Remove)
    return nil
}
```

The `processRecords` function must respect context cancellation by checking `ctx.Done()` at regular intervals:

```go
func processRecords(ctx context.Context, w io.Writer) error {
    records, err := loadRecords()
    if err != nil {
        return err
    }

    enc := csv.NewWriter(w)
    defer enc.Flush()

    for i, record := range records {
        // Check for cancellation periodically, not on every record
        if i%1000 == 0 {
            select {
            case <-ctx.Done():
                // Flush what we have written so far
                enc.Flush()
                return fmt.Errorf("interrupted after %d records: %w", i, ctx.Err())
            default:
            }
        }
        if err := enc.Write(record.Fields()); err != nil {
            return err
        }
    }
    return enc.Error()
}
```

For server-style CLIs that need to drain in-flight work before exiting, the pattern is a dedicated shutdown function with a timeout:

```go
func main() {
    ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
    defer stop()

    srv := newServer()
    go func() {
        if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
            log.Println("server error:", err)
        }
    }()

    // Wait for signal
    <-ctx.Done()
    stop() // Stop receiving more signals — second Ctrl-C will force kill

    // Graceful shutdown with timeout
    shutdownCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
    defer cancel()

    if err := srv.Shutdown(shutdownCtx); err != nil {
        log.Printf("server shutdown error: %v", err)
    }
    log.Println("shutdown complete")
}
```

The call to `stop()` after `ctx.Done()` is important: it un-registers the signal handler so a second Ctrl-C delivers the default behavior (immediate termination). Without it, a stuck shutdown would be impossible to break out of.

## In The Wild

I built a data migration CLI that moved records from a legacy MySQL database to a new Postgres schema. The process took 4–6 hours for full migrations. Without signal handling, a deployment or a maintenance restart would kill the process mid-migration, leaving partial data in Postgres that was inconsistent with the migration progress tracker.

The solution was a checkpointed migration with signal-aware processing:

```go
type Migrator struct {
    src        *sql.DB
    dst        *sql.DB
    checkpoint *CheckpointStore
}

func (m *Migrator) Migrate(ctx context.Context) error {
    lastID, err := m.checkpoint.Load()
    if err != nil {
        return fmt.Errorf("loading checkpoint: %w", err)
    }

    batch := make([]Record, 0, 500)
    for {
        select {
        case <-ctx.Done():
            // Save progress before exiting
            if len(batch) > 0 {
                if err := m.flushBatch(batch); err != nil {
                    return fmt.Errorf("flushing final batch: %w", err)
                }
            }
            log.Printf("migration paused at ID %d", lastID)
            return m.checkpoint.Save(lastID)
        default:
        }

        records, err := m.fetchBatch(lastID, 500)
        if err != nil {
            return err
        }
        if len(records) == 0 {
            break // done
        }
        if err := m.flushBatch(records); err != nil {
            return err
        }
        lastID = records[len(records)-1].ID
        m.checkpoint.Save(lastID) // persist after each batch
    }
    return nil
}
```

When the container received SIGTERM, the migration saved its progress and exited cleanly. The next run resumed from the checkpoint. We went from "restart means starting over" to "restart means continuing" with about fifty lines of additional code.

## The Gotchas

**SIGKILL cannot be caught.** `kill -9` and Kubernetes `forceful` termination send SIGKILL, which cannot be intercepted. Graceful shutdown only works for signals that can be caught — primarily SIGTERM and SIGINT. Always check the termination grace period in your container orchestration config; if it is too short for your shutdown to complete, the container gets SIGKILL anyway.

**`signal.Notify` vs. `signal.NotifyContext`.** `signal.Notify` is older and more flexible — it delivers signals to a channel. `signal.NotifyContext` is the modern version that integrates directly with `context.Context`, which composes better with the rest of the Go ecosystem. Prefer `signal.NotifyContext` for new code.

**Windows signals.** Windows does not have SIGTERM. For cross-platform CLIs, handle `os.Interrupt` (which maps to Ctrl-C / SIGINT on Unix and Ctrl-Break on Windows) and skip `syscall.SIGTERM` conditionally, or use a build tag to define the signal set per platform.

## Key Takeaway

Signal handling is not advanced Go — it is table stakes for any CLI that modifies state. The `signal.NotifyContext` pattern is three lines at the top of `main`. Everything else flows from propagating that context down through your call stack and checking `ctx.Done()` at natural breakpoints in long-running loops. The tools that survive container restarts, user interrupts, and deployment rollovers are the ones that treat signals as a first-class part of their design rather than an afterthought.

---

← [Lesson 3: File I/O Patterns](/post/go/go-cli-file-io) | [Course Index](/series/go-cli-tooling) | [Next → Lesson 5: Cross-Compilation](/post/go/go-cli-cross-compile)
