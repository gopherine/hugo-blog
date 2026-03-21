---
title: 'Lesson 3: File I/O Patterns — Read, write, stream without loading everything into memory'
author: Atharva Pandey
series: "Go CLI & Tooling"
lesson: 3
keywords:
  - Go
  - golang
  - CLI
  - file I/O
  - streaming
  - bufio
  - io.Reader
tags:
  - Go tutorial
  - golang
  - CLI
date: '2024-09-12T00:00:00.000Z'
---

CLI tools spend most of their life doing file I/O. Reading config files, processing log dumps, writing output, transforming data from stdin to stdout — it all comes down to bytes moving through your program. The difference between a CLI tool that handles 100MB files gracefully and one that runs out of memory on large inputs is almost always whether you read everything into memory or stream it.

Go's standard library gives you everything you need to stream data efficiently. The key is knowing which functions to reach for and which ones to avoid when the input is large or unknown in size.

## The Problem

The easiest way to read a file in Go is `os.ReadFile`. For small files, it is fine. For large files or pipelines where the size is unknown, it is a bug:

```go
// WRONG — loads the entire file into memory before processing
func processLogFile(path string) error {
    data, err := os.ReadFile(path)
    if err != nil {
        return err
    }
    lines := strings.Split(string(data), "\n")
    for _, line := range lines {
        if err := handleLine(line); err != nil {
            return err
        }
    }
    return nil
}
```

For a 2GB log file, this allocates 2GB of memory before processing the first line. The second issue is that it reads from a file path directly, which means you cannot feed it stdin — and CLI tools should almost always be able to read from stdin for composability in pipelines.

The write side has the same problem:

```go
// WRONG — accumulates all output in memory before writing
func generateReport(records []Record) (string, error) {
    var sb strings.Builder
    for _, r := range records {
        fmt.Fprintf(&sb, "%s,%d,%s\n", r.Name, r.Count, r.Timestamp.Format(time.RFC3339))
    }
    return sb.String(), nil
}
// caller then writes the entire string at once
```

For millions of records, this allocates a very large string before writing a single byte.

## The Idiomatic Way

The idiomatic Go CLI reads from `io.Reader`, writes to `io.Writer`, and processes data one line (or one chunk) at a time. The caller decides whether the source is a file, stdin, or a network connection:

```go
// RIGHT — streams line by line, accepts any io.Reader
func processLogs(r io.Reader, w io.Writer) error {
    scanner := bufio.NewScanner(r)
    for scanner.Scan() {
        line := scanner.Text()
        result, err := parseLine(line)
        if err != nil {
            // Log bad lines but continue processing
            fmt.Fprintf(os.Stderr, "skipping malformed line: %v\n", err)
            continue
        }
        fmt.Fprintln(w, result.Format())
    }
    return scanner.Err()
}
```

The main command wires up the reader, supporting both file and stdin transparently:

```go
// cmd/process.go — open file or fall back to stdin
var processCmd = &cobra.Command{
    Use:   "process [file]",
    Short: "Process log lines from a file or stdin",
    Args:  cobra.MaximumNArgs(1),
    RunE: func(cmd *cobra.Command, args []string) error {
        var r io.Reader = os.Stdin
        if len(args) == 1 {
            f, err := os.Open(args[0])
            if err != nil {
                return fmt.Errorf("opening file: %w", err)
            }
            defer f.Close()
            r = f
        }
        return processLogs(r, os.Stdout)
    },
}
```

Now the CLI composes naturally in shell pipelines:

```bash
# from a file
myapp process access.log

# from stdin in a pipeline
cat access.log | grep "ERROR" | myapp process

# from a compressed file via process substitution
myapp process <(zcat access.log.gz)
```

For writing large outputs, use `bufio.Writer` to batch small writes into fewer system calls:

```go
// RIGHT — buffered writer reduces syscall count for many small writes
func writeCSVReport(w io.Writer, records []Record) error {
    bw := bufio.NewWriter(w)
    defer bw.Flush() // critical: flush the buffer before returning

    enc := csv.NewWriter(bw)
    defer enc.Flush()

    if err := enc.Write([]string{"name", "count", "timestamp"}); err != nil {
        return err
    }
    for _, r := range records {
        if err := enc.Write([]string{
            r.Name,
            strconv.Itoa(r.Count),
            r.Timestamp.Format(time.RFC3339),
        }); err != nil {
            return err
        }
    }
    return enc.Error()
}
```

The `defer bw.Flush()` is important. Without it, the last chunk of data sitting in the buffer never gets written to the underlying `io.Writer`. This is a common and silent data loss bug.

## In The Wild

The most useful pattern I have built for file-processing CLIs is a function that accepts a path and a processing function, and handles all the open/close and stdin-fallback boilerplate:

```go
// withReader opens path for reading, or uses stdin if path is "-" or empty.
// The caller provides a function that receives the open io.Reader.
func withReader(path string, fn func(io.Reader) error) error {
    if path == "" || path == "-" {
        return fn(os.Stdin)
    }
    f, err := os.Open(path)
    if err != nil {
        return fmt.Errorf("opening %q: %w", path, err)
    }
    defer f.Close()
    return fn(f)
}

// withWriter opens path for writing (creates or truncates), or uses stdout.
func withWriter(path string, fn func(io.Writer) error) error {
    if path == "" || path == "-" {
        return fn(os.Stdout)
    }
    f, err := os.Create(path)
    if err != nil {
        return fmt.Errorf("creating %q: %w", path, err)
    }
    defer f.Close()
    return fn(f)
}
```

Every command in the tool uses this pair:

```go
RunE: func(cmd *cobra.Command, args []string) error {
    return withReader(inputFile, func(r io.Reader) error {
        return withWriter(outputFile, func(w io.Writer) error {
            return processLogs(r, w)
        })
    })
},
```

The convention that `-` means stdin/stdout is standard Unix behavior and makes your tool composable with everything.

For processing very large files where you need random access or parallel processing, `io.ReaderAt` is the right interface — it supports reading from arbitrary offsets without seeking:

```go
// Process large files in parallel by reading sections concurrently
func processInParallel(f *os.File, chunkSize int64) error {
    info, err := f.Stat()
    if err != nil {
        return err
    }
    size := info.Size()

    var wg sync.WaitGroup
    for offset := int64(0); offset < size; offset += chunkSize {
        wg.Add(1)
        go func(off int64) {
            defer wg.Done()
            buf := make([]byte, min(chunkSize, size-off))
            n, _ := f.ReadAt(buf, off)
            processChunk(buf[:n])
        }(offset)
    }
    wg.Wait()
    return nil
}
```

## The Gotchas

**`bufio.Scanner` has a default line-length limit of 64KB.** If your log lines can be longer (compressed JSON blobs, very long SQL queries), set a custom buffer:

```go
scanner := bufio.NewScanner(r)
scanner.Buffer(make([]byte, 1024*1024), 1024*1024) // allow up to 1MB lines
```

**`os.Create` truncates existing files.** If you are writing to a file that should be appended to, use `os.OpenFile` with `os.O_APPEND|os.O_CREATE|os.O_WRONLY`.

**Flush errors.** `csv.Writer.Flush()` and `bufio.Writer.Flush()` can return errors on the `Error()` method call. Always check them — a disk-full condition shows up here, not on individual writes.

## Key Takeaway

File I/O in a CLI tool comes down to one principle: accept `io.Reader`, write to `io.Writer`, and stream rather than accumulate. The standard library's `bufio.Scanner` for line-by-line reads and `bufio.Writer` for batched writes cover the vast majority of CLI needs efficiently. Treat stdin and stdout as first-class citizens with the `-` convention, and your tool will integrate naturally into shell pipelines and compose with every other Unix tool. The memory profile of a well-written Go CLI should be nearly flat regardless of input size.

---

← [Lesson 2: Config and Env Handling](/post/go/go-cli-config) | [Course Index](/series/go-cli-tooling) | [Next → Lesson 4: Signal Handling](/post/go/go-cli-signals)
