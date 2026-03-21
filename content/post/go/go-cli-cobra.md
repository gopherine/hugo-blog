---
title: 'Lesson 1: Building CLIs with Cobra — From main.go to production CLI'
author: Atharva Pandey
series: "Go CLI & Tooling"
lesson: 1
keywords:
  - Go
  - golang
  - CLI
  - Cobra
  - command line
  - cobra-cli
tags:
  - Go tutorial
  - golang
  - CLI
date: '2024-06-22T00:00:00.000Z'
---

Every Go project that grows into something useful eventually needs a command-line interface. Maybe it starts as a quick `main.go` with `os.Args[1]` checks and a switch statement. That works until you need subcommands, flags, help text, shell completion, and version information — and suddenly you are maintaining a hand-rolled argument parser that nobody wants to touch. Cobra is the standard library-grade solution to this problem, and learning to structure a Cobra application properly saves you from rewriting it twice.

Cobra is used in Docker, Kubernetes, GitHub CLI, Hugo, and dozens of other production CLI tools. It handles subcommands, persistent flags, local flags, positional arguments, help generation, shell completion, and command aliases. Understanding its mental model — commands as a tree, flags as typed values, execution as a pipeline — makes the difference between a CLI that grows gracefully and one that becomes a maintenance burden at twenty subcommands.

## The Problem

The naive CLI structure that starts working immediately but breaks badly:

```go
// WRONG — hand-rolled argument parsing that grows into a nightmare
func main() {
    if len(os.Args) < 2 {
        fmt.Println("usage: myapp <command>")
        os.Exit(1)
    }

    switch os.Args[1] {
    case "create":
        if len(os.Args) < 3 {
            fmt.Println("usage: myapp create <name>")
            os.Exit(1)
        }
        handleCreate(os.Args[2])
    case "delete":
        handleDelete(os.Args[2])
    case "--help", "-h":
        printHelp()
    default:
        fmt.Fprintf(os.Stderr, "unknown command: %s\n", os.Args[1])
        os.Exit(1)
    }
}
```

This works for two commands. Add flags to `create` (`--dry-run`, `--namespace`, `--output-format`), add a `list` subcommand with its own flags, add a `--config` global flag, and this switch statement becomes impossible to maintain. Help text is hardcoded. Flags are parsed manually. Shell completion requires starting from scratch.

## The Idiomatic Way

A well-structured Cobra application separates the root command, subcommands, and flag definitions into predictable locations:

```
myapp/
├── main.go
└── cmd/
    ├── root.go
    ├── create.go
    ├── delete.go
    └── list.go
```

The root command is the entry point for all flags and the parent for all subcommands:

```go
// cmd/root.go
package cmd

import (
    "os"
    "github.com/spf13/cobra"
)

var cfgFile string
var verbose bool

var rootCmd = &cobra.Command{
    Use:   "myapp",
    Short: "A tool for managing resources",
    Long:  `myapp manages the lifecycle of resources in your environment.`,
}

func Execute() {
    if err := rootCmd.Execute(); err != nil {
        os.Exit(1)
    }
}

func init() {
    rootCmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file (default: $HOME/.myapp.yaml)")
    rootCmd.PersistentFlags().BoolVarP(&verbose, "verbose", "v", false, "enable verbose output")
}
```

Each subcommand is its own file, registered in `init()`:

```go
// cmd/create.go
package cmd

import (
    "fmt"
    "github.com/spf13/cobra"
)

var dryRun bool
var namespace string

var createCmd = &cobra.Command{
    Use:   "create <name>",
    Short: "Create a new resource",
    Args:  cobra.ExactArgs(1),
    RunE: func(cmd *cobra.Command, args []string) error {
        name := args[0]
        if verbose {
            fmt.Printf("creating resource %q in namespace %q\n", name, namespace)
        }
        if dryRun {
            fmt.Println("[dry-run] would create resource, stopping here")
            return nil
        }
        return createResource(cmd.Context(), name, namespace)
    },
}

func init() {
    rootCmd.AddCommand(createCmd)
    createCmd.Flags().BoolVar(&dryRun, "dry-run", false, "preview changes without applying them")
    createCmd.Flags().StringVarP(&namespace, "namespace", "n", "default", "target namespace")
}
```

The `main.go` stays clean:

```go
// main.go
package main

import "myapp/cmd"

func main() {
    cmd.Execute()
}
```

Using `RunE` instead of `Run` lets you return errors that Cobra handles cleanly — it prints the error and exits with a non-zero code, which is correct behavior for CLI tools. The context from `cmd.Context()` lets you propagate cancellation from signal handling (covered in a later lesson).

## In The Wild

When I built an internal deployment tool used across my team, I made the common mistake of starting with `Run` functions that called `os.Exit` directly. This made testing the command logic impossible — you cannot test a function that calls `os.Exit`. The fix was consistent use of `RunE` combined with a clear separation between command logic and business logic:

```go
// BEFORE: untestable
var deployCmd = &cobra.Command{
    Use: "deploy",
    Run: func(cmd *cobra.Command, args []string) {
        if err := runDeploy(args); err != nil {
            fmt.Fprintln(os.Stderr, err)
            os.Exit(1)
        }
    },
}

// AFTER: testable — error propagates, no os.Exit in business logic
var deployCmd = &cobra.Command{
    Use:   "deploy <service>",
    Short: "Deploy a service to the target environment",
    Args:  cobra.ExactArgs(1),
    RunE: func(cmd *cobra.Command, args []string) error {
        cfg, err := loadConfig(cfgFile)
        if err != nil {
            return fmt.Errorf("loading config: %w", err)
        }
        return deployer.Deploy(cmd.Context(), args[0], cfg)
    },
}
```

The `deployer.Deploy` function is a pure Go function that accepts a context and config. Tests call it directly. The Cobra command is just a thin adapter that parses CLI input and delegates to it.

Adding shell completion is automatic once your commands are structured properly:

```go
// Generate bash completion: myapp completion bash > /etc/bash_completion.d/myapp
// Generate zsh completion:  myapp completion zsh > ~/.zsh/completions/_myapp
// Cobra generates this for free from your command tree structure.
rootCmd.AddCommand(cobra.CompletionOptions.DisableDefaultCmd...)
```

## The Gotchas

**Package-level flag variables are global state.** Using `var dryRun bool` at the package level means your command is not concurrently safe if you ever run multiple commands in tests. For production CLI tools this rarely matters, but for library-quality CLIs, consider passing options as structs instead.

**`PersistentPreRunE` for shared setup.** If multiple commands need to load configuration or initialize logging, put that logic in `rootCmd.PersistentPreRunE`. It runs before every command in the tree. Just be aware that calling a subcommand's own `PersistentPreRunE` requires explicit chaining — Cobra does not chain parent and child pre-run functions automatically.

**`SilenceUsage: true` for error behavior.** By default, Cobra prints the usage text when a command returns an error. For user-input errors this is helpful. For runtime errors deep in business logic, it is noise. Setting `rootCmd.SilenceUsage = true` and handling your own usage printing gives you more control.

## Key Takeaway

The Cobra mental model is: one root command, as many subcommands as you need, each with its own flags and `RunE` functions. Business logic stays out of the command layer entirely — commands parse input and delegate. This structure gives you automatic help generation, shell completion, consistent error handling, and a codebase that scales to fifty subcommands without becoming unreadable. Start with this structure on day one, even if you only have one command, and you will never need to rewrite your CLI's foundation.

---

[Course Index](/series/go-cli-tooling) | [Next → Lesson 2: Config and Env Handling](/post/go/go-cli-config)
