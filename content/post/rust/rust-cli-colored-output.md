---
title: "Lesson 4: Colored Output and Progress Bars — UX for the terminal"
author: Atharva Pandey
series: "Rust CLI & Tooling"
lesson: 4
keywords: [Rust, rust, CLI, colored output, progress bar, indicatif, owo-colors, terminal, UX]
tags: [Rust tutorial, rust, cli]
date: '2024-09-07T16:33:00.000Z'
---

I used to think terminal output was either plain text or ANSI escape code soup. Then I looked at how tools like `cargo`, `ripgrep`, and `bat` handle their output — color used purposefully to draw the eye, progress bars that give you actual information, spinners that tell you something is happening. Good terminal UX isn't about making things pretty. It's about making information scannable.

## Raw ANSI Escape Codes

Before using any crate, you should understand what's actually happening. Terminal colors are just special byte sequences embedded in the output stream:

```rust
fn main() {
    // Format: \x1b[<code>m
    // 31 = red, 32 = green, 33 = yellow, 34 = blue
    // 1 = bold, 0 = reset
    println!("\x1b[31mThis is red\x1b[0m");
    println!("\x1b[1;32mThis is bold green\x1b[0m");
    println!("\x1b[33mWarning:\x1b[0m something happened");

    // Background colors: 40-47
    println!("\x1b[41;37mWhite on red background\x1b[0m");

    // 256-color mode
    println!("\x1b[38;5;208mOrange text (color 208)\x1b[0m");

    // True color (24-bit)
    println!("\x1b[38;2;255;100;50mRGB(255,100,50)\x1b[0m");
}
```

The `\x1b[0m` reset at the end is critical — forget it and your entire terminal stays colored until you run `reset`. I've done this in production logs. It's embarrassing.

You can absolutely use raw ANSI codes for simple cases. But crates handle edge cases you'll forget about: Windows console compatibility, `NO_COLOR` environment variable, piped output detection.

## owo-colors: Lightweight Coloring

For most CLI tools, `owo-colors` is my pick. It's zero-dependency, uses Rust's `Display` trait, and compiles fast:

```toml
[dependencies]
owo-colors = "4"
```

```rust
use owo_colors::OwoColorize;
use std::io::IsTerminal;

fn main() {
    // Basic colors
    println!("{}", "Error: file not found".red());
    println!("{}", "Warning: deprecated API".yellow());
    println!("{}", "Success: deployed".green());
    println!("{}", "Info: processing...".blue());

    // Styles
    println!("{}", "Bold and underlined".bold().underline());
    println!("{}", "Dimmed text".dimmed());

    // Combining
    println!(
        "{}: {} files processed",
        "Result".green().bold(),
        42.blue()
    );

    // Conditional coloring — essential for piped output
    if std::io::stdout().is_terminal() {
        println!("{}", "Colored output".cyan());
    } else {
        println!("Plain output");
    }
}
```

`owo-colors` implements coloring through the `Display` trait, so colored values work anywhere you'd use `{}` in a format string. No macros, no special syntax — just method chaining on any type that implements `Display`.

## Respecting NO_COLOR and --no-color

The [NO_COLOR](https://no-color.org) standard is simple: if the `NO_COLOR` environment variable is set (to any value), don't output ANSI color codes. Lots of CI environments set this. Your tool should respect it:

```rust
use owo_colors::{OwoColorize, Stream};
use std::io::IsTerminal;

struct Output {
    use_color: bool,
}

impl Output {
    fn new(force_no_color: bool) -> Self {
        let use_color = !force_no_color
            && std::env::var("NO_COLOR").is_err()
            && std::io::stdout().is_terminal();

        Self { use_color }
    }

    fn error(&self, msg: &str) {
        if self.use_color {
            eprintln!("{}: {}", "error".red().bold(), msg);
        } else {
            eprintln!("error: {}", msg);
        }
    }

    fn warn(&self, msg: &str) {
        if self.use_color {
            eprintln!("{}: {}", "warning".yellow().bold(), msg);
        } else {
            eprintln!("warning: {}", msg);
        }
    }

    fn success(&self, msg: &str) {
        if self.use_color {
            println!("{}: {}", "ok".green().bold(), msg);
        } else {
            println!("ok: {}", msg);
        }
    }

    fn info(&self, msg: &str) {
        if self.use_color {
            println!("{}: {}", "info".blue(), msg);
        } else {
            println!("info: {}", msg);
        }
    }
}

fn main() {
    let output = Output::new(false); // would come from --no-color flag

    output.error("Failed to connect to database");
    output.warn("Config file not found, using defaults");
    output.info("Starting server on port 8080");
    output.success("Server started");
}
```

I see so many tools that blast color codes into piped output. `myapp | grep something` and now your grep is matching against invisible ANSI bytes. Check your output destination. Always.

## Progress Bars with indicatif

`indicatif` is the standard progress bar crate. It handles everything — progress bars, spinners, multi-bar displays, estimated time remaining:

```toml
[dependencies]
indicatif = "0.17"
```

### Simple Progress Bar

```rust
use indicatif::{ProgressBar, ProgressStyle};
use std::time::Duration;
use std::thread;

fn main() {
    let total = 1000;
    let pb = ProgressBar::new(total);

    pb.set_style(
        ProgressStyle::with_template(
            "{spinner:.green} [{bar:40.cyan/blue}] {pos}/{len} ({eta})"
        )
        .unwrap()
        .progress_chars("=> "),
    );

    for _ in 0..total {
        // Simulate work
        thread::sleep(Duration::from_millis(5));
        pb.inc(1);
    }

    pb.finish_with_message("done");
}
```

The template string is where the magic is. `{bar:40.cyan/blue}` means a 40-character-wide bar, cyan for filled, blue for unfilled. `{eta}` is estimated time remaining. `{spinner:.green}` is a spinning animation in green. The format is well-documented and flexible.

### Spinner for Unknown Duration

When you don't know how long something will take:

```rust
use indicatif::{ProgressBar, ProgressStyle};
use std::time::Duration;
use std::thread;

fn main() {
    let spinner = ProgressBar::new_spinner();
    spinner.set_style(
        ProgressStyle::with_template("{spinner:.green} {msg}")
            .unwrap()
            .tick_strings(&["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏", "✓"]),
    );

    spinner.set_message("Connecting to server...");
    for _ in 0..20 {
        thread::sleep(Duration::from_millis(100));
        spinner.tick();
    }

    spinner.set_message("Downloading manifest...");
    for _ in 0..30 {
        thread::sleep(Duration::from_millis(100));
        spinner.tick();
    }

    spinner.finish_with_message("Connected");
}
```

The last element in `tick_strings` is displayed when the spinner finishes. Using a checkmark gives a nice visual confirmation.

### Multi-Progress for Parallel Work

When you're downloading multiple files or processing in parallel:

```rust
use indicatif::{MultiProgress, ProgressBar, ProgressStyle};
use std::thread;
use std::time::Duration;

fn main() {
    let multi = MultiProgress::new();

    let style = ProgressStyle::with_template(
        "{prefix:>12.cyan.bold} [{bar:25}] {bytes}/{total_bytes} {msg}"
    )
    .unwrap()
    .progress_chars("## ");

    let files = vec![
        ("rustc", 50_000_000u64),
        ("stdlib", 30_000_000),
        ("docs", 15_000_000),
        ("tools", 8_000_000),
    ];

    let handles: Vec<_> = files
        .into_iter()
        .map(|(name, size)| {
            let pb = multi.add(ProgressBar::new(size));
            pb.set_style(style.clone());
            pb.set_prefix(name.to_string());

            thread::spawn(move || {
                let chunk = size / 100;
                for _ in 0..100 {
                    thread::sleep(Duration::from_millis(
                        (50.0 * rand_factor()) as u64,
                    ));
                    pb.inc(chunk);
                }
                pb.finish_with_message("done");
            })
        })
        .collect();

    for h in handles {
        h.join().unwrap();
    }
}

fn rand_factor() -> f64 {
    // Poor man's random for demo purposes
    let t = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .subsec_nanos();
    (t % 100) as f64 / 100.0 + 0.5
}
```

`MultiProgress` manages multiple bars on screen, handling the terminal line manipulation so they don't overwrite each other. Each bar can be updated from its own thread.

## Structured Log-Style Output

For tools that produce log-style output (like `cargo`), you want aligned, colored prefixes:

```rust
use owo_colors::OwoColorize;

enum Action {
    Compiling,
    Downloading,
    Installing,
    Finished,
    Warning,
    Error,
}

fn log(action: Action, message: &str) {
    let (label, colored) = match action {
        Action::Compiling => ("Compiling", "Compiling".green().bold().to_string()),
        Action::Downloading => ("Downloading", "Downloading".cyan().bold().to_string()),
        Action::Installing => ("Installing", "Installing".green().bold().to_string()),
        Action::Finished => ("Finished", "Finished".green().bold().to_string()),
        Action::Warning => ("Warning", "Warning".yellow().bold().to_string()),
        Action::Error => ("Error", "Error".red().bold().to_string()),
    };

    // Right-align the label to 12 characters, like cargo does
    let padding = 12usize.saturating_sub(label.len());
    let pad_str = " ".repeat(padding);
    println!("{}{} {}", pad_str, colored, message);
}

fn main() {
    log(Action::Downloading, "serde v1.0.193");
    log(Action::Downloading, "tokio v1.35.0");
    log(Action::Compiling, "myapp v0.1.0 (path+file:///home/user/myapp)");
    log(Action::Warning, "unused variable: `x`");
    log(Action::Finished, "dev [unoptimized + debuginfo] target(s) in 2.34s");
}
```

That right-aligned prefix is subtle but makes output so much easier to scan. Your eye can track down the left column and find errors or warnings instantly.

## Tables in the Terminal

Sometimes you need to display tabular data. You *could* use a crate like `tabled`, but for simple cases, formatting with `println!` and padding works fine:

```rust
fn print_table(headers: &[&str], rows: &[Vec<String>]) {
    // Calculate column widths
    let mut widths: Vec<usize> = headers.iter().map(|h| h.len()).collect();
    for row in rows {
        for (i, cell) in row.iter().enumerate() {
            if i < widths.len() {
                widths[i] = widths[i].max(cell.len());
            }
        }
    }

    // Print header
    let header_line: Vec<String> = headers
        .iter()
        .enumerate()
        .map(|(i, h)| format!("{:<width$}", h, width = widths[i]))
        .collect();
    println!("{}", header_line.join("  "));

    // Print separator
    let sep: Vec<String> = widths.iter().map(|w| "-".repeat(*w)).collect();
    println!("{}", sep.join("  "));

    // Print rows
    for row in rows {
        let cells: Vec<String> = row
            .iter()
            .enumerate()
            .map(|(i, c)| {
                let w = widths.get(i).copied().unwrap_or(0);
                format!("{:<width$}", c, width = w)
            })
            .collect();
        println!("{}", cells.join("  "));
    }
}

fn main() {
    let headers = &["Name", "Version", "Status", "Size"];
    let rows = vec![
        vec!["serde".into(), "1.0.193".into(), "installed".into(), "245 KB".into()],
        vec!["tokio".into(), "1.35.0".into(), "downloading".into(), "1.2 MB".into()],
        vec!["clap".into(), "4.4.11".into(), "installed".into(), "890 KB".into()],
    ];

    print_table(headers, &rows);
}
```

Output:
```
Name   Version  Status       Size
-----  -------  -----------  ------
serde  1.0.193  installed    245 KB
tokio  1.35.0   downloading  1.2 MB
clap   4.4.11   installed    890 KB
```

## Combining Progress Bars With Log Output

The one thing that trips everyone up: you can't `println!` while a progress bar is active. The output will collide with the bar. Use the `ProgressBar::println` method instead:

```rust
use indicatif::{ProgressBar, ProgressStyle};
use std::time::Duration;
use std::thread;

fn main() {
    let pb = ProgressBar::new(100);
    pb.set_style(
        ProgressStyle::with_template("[{bar:40}] {pos}% {msg}")
            .unwrap()
            .progress_chars("=> "),
    );

    for i in 0..100 {
        thread::sleep(Duration::from_millis(50));

        if i == 25 {
            pb.println("Processing: found 3 warnings");
        }
        if i == 50 {
            pb.println("Processing: optimizing phase 2");
        }
        if i == 75 {
            pb.println("Processing: generating output");
        }

        pb.set_message(format!("step {}", i));
        pb.inc(1);
    }

    pb.finish_with_message("complete");
}
```

`pb.println()` prints the message *above* the progress bar, then redraws the bar below it. Clean, no flicker, no garbled output.

## The Complete Pattern

Here's how I structure output in a real tool — all the pieces together:

```rust
use indicatif::{ProgressBar, ProgressStyle};
use owo_colors::OwoColorize;
use std::io::IsTerminal;

struct Ui {
    color: bool,
    progress: bool,
}

impl Ui {
    fn new() -> Self {
        let is_term = std::io::stderr().is_terminal();
        Self {
            color: is_term && std::env::var("NO_COLOR").is_err(),
            progress: is_term,
        }
    }

    fn status(&self, action: &str, msg: &str) {
        let padding = 12usize.saturating_sub(action.len());
        if self.color {
            eprintln!(
                "{}{} {}",
                " ".repeat(padding),
                action.green().bold(),
                msg
            );
        } else {
            eprintln!("{}{} {}", " ".repeat(padding), action, msg);
        }
    }

    fn error(&self, msg: &str) {
        if self.color {
            eprintln!("{}: {}", "error".red().bold(), msg);
        } else {
            eprintln!("error: {}", msg);
        }
    }

    fn progress_bar(&self, total: u64) -> ProgressBar {
        if !self.progress {
            return ProgressBar::hidden();
        }

        let pb = ProgressBar::new(total);
        pb.set_style(
            ProgressStyle::with_template(
                "  {spinner:.green} [{bar:30}] {pos}/{len} {msg}"
            )
            .unwrap()
            .progress_chars("=> "),
        );
        pb
    }
}

fn main() {
    let ui = Ui::new();

    ui.status("Resolving", "dependencies...");
    ui.status("Downloading", "3 packages");

    let pb = ui.progress_bar(100);
    for i in 0..100 {
        std::thread::sleep(std::time::Duration::from_millis(30));
        pb.inc(1);
    }
    pb.finish_and_clear();

    ui.status("Compiling", "myapp v0.1.0");
    ui.status("Finished", "dev target(s) in 3.2s");
}
```

Status messages go to stderr. Progress bars go to stderr (indicatif does this by default). Actual program output — whatever your tool produces — goes to stdout. This way `myapp > output.txt` captures only the real output while the user still sees progress.

Terminal UX is one of those things that separates hobby projects from tools people actually want to use. Spend the thirty minutes getting it right. Your users will thank you by not filing "the output is confusing" issues.

Next — signal handling and graceful shutdown. Because Ctrl+C shouldn't leave temp files scattered across the filesystem.
