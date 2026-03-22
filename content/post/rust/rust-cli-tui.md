---
title: "Lesson 9: Building TUIs with ratatui — Terminal user interfaces"
author: Atharva Pandey
series: "Rust CLI & Tooling"
lesson: 9
keywords: [Rust, rust, CLI, TUI, ratatui, terminal UI, crossterm, interactive, dashboard]
tags: [Rust tutorial, rust, cli]
date: '2024-09-19T07:55:00.000Z'
---

I was monitoring a deployment through five separate terminal windows — one for logs, one for metrics, one for the deployment status, one for the database, and one running htop. Alt-tabbing between them like a madman. Then a colleague showed me their custom TUI dashboard that combined all five views into a single terminal screen, with tabs and live-updating graphs. It was written in Rust with ratatui. I rebuilt it that weekend.

## What Is a TUI?

A TUI (Terminal User Interface) is a full-screen interactive application that runs in a terminal. Think `htop`, `vim`, `lazygit`, or `k9s`. Not a REPL, not a CLI with flags — a visual application that takes over the entire terminal, handles keyboard input, and draws UI elements like tables, charts, and panels.

ratatui (a fork and successor of `tui-rs`) is the standard library for building these in Rust. It's an immediate-mode rendering library — you describe what the UI looks like on every frame, and ratatui figures out the minimal terminal updates needed.

## Setup

```toml
[dependencies]
ratatui = "0.28"
crossterm = "0.28"
```

`ratatui` handles rendering widgets. `crossterm` handles the low-level terminal interaction — raw mode, mouse capture, alternate screen buffer. ratatui supports multiple backends, but crossterm is the most portable (works on Linux, macOS, and Windows).

## The Minimal TUI

Every ratatui app follows the same structure: enter raw mode, set up the terminal, run the main loop, clean up on exit.

```rust
use crossterm::{
    event::{self, Event, KeyCode, KeyEventKind},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    prelude::*,
    widgets::{Block, Borders, Paragraph},
};
use std::io;

fn main() -> io::Result<()> {
    // Setup
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    // Run
    let result = run(&mut terminal);

    // Cleanup — always runs, even on error
    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;

    result
}

fn run(terminal: &mut Terminal<CrosstermBackend<io::Stdout>>) -> io::Result<()> {
    loop {
        terminal.draw(|frame| {
            let area = frame.area();
            let greeting = Paragraph::new("Hello, ratatui! Press 'q' to quit.")
                .block(Block::default().borders(Borders::ALL).title("My First TUI"));
            frame.render_widget(greeting, area);
        })?;

        // Handle input
        if event::poll(std::time::Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                if key.kind == KeyEventKind::Press && key.code == KeyCode::Char('q') {
                    return Ok(());
                }
            }
        }
    }
}
```

**Raw mode** disables line buffering and echo. Without it, the terminal waits for Enter before sending input, and typed characters appear on screen. In raw mode, every keypress is delivered immediately and silently.

**Alternate screen** switches to a separate screen buffer. When your app exits, the terminal restores the original content. This is why vim doesn't trash your scrollback.

**The cleanup** is critical. If your app panics without disabling raw mode, the terminal is unusable until the user runs `reset`. We'll handle this properly later.

## Application State

Real TUIs have state — selected items, scroll positions, input buffers. Structure it cleanly from the start:

```rust
use crossterm::event::{self, Event, KeyCode, KeyEventKind};
use ratatui::{
    prelude::*,
    widgets::{Block, Borders, List, ListItem, ListState, Paragraph},
};
use std::io;

struct App {
    items: Vec<String>,
    list_state: ListState,
    running: bool,
}

impl App {
    fn new() -> Self {
        let items = vec![
            "First item".to_string(),
            "Second item".to_string(),
            "Third item".to_string(),
            "Fourth item".to_string(),
            "Fifth item".to_string(),
        ];

        let mut list_state = ListState::default();
        list_state.select(Some(0));

        Self {
            items,
            list_state,
            running: true,
        }
    }

    fn next(&mut self) {
        let i = match self.list_state.selected() {
            Some(i) => {
                if i >= self.items.len() - 1 {
                    0
                } else {
                    i + 1
                }
            }
            None => 0,
        };
        self.list_state.select(Some(i));
    }

    fn previous(&mut self) {
        let i = match self.list_state.selected() {
            Some(i) => {
                if i == 0 {
                    self.items.len() - 1
                } else {
                    i - 1
                }
            }
            None => 0,
        };
        self.list_state.select(Some(i));
    }

    fn selected_item(&self) -> Option<&str> {
        self.list_state
            .selected()
            .and_then(|i| self.items.get(i))
            .map(|s| s.as_str())
    }
}

fn run(terminal: &mut Terminal<CrosstermBackend<io::Stdout>>) -> io::Result<()> {
    let mut app = App::new();

    while app.running {
        terminal.draw(|frame| ui(frame, &mut app))?;
        handle_events(&mut app)?;
    }

    Ok(())
}

fn ui(frame: &mut Frame, app: &mut App) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(5), Constraint::Length(3)])
        .split(frame.area());

    // List widget
    let items: Vec<ListItem> = app
        .items
        .iter()
        .map(|i| ListItem::new(i.as_str()))
        .collect();

    let list = List::new(items)
        .block(Block::default().borders(Borders::ALL).title("Items"))
        .highlight_style(Style::default().bg(Color::DarkGray).bold())
        .highlight_symbol(">> ");

    frame.render_stateful_widget(list, chunks[0], &mut app.list_state);

    // Status bar
    let selected = app
        .selected_item()
        .unwrap_or("nothing");
    let status = Paragraph::new(format!("Selected: {} | Press q to quit, j/k to navigate", selected))
        .block(Block::default().borders(Borders::ALL));

    frame.render_widget(status, chunks[1]);
}

fn handle_events(app: &mut App) -> io::Result<()> {
    if event::poll(std::time::Duration::from_millis(50))? {
        if let Event::Key(key) = event::read()? {
            if key.kind == KeyEventKind::Press {
                match key.code {
                    KeyCode::Char('q') => app.running = false,
                    KeyCode::Char('j') | KeyCode::Down => app.next(),
                    KeyCode::Char('k') | KeyCode::Up => app.previous(),
                    _ => {}
                }
            }
        }
    }
    Ok(())
}
```

The separation matters: `App` holds state, `ui()` renders it, `handle_events()` updates it. This is the Model-View-Update pattern, and it keeps TUI code manageable as complexity grows.

## Layouts

ratatui's layout system splits the terminal into rectangular regions:

```rust
use ratatui::{
    prelude::*,
    widgets::{Block, Borders, Paragraph},
};

fn ui(frame: &mut Frame) {
    // Split vertically: header, main content, footer
    let main_layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),  // Header: exactly 3 rows
            Constraint::Min(10),   // Main: at least 10, takes remaining space
            Constraint::Length(3), // Footer: exactly 3 rows
        ])
        .split(frame.area());

    // Split the middle section horizontally
    let content_layout = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage(30), // Sidebar: 30% width
            Constraint::Percentage(70), // Content: 70% width
        ])
        .split(main_layout[1]);

    // Render widgets into each region
    let header = Paragraph::new("Dashboard v0.1.0")
        .style(Style::default().fg(Color::Cyan).bold())
        .block(Block::default().borders(Borders::ALL));
    frame.render_widget(header, main_layout[0]);

    let sidebar = Paragraph::new("Navigation\n\n  [1] Overview\n  [2] Logs\n  [3] Metrics")
        .block(Block::default().borders(Borders::ALL).title("Menu"));
    frame.render_widget(sidebar, content_layout[0]);

    let content = Paragraph::new("Main content area")
        .block(Block::default().borders(Borders::ALL).title("Content"));
    frame.render_widget(content, content_layout[1]);

    let footer = Paragraph::new(" q: quit | tab: switch panel | /: search")
        .style(Style::default().fg(Color::DarkGray));
    frame.render_widget(footer, main_layout[2]);
}
```

`Constraint::Length(n)` — fixed size. `Constraint::Min(n)` — at least n, stretches to fill. `Constraint::Percentage(n)` — percentage of parent. `Constraint::Ratio(a, b)` — fraction of parent. Mix and match.

## Building a Real Dashboard

Here's a more complete example — a system monitoring dashboard with multiple panels:

```rust
use crossterm::event::{self, Event, KeyCode, KeyEventKind};
use ratatui::{
    prelude::*,
    widgets::*,
};
use std::io;
use std::time::Instant;

struct App {
    running: bool,
    tab: usize,
    cpu_history: Vec<f64>,
    mem_history: Vec<f64>,
    logs: Vec<String>,
    log_scroll: u16,
    start_time: Instant,
}

impl App {
    fn new() -> Self {
        Self {
            running: true,
            tab: 0,
            cpu_history: vec![0.0; 60],
            mem_history: vec![0.0; 60],
            logs: Vec::new(),
            log_scroll: 0,
            start_time: Instant::now(),
        }
    }

    fn tick(&mut self) {
        // Simulate data
        let elapsed = self.start_time.elapsed().as_secs_f64();
        let cpu = 30.0 + 20.0 * (elapsed * 0.5).sin() + 10.0 * (elapsed * 1.3).cos();
        let mem = 45.0 + 15.0 * (elapsed * 0.3).sin();

        self.cpu_history.push(cpu.clamp(0.0, 100.0));
        if self.cpu_history.len() > 60 {
            self.cpu_history.remove(0);
        }

        self.mem_history.push(mem.clamp(0.0, 100.0));
        if self.mem_history.len() > 60 {
            self.mem_history.remove(0);
        }

        // Add a log entry occasionally
        let seconds = elapsed as u64;
        if seconds % 3 == 0 && (self.logs.is_empty() || seconds > self.logs.len() as u64 * 3) {
            self.logs.push(format!(
                "[{:>6.1}s] Request handled in {}ms",
                elapsed,
                (cpu * 2.0) as u64
            ));
        }
    }
}

fn ui(frame: &mut Frame, app: &App) {
    let outer = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(10),
            Constraint::Length(1),
        ])
        .split(frame.area());

    // Tab bar
    let titles = vec!["Overview", "Logs", "Config"];
    let tabs = Tabs::new(titles)
        .block(Block::default().borders(Borders::ALL).title("Dashboard"))
        .select(app.tab)
        .style(Style::default().fg(Color::White))
        .highlight_style(Style::default().fg(Color::Yellow).bold());
    frame.render_widget(tabs, outer[0]);

    // Content based on selected tab
    match app.tab {
        0 => render_overview(frame, app, outer[1]),
        1 => render_logs(frame, app, outer[1]),
        2 => render_config(frame, outer[1]),
        _ => {}
    }

    // Status bar
    let uptime = app.start_time.elapsed().as_secs();
    let status = Paragraph::new(format!(
        " Uptime: {}m {}s | Tab: switch | q: quit",
        uptime / 60,
        uptime % 60
    ))
    .style(Style::default().fg(Color::DarkGray));
    frame.render_widget(status, outer[2]);
}

fn render_overview(frame: &mut Frame, app: &App, area: Rect) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Percentage(50), Constraint::Percentage(50)])
        .split(area);

    // CPU sparkline
    let cpu_data: Vec<u64> = app.cpu_history.iter().map(|v| *v as u64).collect();
    let cpu_spark = Sparkline::default()
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(format!(
                    "CPU: {:.1}%",
                    app.cpu_history.last().unwrap_or(&0.0)
                )),
        )
        .data(&cpu_data)
        .max(100)
        .style(Style::default().fg(Color::Green));
    frame.render_widget(cpu_spark, chunks[0]);

    // Memory gauge
    let mem_pct = *app.mem_history.last().unwrap_or(&0.0);
    let mem_color = if mem_pct > 80.0 {
        Color::Red
    } else if mem_pct > 60.0 {
        Color::Yellow
    } else {
        Color::Green
    };

    let mem_gauge = Gauge::default()
        .block(Block::default().borders(Borders::ALL).title("Memory"))
        .gauge_style(Style::default().fg(mem_color))
        .percent(mem_pct as u16)
        .label(format!("{:.1}%", mem_pct));
    frame.render_widget(mem_gauge, chunks[1]);
}

fn render_logs(frame: &mut Frame, app: &App, area: Rect) {
    let items: Vec<ListItem> = app
        .logs
        .iter()
        .rev()
        .take(area.height as usize)
        .map(|log| ListItem::new(log.as_str()))
        .collect();

    let log_list = List::new(items)
        .block(Block::default().borders(Borders::ALL).title("Logs (newest first)"));
    frame.render_widget(log_list, area);
}

fn render_config(frame: &mut Frame, area: Rect) {
    let rows = vec![
        Row::new(vec!["server.host", "0.0.0.0"]),
        Row::new(vec!["server.port", "8080"]),
        Row::new(vec!["log.level", "info"]),
        Row::new(vec!["db.pool_size", "10"]),
        Row::new(vec!["db.timeout", "30s"]),
    ];

    let widths = [Constraint::Percentage(50), Constraint::Percentage(50)];
    let table = Table::new(rows, widths)
        .block(Block::default().borders(Borders::ALL).title("Configuration"))
        .header(
            Row::new(vec!["Key", "Value"])
                .style(Style::default().bold())
                .bottom_margin(1),
        )
        .highlight_style(Style::default().bg(Color::DarkGray));

    frame.render_widget(table, area);
}

fn handle_events(app: &mut App) -> io::Result<()> {
    if event::poll(std::time::Duration::from_millis(200))? {
        if let Event::Key(key) = event::read()? {
            if key.kind == KeyEventKind::Press {
                match key.code {
                    KeyCode::Char('q') => app.running = false,
                    KeyCode::Tab => app.tab = (app.tab + 1) % 3,
                    KeyCode::BackTab => {
                        app.tab = if app.tab == 0 { 2 } else { app.tab - 1 }
                    }
                    _ => {}
                }
            }
        }
    }
    Ok(())
}

fn main() -> io::Result<()> {
    // Install panic hook that restores terminal before printing panic
    let original_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |panic_info| {
        let _ = disable_raw_mode();
        let _ = execute!(io::stdout(), LeaveAlternateScreen);
        original_hook(panic_info);
    }));

    enable_raw_mode()?;
    execute!(io::stdout(), EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(io::stdout());
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new();

    while app.running {
        app.tick();
        terminal.draw(|frame| ui(frame, &app))?;
        handle_events(&mut app)?;
    }

    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;

    Ok(())
}

use crossterm::terminal::{disable_raw_mode, enable_raw_mode};
use crossterm::{execute, terminal::{EnterAlternateScreen, LeaveAlternateScreen}};
```

The panic hook is important — without it, a panic leaves the terminal in raw mode with the alternate screen still active. The user sees nothing and their terminal is broken until they type `reset` blindly.

## Widget Catalog

ratatui ships with a solid set of widgets:

- **Paragraph** — text with wrapping, scrolling, styling
- **List** — selectable list with highlight
- **Table** — rows and columns with headers
- **Tabs** — tab bar for switching views
- **Gauge** — progress bar / percentage indicator
- **Sparkline** — inline mini-chart
- **BarChart** — vertical bar chart
- **Canvas** — draw arbitrary shapes, lines, maps
- **Block** — container with borders and title (wraps other widgets)
- **Scrollbar** — standalone scrollbar widget

And the community has built more: `tui-textarea` for multi-line text editing, `tui-input` for single-line input, `tui-logger` for log viewing.

## Key Advice

**Don't fight the terminal.** Terminals are 80x24 by default. Your UI needs to work at that size. Test it by resizing your terminal window to something tiny.

**Use frame rate wisely.** Don't redraw at 60fps unless something is animating. For most dashboards, 4-5fps (200ms poll timeout) is plenty. For input-heavy apps, draw only when an event arrives.

**Handle resize events.** crossterm sends `Event::Resize(width, height)`. Your layout should adapt — that's why constraint-based layouts exist.

**Keep state and rendering separate.** If your `ui()` function is modifying state, you've got a bug waiting to happen. Render from state, update state from events. Never mix them.

ratatui has become the de facto standard for Rust TUIs. Tools like `gitui`, `bottom`, and `diskonaut` are all built on it. The immediate-mode approach means your UI code is just functions — easy to test, easy to refactor, easy to understand.

Last lesson coming up — testing CLI applications end-to-end.
