---
title: 'Lesson 5: os and filepath — Cross-platform file operations that actually work'
author: Atharva Pandey
series: "Go Standard Library Mastery"
lesson: 5
keywords:
  - Go
  - golang
  - os
  - filepath
  - file operations
  - cross-platform
  - path manipulation
  - stdlib
tags:
  - Go tutorial
  - golang
  - stdlib
date: '2025-01-22T00:00:00.000Z'
---

Go's `os` and `path/filepath` packages are among the most underappreciated in the standard library. Most developers know `os.Open`, `os.Create`, and `os.ReadFile`. Far fewer know `os.MkdirAll`, `os.CreateTemp`, `filepath.WalkDir`, or the difference between `path` and `path/filepath` — which is the difference between code that works everywhere and code that silently breaks on Windows.

I maintain a CLI tool that runs on macOS, Linux, and Windows. The file operation bugs I've shipped have taught me exactly which parts of these packages require care.

## The Problem

Path manipulation with string concatenation:

```go
// WRONG — breaks on Windows where separator is \, not /
configPath := os.Getenv("HOME") + "/config/" + app + "/settings.json"

// ALSO WRONG — uses path package instead of filepath for OS paths
import "path"
joined := path.Join(baseDir, "config", "settings.json")
// path.Join always uses forward slash — wrong on Windows
```

The `path` package is for URL paths and forward-slash-separated paths in HTTP routing. `path/filepath` is for OS file system paths and uses the OS-native separator. On Linux and macOS they behave identically. On Windows, `filepath.Join` uses backslashes and `path.Join` uses forward slashes, and the two are not interchangeable.

The second problem is file handling without proper error propagation and cleanup:

```go
// WRONG — if Write fails, the file is partially written and not cleaned up
func writeConfig(cfg Config, path string) error {
    f, err := os.Create(path)
    if err != nil {
        return err
    }
    defer f.Close() // file is closed but not removed on error
    return json.NewEncoder(f).Encode(cfg)
}
```

If the JSON encoding fails partway through, the file exists but is invalid. Any reader that arrives before your retry will get a corrupt file.

## The Idiomatic Way

Always use `filepath` for OS paths, always use `filepath.Join` for concatenation:

```go
import (
    "os"
    "path/filepath"
    "runtime"
)

// configDir returns the user's config directory in a cross-platform way
func configDir(appName string) (string, error) {
    // os.UserConfigDir returns the correct directory for the OS:
    // Linux: $XDG_CONFIG_HOME or ~/.config
    // macOS: ~/Library/Application Support
    // Windows: %APPDATA%
    base, err := os.UserConfigDir()
    if err != nil {
        return "", fmt.Errorf("config dir: %w", err)
    }
    return filepath.Join(base, appName), nil
}

func configFilePath(appName string) (string, error) {
    dir, err := configDir(appName)
    if err != nil {
        return "", err
    }
    return filepath.Join(dir, "settings.json"), nil
}
```

Atomic file writes — write to a temp file first, then rename:

```go
// writeFileAtomic writes data to path atomically.
// If the write fails, the original file is unchanged.
func writeFileAtomic(path string, data []byte, perm os.FileMode) error {
    dir := filepath.Dir(path)

    // Create a temp file in the same directory as the target.
    // Same directory ensures os.Rename is atomic (same filesystem).
    f, err := os.CreateTemp(dir, ".tmp-*")
    if err != nil {
        return fmt.Errorf("create temp: %w", err)
    }
    tmpPath := f.Name()

    // Clean up the temp file on any error path.
    cleanup := func() {
        f.Close()
        os.Remove(tmpPath)
    }

    if _, err := f.Write(data); err != nil {
        cleanup()
        return fmt.Errorf("write: %w", err)
    }

    // Sync to disk before renaming — ensures data survives a crash.
    if err := f.Sync(); err != nil {
        cleanup()
        return fmt.Errorf("sync: %w", err)
    }

    if err := f.Close(); err != nil {
        os.Remove(tmpPath)
        return fmt.Errorf("close: %w", err)
    }

    // Set permissions before rename
    if err := os.Chmod(tmpPath, perm); err != nil {
        os.Remove(tmpPath)
        return fmt.Errorf("chmod: %w", err)
    }

    // Atomic rename — on the same filesystem, this is a single syscall.
    if err := os.Rename(tmpPath, path); err != nil {
        os.Remove(tmpPath)
        return fmt.Errorf("rename: %w", err)
    }

    return nil
}
```

`os.Rename` on the same filesystem is an atomic operation at the OS level. Either the destination sees the new file or the old file — never a partial write. This pattern is how package managers, config tools, and databases write files safely.

Walking a directory tree:

```go
// filepath.WalkDir is more efficient than filepath.Walk — passes DirEntry
// not os.FileInfo, avoiding a stat call for each entry.
func findGoFiles(root string) ([]string, error) {
    var files []string

    err := filepath.WalkDir(root, func(path string, d fs.DirEntry, err error) error {
        if err != nil {
            // Permission error on a directory — skip it but continue walking.
            if os.IsPermission(err) {
                return filepath.SkipDir
            }
            return err
        }

        // Skip hidden directories (like .git)
        if d.IsDir() && strings.HasPrefix(d.Name(), ".") {
            return filepath.SkipDir
        }

        if !d.IsDir() && filepath.Ext(path) == ".go" {
            files = append(files, path)
        }
        return nil
    })

    return files, err
}
```

## In The Wild

`os.DirFS` is the modern way to work with directory-relative paths and is testable with `testing/fstest`:

```go
// Accept an fs.FS instead of a path string — makes the function testable
func loadTemplates(fsys fs.FS) error {
    return fs.WalkDir(fsys, ".", func(path string, d fs.DirEntry, err error) error {
        if err != nil {
            return err
        }
        if d.IsDir() || filepath.Ext(path) != ".html" {
            return nil
        }
        data, err := fs.ReadFile(fsys, path)
        if err != nil {
            return err
        }
        return registerTemplate(path, data)
    })
}

// Production: use the real filesystem
loadTemplates(os.DirFS("templates"))

// Test: use an in-memory filesystem
loadTemplates(fstest.MapFS{
    "index.html":   {Data: []byte("<html>...</html>")},
    "about.html":   {Data: []byte("<html>...</html>")},
})
```

This pattern — accepting `fs.FS` rather than a directory path — makes functions that read files testable without touching the real filesystem.

## The Gotchas

**`os.Remove` vs `os.RemoveAll`.** `os.Remove` removes a single file or empty directory. `os.RemoveAll` removes a directory and all its contents. Calling `os.Remove` on a non-empty directory returns an error; calling `os.RemoveAll` on a path that doesn't exist silently returns nil.

**`filepath.Abs` depends on `os.Getwd`.** If your process changes working directory (e.g., in tests), `filepath.Abs` will produce different results for the same relative path. Resolve relative paths to absolute paths at startup, before any `chdir`.

**File descriptor limits.** `os.Open` returns a file descriptor. Forgetting to close it — perhaps because an error return skips the `defer f.Close()` — leaks a file descriptor. On Linux, the default limit is 1024 per process. In a long-running service, accumulating unclosed file descriptors eventually causes "too many open files" errors. Use `defer f.Close()` immediately after every successful `os.Open`.

**Path traversal in user-supplied paths.** If you build a file path from user input — URL parameters, API payloads — validate that the resolved path is within the expected directory:

```go
func safeOpen(base, userPath string) (*os.File, error) {
    // Clean removes ".." components
    clean := filepath.Join(base, filepath.Clean(userPath))
    if !strings.HasPrefix(clean, filepath.Clean(base)+string(os.PathSeparator)) {
        return nil, fmt.Errorf("path traversal detected: %s", userPath)
    }
    return os.Open(clean)
}
```

## Key Takeaway

Use `path/filepath` for OS paths and `path` for URL paths — they're not interchangeable. Use `filepath.Join` for path construction, never string concatenation. Write files atomically by writing to a temp file and renaming. Accept `fs.FS` in library functions for testability. Close every file you open.

---

**Previous:** [Lesson 4: time Package Gotchas](/post/go/go-stdlib-time/)
**Next:** [Lesson 6: sync Package Complete Guide — Mutex, Once, Pool, Map — when to use each](/post/go/go-stdlib-sync/)
