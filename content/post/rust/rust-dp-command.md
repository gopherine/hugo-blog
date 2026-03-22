---
title: "Lesson 4: Command Pattern — Closures as commands"
author: Atharva Pandey
series: "Rust Design Patterns"
lesson: 4
keywords: [Rust, rust, design patterns, command pattern, closures, undo redo, functional programming]
tags: [Rust tutorial, rust, design-patterns, architecture]
date: '2025-10-07T09:20:00.000Z'
---

The Command pattern has always struck me as one of those patterns that's really just "wrap a function call in an object." In Java, you create a `Command` interface with an `execute()` method, make a class for each command, and pass them around. It takes about 40 lines of boilerplate to do what a lambda does in one.

In Rust, closures *are* the Command pattern. But when you need undo, history, or serialization, you still need the structured version — and Rust's ownership model makes the undo/redo part surprisingly elegant.

## Closures: Commands for Free

Let's start with the simplest version. If all you need is deferred execution, Rust closures are already commands:

```rust
type Command = Box<dyn FnOnce()>;

fn execute_later(commands: Vec<Command>) {
    for cmd in commands {
        cmd();
    }
}

fn main() {
    let name = String::from("database");

    let commands: Vec<Command> = vec![
        Box::new(|| println!("Connecting...")),
        Box::new(move || println!("Connected to {}", name)),
        Box::new(|| println!("Running migrations...")),
    ];

    execute_later(commands);
}
```

`FnOnce` means each command can be executed exactly once — it consumes its captured environment. That's perfect for one-shot operations like "run this migration" or "send this notification."

For reusable commands, use `Fn`:

```rust
type ReusableCommand = Box<dyn Fn()>;

struct Scheduler {
    tasks: Vec<ReusableCommand>,
}

impl Scheduler {
    fn new() -> Self {
        Self { tasks: Vec::new() }
    }

    fn add_task(&mut self, task: impl Fn() + 'static) {
        self.tasks.push(Box::new(task));
    }

    fn run_all(&self) {
        for task in &self.tasks {
            task();
        }
    }
}
```

That's it. No `Command` interface, no `ConcreteCommand` classes. Closures with captured state *are* the pattern.

## When You Need More Than Closures

Closures fall short when you need:

1. **Undo/redo** — closures can't easily reverse themselves
2. **Serialization** — you can't serialize a closure
3. **Inspection** — you can't ask a closure what it does
4. **Composition** — combining commands with metadata

For these cases, you need the structured Command pattern. Here's how I build it:

```rust
pub trait Command {
    fn execute(&mut self, doc: &mut Document);
    fn undo(&mut self, doc: &mut Document);
    fn description(&self) -> &str;
}

#[derive(Debug, Clone)]
pub struct Document {
    pub content: String,
    pub cursor: usize,
}

impl Document {
    pub fn new() -> Self {
        Self {
            content: String::new(),
            cursor: 0,
        }
    }
}
```

Now specific commands:

```rust
pub struct InsertText {
    position: usize,
    text: String,
}

impl InsertText {
    pub fn new(position: usize, text: impl Into<String>) -> Self {
        Self {
            position,
            text: text.into(),
        }
    }
}

impl Command for InsertText {
    fn execute(&mut self, doc: &mut Document) {
        doc.content.insert_str(self.position, &self.text);
        doc.cursor = self.position + self.text.len();
    }

    fn undo(&mut self, doc: &mut Document) {
        let end = self.position + self.text.len();
        doc.content.drain(self.position..end);
        doc.cursor = self.position;
    }

    fn description(&self) -> &str {
        "Insert text"
    }
}

pub struct DeleteRange {
    start: usize,
    end: usize,
    deleted_text: Option<String>, // saved for undo
}

impl DeleteRange {
    pub fn new(start: usize, end: usize) -> Self {
        Self {
            start,
            end,
            deleted_text: None,
        }
    }
}

impl Command for DeleteRange {
    fn execute(&mut self, doc: &mut Document) {
        // Save deleted text for undo
        self.deleted_text = Some(doc.content[self.start..self.end].to_string());
        doc.content.drain(self.start..self.end);
        doc.cursor = self.start;
    }

    fn undo(&mut self, doc: &mut Document) {
        if let Some(ref text) = self.deleted_text {
            doc.content.insert_str(self.start, text);
            doc.cursor = self.start + text.len();
        }
    }

    fn description(&self) -> &str {
        "Delete range"
    }
}

pub struct ReplaceAll {
    pattern: String,
    replacement: String,
    original_content: Option<String>,
}

impl ReplaceAll {
    pub fn new(pattern: impl Into<String>, replacement: impl Into<String>) -> Self {
        Self {
            pattern: pattern.into(),
            replacement: replacement.into(),
            original_content: None,
        }
    }
}

impl Command for ReplaceAll {
    fn execute(&mut self, doc: &mut Document) {
        self.original_content = Some(doc.content.clone());
        doc.content = doc.content.replace(&self.pattern, &self.replacement);
    }

    fn undo(&mut self, doc: &mut Document) {
        if let Some(ref original) = self.original_content {
            doc.content = original.clone();
        }
    }

    fn description(&self) -> &str {
        "Replace all"
    }
}
```

Notice how `DeleteRange` saves the deleted text inside itself for undo. That's the key insight — *commands own their undo state*. In Java you'd use fields the same way, but Rust's ownership model makes the lifecycle crystal clear. The command struct is created, it captures undo data during execution, and it uses that data on undo. When the command is dropped, the undo data is dropped with it.

## The Command History

Now wire it up with undo/redo:

```rust
pub struct Editor {
    document: Document,
    history: Vec<Box<dyn Command>>,
    undo_stack: Vec<Box<dyn Command>>,
}

impl Editor {
    pub fn new() -> Self {
        Self {
            document: Document::new(),
            history: Vec::new(),
            undo_stack: Vec::new(),
        }
    }

    pub fn execute(&mut self, mut cmd: Box<dyn Command>) {
        cmd.execute(&mut self.document);
        println!("Executed: {}", cmd.description());
        self.history.push(cmd);
        self.undo_stack.clear(); // new action invalidates redo
    }

    pub fn undo(&mut self) {
        if let Some(mut cmd) = self.history.pop() {
            cmd.undo(&mut self.document);
            println!("Undone: {}", cmd.description());
            self.undo_stack.push(cmd);
        }
    }

    pub fn redo(&mut self) {
        if let Some(mut cmd) = self.undo_stack.pop() {
            cmd.execute(&mut self.document);
            println!("Redone: {}", cmd.description());
            self.history.push(cmd);
        }
    }

    pub fn content(&self) -> &str {
        &self.document.content
    }
}

fn main() {
    let mut editor = Editor::new();

    editor.execute(Box::new(InsertText::new(0, "Hello, world!")));
    println!("Content: {:?}", editor.content());

    editor.execute(Box::new(InsertText::new(5, " beautiful")));
    println!("Content: {:?}", editor.content());

    editor.execute(Box::new(ReplaceAll::new("beautiful", "cruel")));
    println!("Content: {:?}", editor.content());

    editor.undo();
    println!("After undo: {:?}", editor.content());

    editor.undo();
    println!("After undo: {:?}", editor.content());

    editor.redo();
    println!("After redo: {:?}", editor.content());
}
```

The ownership flow is clean — commands move from creation to the history stack, from the history stack to the undo stack, and back. No reference counting, no garbage collection, no dangling pointers.

## Macro Commands (Composite + Command)

Want to group multiple commands into one undoable operation? That's the Composite pattern layered on top of Command:

```rust
pub struct MacroCommand {
    name: String,
    commands: Vec<Box<dyn Command>>,
}

impl MacroCommand {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            name: name.into(),
            commands: Vec::new(),
        }
    }

    pub fn add(mut self, cmd: impl Command + 'static) -> Self {
        self.commands.push(Box::new(cmd));
        self
    }
}

impl Command for MacroCommand {
    fn execute(&mut self, doc: &mut Document) {
        for cmd in &mut self.commands {
            cmd.execute(doc);
        }
    }

    fn undo(&mut self, doc: &mut Document) {
        // Undo in reverse order — critical!
        for cmd in self.commands.iter_mut().rev() {
            cmd.undo(doc);
        }
    }

    fn description(&self) -> &str {
        &self.name
    }
}

fn main() {
    let mut editor = Editor::new();

    // Build a multi-step refactoring as one undoable action
    let refactor = MacroCommand::new("Rename variable")
        .add(ReplaceAll::new("old_name", "new_name"))
        .add(InsertText::new(0, "// Renamed old_name -> new_name\n"));

    editor.execute(Box::new(refactor));
    println!("Content: {:?}", editor.content());

    // Single undo reverts the entire macro
    editor.undo();
    println!("After undo: {:?}", editor.content());
}
```

The reverse-order undo is crucial and easy to get wrong. If you insert text and then delete text, undoing should restore the deletion first, then remove the insertion. `iter_mut().rev()` handles this.

## The Enum Approach

When your command set is closed — you know all possible commands at compile time — an enum is simpler than trait objects and avoids heap allocation:

```rust
#[derive(Debug, Clone)]
pub enum EditorCommand {
    Insert { position: usize, text: String },
    Delete { start: usize, end: usize, saved: Option<String> },
    Replace { pattern: String, replacement: String, saved: Option<String> },
}

impl EditorCommand {
    pub fn execute(&mut self, doc: &mut Document) {
        match self {
            EditorCommand::Insert { position, text } => {
                doc.content.insert_str(*position, text);
            }
            EditorCommand::Delete { start, end, saved } => {
                *saved = Some(doc.content[*start..*end].to_string());
                doc.content.drain(*start..*end);
            }
            EditorCommand::Replace { pattern, replacement, saved } => {
                *saved = Some(doc.content.clone());
                doc.content = doc.content.replace(pattern.as_str(), replacement);
            }
        }
    }

    pub fn undo(&mut self, doc: &mut Document) {
        match self {
            EditorCommand::Insert { position, text } => {
                doc.content.drain(*position..(*position + text.len()));
            }
            EditorCommand::Delete { start, saved, .. } => {
                if let Some(text) = saved {
                    doc.content.insert_str(*start, text);
                }
            }
            EditorCommand::Replace { saved, .. } => {
                if let Some(original) = saved {
                    doc.content = original.clone();
                }
            }
        }
    }
}
```

Enums are stack-allocated, `Clone`-able, and `Debug`-gable out of the box. If you need to serialize your command history — say, for collaborative editing — enums with `serde` are dead simple. Try serializing a `Box<dyn Command>`.

## When to Use What

**Closures** when you just need deferred execution. Task queues, event handlers, one-shot operations. No undo needed.

**Trait objects** when commands are open-ended — users or plugins can define new command types. Library code where you don't know all variants upfront.

**Enums** when the command set is fixed and you need serialization, cloning, or want to avoid heap allocation. Game engines, editors, anything with undo history.

The Command pattern might be the most naturally Rusty of all the GoF patterns. Closures are first-class, ownership makes undo semantics explicit, and enums give you a zero-allocation alternative that doesn't exist in most OOP languages. The pattern fits Rust's strengths like a glove.
