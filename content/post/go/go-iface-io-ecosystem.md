---
title: 'Lesson 7: The io.Reader/Writer Ecosystem — The most powerful 2-method interfaces in Go'
author: Atharva Pandey
series: "Go Interfaces & Abstraction Design"
lesson: 7
keywords:
  - Go
  - golang
  - interfaces
  - io.Reader
  - io.Writer
  - streaming
  - Go standard library
tags:
  - Go tutorial
  - golang
  - interfaces
date: '2025-03-08T00:00:00.000Z'
---

If you want to understand what makes Go interfaces powerful, do not start with your own code. Start with `io.Reader`. It is one method — `Read(p []byte) (n int, err error)` — and it is the foundation of an entire ecosystem that spans files, network connections, HTTP bodies, compressed streams, encrypted data, buffered reads, pipes, test helpers, and dozens of third-party libraries. Everything that produces bytes implements `io.Reader`. Everything that consumes bytes accepts one.

Understanding this ecosystem is not academic. It is the difference between writing code that loads an entire file into memory and writing code that streams a gigabyte through your application with a fixed 32KB buffer.

## The Problem

The naive approach to working with data is to treat everything as `[]byte` or `string` — read it all, transform it all, write it all.

```go
// WRONG — loads the entire response body into memory
func fetchAndProcess(url string) error {
    resp, err := http.Get(url)
    if err != nil {
        return err
    }
    defer resp.Body.Close()

    // ReadAll holds the entire response in memory
    data, err := io.ReadAll(resp.Body)
    if err != nil {
        return err
    }

    return processJSON(data) // also processes the entire blob at once
}
```

For a 10KB response, this is fine. For a 500MB export file, it blows your heap. For a streaming API that sends data continuously, `ReadAll` never returns.

The second version of this problem is functions that only work with files:

```go
// WRONG — forced to use os.File even when any byte source would work
func countLines(filename string) (int, error) {
    f, err := os.Open(filename)
    if err != nil {
        return 0, err
    }
    defer f.Close()

    count := 0
    scanner := bufio.NewScanner(f)
    for scanner.Scan() {
        count++
    }
    return count, scanner.Err()
}
```

This function is untestable without a real file. It cannot count lines from a network response, a `bytes.Buffer`, or a gzip stream. It is artificially locked to a single data source.

## The Idiomatic Way

Accept `io.Reader` for anything that reads bytes. Accept `io.Writer` for anything that writes bytes. The single-method interface connects your code to every data source and sink that exists and will ever exist.

```go
// RIGHT — accepts any reader, works with files, HTTP bodies, buffers, pipes
func countLines(r io.Reader) (int, error) {
    count := 0
    scanner := bufio.NewScanner(r)
    for scanner.Scan() {
        count++
    }
    return count, scanner.Err()
}

// Works with a file:
f, _ := os.Open("data.txt")
n, _ := countLines(f)

// Works with an HTTP response:
resp, _ := http.Get("https://example.com/logs")
n, _ := countLines(resp.Body)

// Works in tests without touching the filesystem:
n, _ := countLines(strings.NewReader("line one\nline two\nline three"))
```

The same pattern applies to writing. Accept `io.Writer` and your output can go to a file, an HTTP response body, a `bytes.Buffer`, a gzip compressor, a network socket, or a test recorder.

```go
// RIGHT — write to any sink
func writeReport(w io.Writer, data []ReportRow) error {
    enc := csv.NewWriter(w)
    defer enc.Flush()
    for _, row := range data {
        if err := enc.Write(row.Fields()); err != nil {
            return err
        }
    }
    return enc.Error()
}

// Write to a file:
f, _ := os.Create("report.csv")
defer f.Close()
writeReport(f, rows)

// Write directly into an HTTP response:
func reportHandler(w http.ResponseWriter, r *http.Request) {
    w.Header().Set("Content-Type", "text/csv")
    writeReport(w, fetchRows(r.Context()))
}

// Capture in a test:
var buf bytes.Buffer
writeReport(&buf, testRows)
```

The streaming composition is where the ecosystem becomes genuinely exciting. Because every intermediate step implements both `io.Reader` and `io.Writer`, you can chain them:

```go
// Stream: HTTP response → gzip decompress → JSON decode, never loading the full body
func streamDecompress(url string, target interface{}) error {
    resp, err := http.Get(url)
    if err != nil {
        return err
    }
    defer resp.Body.Close()

    // resp.Body is io.ReadCloser (satisfies io.Reader)
    gz, err := gzip.NewReader(resp.Body)
    if err != nil {
        return err
    }
    defer gz.Close()

    // gz is *gzip.Reader (satisfies io.Reader)
    // json.NewDecoder accepts io.Reader
    return json.NewDecoder(gz).Decode(target)
}
```

The HTTP response body, the gzip decompressor, and the JSON decoder form a pipeline. Data flows through them in chunks. At no point does the entire payload exist in memory at once.

## In The Wild

I worked on a data export service that produced CSV files from database queries. The initial implementation buffered the entire result set in memory, serialized it to a string, and then sent the string as an HTTP response. For small exports it was fine. When users started exporting tens of thousands of rows, the service OOM-killed itself.

The fix was to wire the database cursor directly to the HTTP response writer through the `io.Writer` chain:

```go
func (h *ExportHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
    w.Header().Set("Content-Type", "text/csv")
    w.Header().Set("Content-Disposition", `attachment; filename="export.csv"`)

    // Data flows: DB rows → CSV encoder → gzip → HTTP response
    gz := gzip.NewWriter(w)
    defer gz.Close()

    cw := csv.NewWriter(gz)
    defer cw.Flush()

    rows, err := h.db.QueryContext(r.Context(), exportQuery, queryArgs(r)...)
    if err != nil {
        http.Error(w, "query failed", http.StatusInternalServerError)
        return
    }
    defer rows.Close()

    for rows.Next() {
        var row ExportRow
        if err := rows.Scan(&row.Fields); err != nil {
            // Can't change status code after headers are sent,
            // but at least we stop writing corrupt data.
            return
        }
        cw.Write(row.CSVFields())
    }
}
```

Memory usage for a 100,000-row export dropped from ~400MB to under 2MB because we were never holding more than a few kilobytes in any buffer at once. The connection, the gzip writer, and the csv writer each maintain their own small internal buffer and flush when full.

The other real-world use I reach for constantly is `io.TeeReader` — it reads from one reader and simultaneously writes everything it reads to a writer. Perfect for request logging:

```go
// Log the request body while still allowing the handler to read it
var requestLog bytes.Buffer
body := io.TeeReader(r.Body, &requestLog)
// Use body as the request body — every byte read is also written to requestLog
```

## The Gotchas

**`io.ReadAll` is not always wrong.** For small, bounded payloads — a config file, a short API response — reading all at once is fine and simpler. The problem is when you use it as the default for everything without thinking about payload size.

**`io.Reader` is single-pass.** Once you have read to the end, you cannot read it again without seeking or re-creating the reader. If you need to read a body twice (once for signature verification, once for JSON decoding), use `io.TeeReader` to capture a copy, or use `bytes.Buffer` to buffer once and reset.

**Error handling mid-stream is awkward.** Once you have started writing to an HTTP `ResponseWriter`, you cannot change the status code. Design your handlers to validate input before beginning to write output, and accept that large streaming operations may fail mid-transfer.

## Key Takeaway

`io.Reader` and `io.Writer` are the most important interfaces in Go not because of their simplicity but because of their ubiquity. Every standard library package that deals with bytes speaks this language, which means code that accepts `io.Reader` is automatically compatible with files, network connections, compressed streams, in-memory buffers, and test helpers — without any modification. Accepting `io.Reader` instead of `[]byte` costs you nothing in the common case and buys you everything when the data is large, streaming, or dynamically sourced. This is interface design working exactly as intended.

---

← [Lesson 6: Testability Without Over-Mocking](/post/go/go-iface-testability) | [Course Index](/series/go-interfaces-abstraction-design) | [Next → Lesson 8: Designing Interfaces for Libraries](/post/go/go-iface-library-design)
