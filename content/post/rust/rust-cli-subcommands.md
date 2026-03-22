---
title: "Lesson 6: Subcommands and Complex CLI Structures — git-style interfaces"
author: Atharva Pandey
series: "Rust CLI & Tooling"
lesson: 6
keywords: [Rust, rust, CLI, subcommands, clap, git-style, command hierarchy, nested commands]
tags: [Rust tutorial, rust, cli]
date: '2024-09-11T13:50:00.000Z'
---

Every tool starts as `mytool --flag input.txt`. Then someone asks for a second mode. Then a third. Before you know it, you have `mytool --mode=convert --input foo --output bar` and `mytool --mode=validate --strict --input foo` and users are scrolling through `--help` trying to find the three flags that matter for their use case. The answer is subcommands. `git commit`, `docker build`, `cargo test` — separate commands with separate flags, unified under one binary.

## The Subcommand Pattern

With clap's derive API, subcommands map naturally to Rust enums:

```rust
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "repo", version, about = "Repository management tool")]
struct Cli {
    /// Enable verbose output
    #[arg(short, long, global = true)]
    verbose: bool,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Initialize a new repository
    Init {
        /// Directory to initialize
        #[arg(default_value = ".")]
        path: PathBuf,

        /// Use a specific template
        #[arg(short, long)]
        template: Option<String>,
    },

    /// Clone a remote repository
    Clone {
        /// Repository URL
        url: String,

        /// Target directory
        #[arg(short, long)]
        directory: Option<PathBuf>,

        /// Clone depth (shallow clone)
        #[arg(long)]
        depth: Option<u32>,
    },

    /// Show repository status
    Status {
        /// Show short format
        #[arg(short, long)]
        short: bool,
    },
}

fn main() {
    let cli = Cli::parse();

    if cli.verbose {
        eprintln!("Verbose mode enabled");
    }

    match cli.command {
        Commands::Init { path, template } => {
            println!("Initializing repo at: {}", path.display());
            if let Some(t) = template {
                println!("Using template: {}", t);
            }
        }
        Commands::Clone { url, directory, depth } => {
            let dir = directory
                .unwrap_or_else(|| PathBuf::from(url.rsplit('/').next().unwrap_or("repo")));
            println!("Cloning {} into {}", url, dir.display());
            if let Some(d) = depth {
                println!("Shallow clone, depth: {}", d);
            }
        }
        Commands::Status { short } => {
            if short {
                println!("M  src/main.rs");
            } else {
                println!("Modified: src/main.rs");
            }
        }
    }
}
```

Each enum variant is a subcommand. Each field in the variant is a flag or positional argument for that subcommand. The `global = true` on `--verbose` means it works with any subcommand: `repo --verbose status`, `repo clone --verbose`, etc.

Usage looks like:
```
repo init --template rust
repo clone https://github.com/user/repo --depth 1
repo status --short
repo --verbose status
```

## Nested Subcommands

Real tools often need two levels deep. `docker container ls`, `git remote add`, `kubectl get pods`. Here's how:

```rust
use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "cloud", about = "Cloud infrastructure CLI")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Manage compute instances
    #[command(subcommand)]
    Instance(InstanceCommands),

    /// Manage storage volumes
    #[command(subcommand)]
    Volume(VolumeCommands),

    /// Manage networks
    #[command(subcommand)]
    Network(NetworkCommands),
}

#[derive(Subcommand)]
enum InstanceCommands {
    /// List all instances
    List {
        /// Filter by status
        #[arg(long)]
        status: Option<String>,

        /// Output format
        #[arg(long, default_value = "table")]
        format: String,
    },

    /// Create a new instance
    Create {
        /// Instance name
        name: String,

        /// Instance type
        #[arg(short = 't', long, default_value = "small")]
        instance_type: String,

        /// Region
        #[arg(short, long, default_value = "us-east-1")]
        region: String,

        /// SSH key name
        #[arg(long)]
        key: Option<String>,
    },

    /// Delete an instance
    Delete {
        /// Instance name or ID
        name: String,

        /// Skip confirmation
        #[arg(long)]
        force: bool,
    },

    /// SSH into an instance
    Ssh {
        /// Instance name or ID
        name: String,

        /// SSH user
        #[arg(short, long, default_value = "root")]
        user: String,
    },
}

#[derive(Subcommand)]
enum VolumeCommands {
    /// List volumes
    List,

    /// Create a volume
    Create {
        /// Volume name
        name: String,

        /// Size in GB
        #[arg(long)]
        size: u64,
    },

    /// Attach a volume to an instance
    Attach {
        /// Volume name
        volume: String,

        /// Instance name
        instance: String,
    },
}

#[derive(Subcommand)]
enum NetworkCommands {
    /// List networks
    List,

    /// Create a network
    Create {
        /// Network name
        name: String,

        /// CIDR block
        #[arg(long, default_value = "10.0.0.0/16")]
        cidr: String,
    },
}

fn main() {
    let cli = Cli::parse();

    match cli.command {
        Commands::Instance(cmd) => match cmd {
            InstanceCommands::List { status, format } => {
                println!("Listing instances (status: {:?}, format: {})", status, format);
            }
            InstanceCommands::Create {
                name,
                instance_type,
                region,
                key,
            } => {
                println!(
                    "Creating instance '{}' (type: {}, region: {})",
                    name, instance_type, region
                );
                if let Some(k) = key {
                    println!("SSH key: {}", k);
                }
            }
            InstanceCommands::Delete { name, force } => {
                if !force {
                    println!("Are you sure you want to delete '{}'? Use --force to skip.", name);
                    return;
                }
                println!("Deleting instance '{}'", name);
            }
            InstanceCommands::Ssh { name, user } => {
                println!("Connecting to {} as {}", name, user);
            }
        },
        Commands::Volume(cmd) => match cmd {
            VolumeCommands::List => println!("Listing volumes"),
            VolumeCommands::Create { name, size } => {
                println!("Creating volume '{}' ({}GB)", name, size);
            }
            VolumeCommands::Attach { volume, instance } => {
                println!("Attaching '{}' to '{}'", volume, instance);
            }
        },
        Commands::Network(cmd) => match cmd {
            NetworkCommands::List => println!("Listing networks"),
            NetworkCommands::Create { name, cidr } => {
                println!("Creating network '{}' ({})", name, cidr);
            }
        },
    }
}
```

This gives you `cloud instance create myvm --instance-type large --region eu-west-1`. Three levels deep if you count the binary name. More than that and you're overcomplicating things — I've never seen a tool that benefits from four levels.

## Aliases

Users want shortcuts. `docker container list` is fine, but `docker ps` is what everyone actually types. clap supports this:

```rust
use clap::{Parser, Subcommand};

#[derive(Subcommand)]
enum Commands {
    /// List resources (alias: ls)
    #[command(alias = "ls")]
    List {
        #[arg(short, long)]
        all: bool,
    },

    /// Remove resources (aliases: rm, del)
    #[command(aliases = ["rm", "del"])]
    Remove {
        name: String,
    },

    /// Show detailed info (alias: desc)
    #[command(alias = "desc")]
    Describe {
        name: String,
    },
}

#[derive(Parser)]
#[command(name = "res")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

fn main() {
    let cli = Cli::parse();
    match cli.command {
        Commands::List { all } => println!("Listing (all: {})", all),
        Commands::Remove { name } => println!("Removing {}", name),
        Commands::Describe { name } => println!("Describing {}", name),
    }
}
```

Now `res ls`, `res list`, `res rm foo`, and `res remove foo` all work. Aliases don't show up in `--help` by default, which keeps things clean. Users who know the shortcut use it; users who don't see the full name.

## Shared Arguments Across Subcommands

Different subcommands often share a subset of arguments. Don't repeat yourself — use `Args` derive:

```rust
use clap::{Args, Parser, Subcommand};
use std::path::PathBuf;

#[derive(Args, Debug, Clone)]
struct OutputOpts {
    /// Output format
    #[arg(short, long, default_value = "text")]
    format: OutputFormat,

    /// Output file (defaults to stdout)
    #[arg(short, long)]
    output: Option<PathBuf>,

    /// Suppress headers
    #[arg(long)]
    no_headers: bool,
}

#[derive(clap::ValueEnum, Debug, Clone)]
enum OutputFormat {
    Text,
    Json,
    Csv,
    Yaml,
}

#[derive(Args, Debug, Clone)]
struct FilterOpts {
    /// Filter by label (key=value)
    #[arg(short, long)]
    label: Vec<String>,

    /// Filter by name pattern
    #[arg(long)]
    name: Option<String>,
}

#[derive(Subcommand)]
enum Commands {
    /// List pods
    Pods {
        #[command(flatten)]
        output: OutputOpts,

        #[command(flatten)]
        filter: FilterOpts,

        /// Namespace
        #[arg(short, long, default_value = "default")]
        namespace: String,
    },

    /// List services
    Services {
        #[command(flatten)]
        output: OutputOpts,

        #[command(flatten)]
        filter: FilterOpts,
    },

    /// Show logs
    Logs {
        /// Pod name
        pod: String,

        /// Follow log output
        #[arg(short, long)]
        follow: bool,

        /// Number of lines to show
        #[arg(short = 'n', long, default_value = "100")]
        tail: usize,
    },
}

#[derive(Parser)]
#[command(name = "kube")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

fn main() {
    let cli = Cli::parse();

    match cli.command {
        Commands::Pods {
            output,
            filter,
            namespace,
        } => {
            println!("Listing pods in namespace '{}'", namespace);
            println!("Output: {:?}", output);
            println!("Filter: {:?}", filter);
        }
        Commands::Services { output, filter } => {
            println!("Listing services");
            println!("Output: {:?}", output);
            println!("Filter: {:?}", filter);
        }
        Commands::Logs { pod, follow, tail } => {
            println!("Logs for '{}' (follow: {}, tail: {})", pod, follow, tail);
        }
    }
}
```

`OutputOpts` and `FilterOpts` are shared between `pods` and `services` but not `logs`. Each subcommand can mix and match shared argument groups as needed. The `--help` for each subcommand shows only its relevant flags.

## The Dispatch Pattern

For larger applications, matching on the enum in `main` gets unwieldy. I prefer pulling each subcommand's logic into its own function or module:

```rust
use clap::{Parser, Subcommand};
use std::error::Error;

mod commands {
    use std::error::Error;

    pub fn init(path: &str, bare: bool) -> Result<(), Box<dyn Error>> {
        println!("Initializing at '{}' (bare: {})", path, bare);
        // Real implementation here
        Ok(())
    }

    pub fn build(release: bool, target: &Option<String>) -> Result<(), Box<dyn Error>> {
        let profile = if release { "release" } else { "debug" };
        println!("Building in {} mode", profile);
        if let Some(t) = target {
            println!("Target: {}", t);
        }
        Ok(())
    }

    pub fn test(filter: &Option<String>, jobs: usize) -> Result<(), Box<dyn Error>> {
        println!("Running tests (jobs: {})", jobs);
        if let Some(f) = filter {
            println!("Filter: {}", f);
        }
        Ok(())
    }

    pub fn publish(dry_run: bool) -> Result<(), Box<dyn Error>> {
        if dry_run {
            println!("DRY RUN — would publish package");
        } else {
            println!("Publishing package...");
        }
        Ok(())
    }
}

#[derive(Parser)]
#[command(name = "pkg", version)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Initialize a new package
    Init {
        #[arg(default_value = ".")]
        path: String,
        #[arg(long)]
        bare: bool,
    },

    /// Build the package
    Build {
        #[arg(long)]
        release: bool,
        #[arg(long)]
        target: Option<String>,
    },

    /// Run tests
    Test {
        /// Test name filter
        filter: Option<String>,
        #[arg(short, long, default_value = "4")]
        jobs: usize,
    },

    /// Publish the package
    Publish {
        #[arg(long)]
        dry_run: bool,
    },
}

fn main() {
    let cli = Cli::parse();

    let result = match cli.command {
        Commands::Init { ref path, bare } => commands::init(path, bare),
        Commands::Build { release, ref target } => commands::build(release, target),
        Commands::Test { ref filter, jobs } => commands::test(filter, jobs),
        Commands::Publish { dry_run } => commands::publish(dry_run),
    };

    if let Err(e) = result {
        eprintln!("Error: {}", e);
        std::process::exit(1);
    }
}
```

In a real project, each command would be its own module file (`commands/init.rs`, `commands/build.rs`, etc.). The `main.rs` only handles CLI parsing and dispatch. Business logic lives elsewhere.

## Default Subcommand and No Subcommand

What if the user just runs `mytool` with no subcommand? You have options:

```rust
use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(name = "app")]
struct Cli {
    #[command(subcommand)]
    command: Option<Commands>,

    /// Run the default action with this input
    #[arg(global = true)]
    input: Option<String>,
}

#[derive(Subcommand)]
enum Commands {
    /// Run something
    Run { target: String },

    /// Check something
    Check,
}

fn main() {
    let cli = Cli::parse();

    match cli.command {
        Some(Commands::Run { target }) => println!("Running: {}", target),
        Some(Commands::Check) => println!("Checking..."),
        None => {
            // No subcommand — show help or run default behavior
            if let Some(input) = cli.input {
                println!("Default action with: {}", input);
            } else {
                println!("Run 'app --help' for usage");
            }
        }
    }
}
```

Making the subcommand `Option<Commands>` means the user can run the binary without a subcommand. Use this for tools that have a natural default action — `cargo` without arguments shows help, `git` without arguments shows status hints.

## Version and Metadata

Production tools should include version info that's actually useful:

```rust
use clap::Parser;

#[derive(Parser)]
#[command(
    name = "mytool",
    version = env!("CARGO_PKG_VERSION"),
    long_version = concat!(
        env!("CARGO_PKG_VERSION"),
        "\n",
        "commit: ", env!("VERGEN_GIT_SHA"),
        "\n",
        "build: ", env!("VERGEN_BUILD_TIMESTAMP"),
    ),
    about = "A tool that does things",
    long_about = "A longer description that shows up in --help but not -h"
)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}
```

`--version` shows the short version. `-V` shows the same. If you set `long_version`, `--version` shows the detailed build info including git commit. You'll need the `vergen` crate to populate those environment variables at build time, but it's worth it for debugging production issues.

Subcommands are the backbone of any serious CLI. Get the structure right early — reorganizing subcommands after users have built scripts around your interface is painful. Next lesson: cross-compilation. Building for Linux, Mac, and Windows from a single machine.
