---
title: 'Lesson 2: io Patterns — Reader, Writer, and the composability that makes Go great'
author: Atharva Pandey
series: "Go Standard Library Mastery"
lesson: 2
keywords:
  - Go
  - golang
  - io
  - Reader
  - Writer
  - io.Reader
  - io.Writer
  - composability
  - streaming
  - stdlib
tags:
  - Go tutorial
  - golang
  - stdlib
date: '2024-08-16T00:00:00.000Z'
---

The `io` package is three hundred lines of interface definitions and a handful of utility functions. It is also the spine of the entire Go standard library. Every package that reads or writes data — `os`, `net`, `compress/gzip`, `crypto/tls`, `encoding/json`, `bufio` — does it through `io.Reader` and `io.Writer`. Once you understand these two interfaces, the standard library clicks into place as one coherent system.

I spent my first few months with Go treating `io.Reader` as the thing that HTTP response bodies happen to be. It wasn't until I built a streaming CSV processor — piping data from S3 through gzip decompression into a CSV parser, all without loading the file into memory — that I understood what the interfaces were actually designed for.

## The Problem

The naive approach to processing data reads everything into memory first:

```go
// WRONG — reads entire file into memory before processing
func processFile(filename string) error {
    data, err := os.ReadFile(filename) // allocates len(file) bytes
    if err != nil {
        return err
    }
    // parse and process data...
    lines := strings.Split(string(data), "\n")
    for _, line := range lines {
        processLine(line)
    }
    return nil
}
```

For a 10MB file, this allocates 10MB. For a 10GB file, it allocates 10GB and probably OOMs. The same pattern shows up with HTTP response bodies, database result sets, and network streams. If you can process data line by line without holding all of it in memory simultaneously, you should.

The `io` package's interfaces exist precisely to eliminate this pattern.

## The Idiomatic Way

`io.Reader` and `io.Writer` are defined as:

```go
type Reader interface {
    Read(p []byte) into how many bytes were read
    // Read reads up to len(p) bytes, returns n bytes read and any error.
    // A successful Read returns n > 0. At EOF, returns 0, io.EOF.
    Read(p []byte) (n int, err error)
}

type Writer interface {
    Write(p []byte) (n int, err error)
}
```

The power is in composition. Here's the streaming CSV processor I mentioned:

```go
package main

import (
    "compress/gzip"
    "encoding/csv"
    "fmt"
    "io"
    "os"
)

func processGzipCSV(filename string) error {
    // Open the file — returns an *os.File, which implements io.Reader
    f, err := os.Open(filename)
    if err != nil {
        return fmt.Errorf("open: %w", err)
    }
    defer f.Close()

    // Wrap in gzip reader — wraps any io.Reader
    gz, err := gzip.NewReader(f)
    if err != nil {
        return fmt.Errorf("gzip: %w", err)
    }
    defer gz.Close()

    // Wrap in bufio.Reader for efficient line reading — wraps any io.Reader
    // csv.NewReader takes any io.Reader
    cr := csv.NewReader(gz)
    cr.ReuseRecord = true // reuse the slice on each call — less allocation

    for {
        record, err := cr.Read()
        if err == io.EOF {
            break
        }
        if err != nil {
            return fmt.Errorf("read csv: %w", err)
        }
        processRecord(record)
    }
    return nil
}
```

The entire 10GB gzip CSV is never in memory. Data flows from disk through gzip decompression through CSV parsing one record at a time. Swap `os.Open` for `http.Response.Body` and you're streaming from a URL. Swap `gzip.NewReader` for `compress/zstd.NewReader` and you're handling a different compression format. None of the other code changes.

`io.TeeReader` and `io.MultiWriter` enable fan-out patterns:

```go
// io.TeeReader: read from src, write a copy to dst
// Useful for: logging request bodies without consuming them,
// computing a checksum while reading data.
func withBodyLogging(r io.Reader) io.Reader {
    var buf bytes.Buffer
    return io.TeeReader(r, &buf)
    // After reading, buf contains everything that was read
}

// io.MultiWriter: write to multiple destinations simultaneously
func withTeeUpload(dst io.Writer, s3bucket *s3.Bucket) io.Writer {
    pr, pw := io.Pipe()
    go func() {
        s3bucket.Upload(pr)
    }()
    // Every byte written to the returned Writer goes to both dst and S3
    return io.MultiWriter(dst, pw)
}
```

`io.Pipe` is worth a closer look. It creates a synchronous, in-memory pipe — a connected reader/writer pair. Bytes written to the `PipeWriter` are immediately available to the `PipeReader`. No intermediate buffer allocation. Use it to connect a producer that writes to a `Writer` with a consumer that reads from a `Reader`:

```go
// Connect json.Encoder (writes) to http.NewRequest (reads) with no intermediate buffer
pr, pw := io.Pipe()

go func() {
    enc := json.NewEncoder(pw)
    if err := enc.Encode(payload); err != nil {
        pw.CloseWithError(err)
        return
    }
    pw.Close()
}()

req, err := http.NewRequestWithContext(ctx, "POST", url, pr)
// The request body is streamed directly from the JSON encoder
```

## In The Wild

`io.ReadAll` is `ioutil.ReadAll`'s replacement. Use it when you genuinely need all the bytes:

```go
// When you must read everything (e.g., to unmarshal JSON not via streaming):
data, err := io.ReadAll(resp.Body)
```

But `io.Copy` is better when you're transferring data from one stream to another:

```go
// Pipe an HTTP response body to a file — no intermediate buffer
f, _ := os.Create(filename)
defer f.Close()
n, err := io.Copy(f, resp.Body)
// io.Copy uses a 32KB internal buffer and loops until EOF
```

`io.LimitReader` prevents unbounded reads from untrusted sources:

```go
// Read at most 10MB from an HTTP body
limited := io.LimitReader(resp.Body, 10<<20)
data, err := io.ReadAll(limited)
```

If the body is larger than 10MB, `ReadAll` returns exactly 10MB of data and no error. Check `len(data) == 10<<20` if you need to detect the truncation.

## The Gotchas

**`io.EOF` is a sentinel, not an error.** `io.EOF` returned from `Read` means "the stream ended normally." It should be checked and handled, not returned to callers as an error. Functions that read until EOF typically loop until `err == io.EOF` and return `nil` — a clean end of stream is not an error.

**`Read` may read fewer bytes than `len(p)`.** A single `Read` call doesn't guarantee filling the buffer. `io.ReadFull` reads exactly `len(p)` bytes or returns an error — use it when you need a specific number of bytes:

```go
buf := make([]byte, 16)
n, err := io.ReadFull(r, buf) // reads exactly 16 bytes or fails
```

**`io.Pipe` blocks until both sides are ready.** `pw.Write` blocks until `pr.Read` consumes the data. Always run one side in a goroutine. If both sides run synchronously, you get a deadlock.

**`bufio.Reader` buffers ahead — don't use it with non-seekable readers if you need to un-read.** If you wrap a network connection in a `bufio.Reader`, the buffered read might read more from the wire than you process. The data is in the buffer, not lost — but it complicates protocol switching (like HTTP to WebSocket upgrades) where a second reader also needs to access the beginning of the stream.

## Key Takeaway

`io.Reader` and `io.Writer` are the lingua franca of Go I/O. Any two libraries that speak this interface can be connected without writing glue code. The pattern is: chain readers and writers to build processing pipelines that never allocate more than a fixed buffer regardless of input size. Learn `io.Pipe`, `io.TeeReader`, `io.MultiWriter`, `io.LimitReader`, and `io.Copy`. They're the building blocks for everything else.

---

**Previous:** [Lesson 1: net/http Deep Dive](/post/go/go-stdlib-net-http/)
**Next:** [Lesson 3: encoding/json Beyond Basics — Custom marshalers, streaming, and the traps](/post/go/go-stdlib-json/)
