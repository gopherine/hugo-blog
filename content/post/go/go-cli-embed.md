---
title: 'Lesson 6: Embedding Assets — embed.FS puts files inside your binary'
author: Atharva Pandey
series: "Go CLI & Tooling"
lesson: 6
keywords:
  - Go
  - golang
  - CLI
  - embed
  - embed.FS
  - static assets
  - templates
tags:
  - Go tutorial
  - golang
  - CLI
date: '2025-02-05T00:00:00.000Z'
---

Before Go 1.16, embedding static assets in a Go binary required either a code generation tool that converted files to byte arrays, a third-party library like `packr` or `statik`, or shipping the files alongside the binary and reading them from disk at runtime. Each approach had real costs: generated code bloated repositories, third-party tools had to be installed separately, and shipping separate files broke the "single binary" distribution story.

The `//go:embed` directive in Go 1.16 solved this cleanly. Files, directories, and whole asset trees can be embedded directly into the binary with a single comment and a variable declaration. Templates, SQL migrations, web assets, default configs — everything your binary needs can travel with it.

## The Problem

The typical approach before `embed` was to read files from disk relative to the binary location:

```go
// WRONG — depends on files being present on disk at runtime
func loadTemplate(name string) (*template.Template, error) {
    path := filepath.Join("templates", name+".html")
    data, err := os.ReadFile(path)
    if err != nil {
        return nil, fmt.Errorf("reading template %q: %w", name, err)
    }
    return template.New(name).Parse(string(data))
}
```

This breaks the moment the binary is moved without the `templates/` directory. In a Docker container, you have to `COPY templates/ /app/templates/` in addition to the binary. In a Homebrew formula, you have to define data files separately. The single-binary story is broken.

The workaround was code generation:

```bash
# go generate called statik or packr to produce a .go file with byte arrays
//go:generate packr2
```

Generated files are hundreds of kilobytes of encoded strings that nobody can read, diff, or review meaningfully. They are regenerated on every asset change and committed to version control as a noisy diff.

## The Idiomatic Way

With `//go:embed`, the approach is clean and transparent:

```go
// templates.go — embed the entire templates directory
package myapp

import "embed"

//go:embed templates
var templateFiles embed.FS
```

That is the entire embedding declaration. The `templates/` directory and all its contents are now compiled into the binary. Use it like any filesystem:

```go
func loadTemplate(name string) (*template.Template, error) {
    data, err := templateFiles.ReadFile("templates/" + name + ".html")
    if err != nil {
        return nil, fmt.Errorf("reading template %q: %w", name, err)
    }
    return template.New(name).Parse(string(data))
}
```

For SQL migrations, the same approach makes schema management self-contained:

```go
// migrations.go
package db

import "embed"

//go:embed migrations
var migrationFiles embed.FS

func RunMigrations(db *sql.DB) error {
    entries, err := migrationFiles.ReadDir("migrations")
    if err != nil {
        return err
    }
    for _, entry := range entries {
        if entry.IsDir() {
            continue
        }
        sql, err := migrationFiles.ReadFile("migrations/" + entry.Name())
        if err != nil {
            return err
        }
        if _, err := db.Exec(string(sql)); err != nil {
            return fmt.Errorf("running migration %s: %w", entry.Name(), err)
        }
    }
    return nil
}
```

The migrations directory:

```
migrations/
├── 001_create_users.sql
├── 002_add_email_index.sql
└── 003_create_orders.sql
```

All three files travel with the binary. No migration tool installation required, no external file copying, no path management.

For web assets in an HTTP server, `embed.FS` implements `fs.FS` so it works directly with `http.FileServer`:

```go
package main

import (
    "embed"
    "net/http"
    "io/fs"
)

//go:embed static
var staticFiles embed.FS

func main() {
    // Serve files from the embedded static/ directory
    sub, err := fs.Sub(staticFiles, "static")
    if err != nil {
        panic(err)
    }
    http.Handle("/static/", http.StripPrefix("/static/", http.FileServer(http.FS(sub))))
    http.ListenAndServe(":8080", nil)
}
```

The `fs.Sub` call strips the `static/` prefix from the embedded paths so URLs like `/static/app.js` resolve to the file at `static/app.js` in the embedded FS.

## In The Wild

I built a CLI tool that generates project scaffolding — a `myapp init` command that creates a directory structure with config files, a Makefile, and starter code. Before `embed`, I had three options: ship the templates separately, bundle them as string constants in a Go file, or generate them at build time. All three were painful in different ways.

After Go 1.16, the solution was straightforward:

```go
//go:embed scaffold
var scaffoldFiles embed.FS

var initCmd = &cobra.Command{
    Use:   "init <project-name>",
    Short: "Initialize a new project",
    Args:  cobra.ExactArgs(1),
    RunE: func(cmd *cobra.Command, args []string) error {
        projectName := args[0]
        return scaffold(projectName, scaffoldFiles)
    },
}

func scaffold(name string, files embed.FS) error {
    return fs.WalkDir(files, "scaffold", func(path string, d fs.DirEntry, err error) error {
        if err != nil {
            return err
        }
        // path is "scaffold/Makefile", "scaffold/cmd/main.go", etc.
        dest := strings.TrimPrefix(path, "scaffold/")
        dest = strings.ReplaceAll(dest, "PROJECT_NAME", name) // rename placeholders

        if d.IsDir() {
            return os.MkdirAll(filepath.Join(name, dest), 0o755)
        }

        data, err := files.ReadFile(path)
        if err != nil {
            return err
        }
        // Replace template variables in file contents
        content := strings.ReplaceAll(string(data), "PROJECT_NAME", name)
        return os.WriteFile(filepath.Join(name, dest), []byte(content), 0o644)
    })
}
```

The entire scaffold template tree — a dozen files — is embedded in the binary. Users install one binary and run `myapp init`. No additional downloads, no internet required.

## The Gotchas

**Embedding is evaluated at compile time, not at the `//go:embed` directive location.** The files must exist at the path relative to the `.go` file that contains the directive when `go build` runs. If the files are generated, they must be generated before the build.

**`//go:embed` cannot embed files outside the module root.** You cannot embed `/etc/ssl/certs` or any path outside your module. The embed directive only works within your module's file tree.

**Hidden files and `_`-prefixed files are excluded by default.** If your asset directory contains `.env`, `.gitignore`, or `_internal/` paths, they are not embedded unless you use `all:` in the pattern: `//go:embed all:static`.

**Binary size grows.** Every embedded file increases binary size. A tool that embeds a 10MB web app will produce a 10MB+ binary. This is usually acceptable and expected — but be intentional about what you embed and compress assets where size matters.

## Key Takeaway

`//go:embed` is one of the cleanest quality-of-life features in modern Go. It eliminates the mismatch between "where assets live during development" and "where they are at runtime," makes single-binary distribution straightforward, and removes a whole category of deployment bugs. For CLI tools, embed your templates, migrations, default configs, and scaffold files. For web servers in Go, embed your static assets. The binary becomes self-contained, and deployment becomes a matter of copying one file.

---

← [Lesson 5: Cross-Compilation](/post/go/go-cli-cross-compile) | [Course Index](/series/go-cli-tooling) | [Next → Lesson 7: Build Flags and ldflags](/post/go/go-cli-build-flags)
