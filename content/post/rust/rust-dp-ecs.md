---
title: "Lesson 9: Entity Component System — Data-oriented design"
author: Atharva Pandey
series: "Rust Design Patterns"
lesson: 9
keywords: [Rust, rust, design patterns, ECS, entity component system, data-oriented design, game architecture, bevy]
tags: [Rust tutorial, rust, design-patterns, architecture]
date: '2025-10-19T15:30:00.000Z'
---

I spent years thinking about game objects the OOP way. A `Player` extends `Character` extends `Entity`. A `Goblin` extends `Enemy` extends `Character` extends `Entity`. Then someone asks "what if a goblin can be mind-controlled and act like a player?" and your inheritance hierarchy collapses.

ECS — Entity Component System — is the answer that the game development world converged on, and it's fundamentally a Rust-shaped idea. Data and behavior are separated. Composition replaces inheritance. Cache-friendly memory layouts replace pointer-chasing object graphs. Rust's ownership model maps onto ECS so naturally that Bevy — the most popular Rust game engine — is built entirely around it.

But ECS isn't just for games. It's a general-purpose architecture pattern that works anywhere you have entities with varying, dynamic combinations of properties.

## The Three Parts

**Entities** are just IDs. No data, no behavior. A `u64` or a newtype around one.

**Components** are plain data. Position, velocity, health, sprite, name — each one is a struct with no methods (or minimal methods). An entity "has" components attached to it.

**Systems** are functions that operate on entities with specific component combinations. "Move all entities that have Position and Velocity." "Render all entities that have Position and Sprite." Systems contain *all* the behavior.

This is the opposite of OOP. In OOP, data and behavior live together in objects. In ECS, data lives in components, behavior lives in systems, and entities are just the glue.

## Building a Minimal ECS

Let's build one from scratch. No crates, no macros — just the raw pattern:

```rust
use std::any::{Any, TypeId};
use std::collections::HashMap;

pub type Entity = u64;

pub struct World {
    next_entity: Entity,
    // component_type -> (entity -> component_data)
    components: HashMap<TypeId, HashMap<Entity, Box<dyn Any>>>,
}

impl World {
    pub fn new() -> Self {
        Self {
            next_entity: 0,
            components: HashMap::new(),
        }
    }

    pub fn spawn(&mut self) -> Entity {
        let entity = self.next_entity;
        self.next_entity += 1;
        entity
    }

    pub fn add_component<C: 'static>(&mut self, entity: Entity, component: C) {
        self.components
            .entry(TypeId::of::<C>())
            .or_default()
            .insert(entity, Box::new(component));
    }

    pub fn get_component<C: 'static>(&self, entity: Entity) -> Option<&C> {
        self.components
            .get(&TypeId::of::<C>())?
            .get(&entity)?
            .downcast_ref()
    }

    pub fn get_component_mut<C: 'static>(&mut self, entity: Entity) -> Option<&mut C> {
        self.components
            .get_mut(&TypeId::of::<C>())?
            .get_mut(&entity)?
            .downcast_mut()
    }

    pub fn remove_component<C: 'static>(&mut self, entity: Entity) {
        if let Some(storage) = self.components.get_mut(&TypeId::of::<C>()) {
            storage.remove(&entity);
        }
    }

    pub fn despawn(&mut self, entity: Entity) {
        for storage in self.components.values_mut() {
            storage.remove(&entity);
        }
    }
}
```

Components are just regular structs:

```rust
#[derive(Debug, Clone)]
pub struct Position {
    pub x: f32,
    pub y: f32,
}

#[derive(Debug, Clone)]
pub struct Velocity {
    pub dx: f32,
    pub dy: f32,
}

#[derive(Debug, Clone)]
pub struct Health {
    pub current: i32,
    pub max: i32,
}

#[derive(Debug, Clone)]
pub struct Name(pub String);

#[derive(Debug, Clone)]
pub struct Renderable {
    pub sprite: String,
    pub z_order: i32,
}
```

## Querying: The Heart of ECS

Systems need to find entities with specific component combinations. Let's add query support:

```rust
impl World {
    /// Get all entities that have both Position and Velocity
    pub fn query_two<A: 'static, B: 'static>(
        &self,
    ) -> Vec<(Entity, &A, &B)> {
        let storage_a = match self.components.get(&TypeId::of::<A>()) {
            Some(s) => s,
            None => return vec![],
        };
        let storage_b = match self.components.get(&TypeId::of::<B>()) {
            Some(s) => s,
            None => return vec![],
        };

        storage_a
            .iter()
            .filter_map(|(&entity, comp_a)| {
                let a = comp_a.downcast_ref::<A>()?;
                let b = storage_b.get(&entity)?.downcast_ref::<B>()?;
                Some((entity, a, b))
            })
            .collect()
    }

    pub fn query_two_mut<A: 'static, B: 'static>(
        &mut self,
    ) -> Vec<(Entity, &mut A, &mut B)> {
        // This is where it gets tricky with Rust's borrow checker.
        // We need unsafe or a different storage strategy.
        // For now, let's use a simpler approach:
        let entities: Vec<Entity> = {
            let storage_a = match self.components.get(&TypeId::of::<A>()) {
                Some(s) => s,
                None => return vec![],
            };
            let storage_b = match self.components.get(&TypeId::of::<B>()) {
                Some(s) => s,
                None => return vec![],
            };
            storage_a
                .keys()
                .filter(|e| storage_b.contains_key(e))
                .copied()
                .collect()
        };

        // Process one at a time — not ideal for performance,
        // but safe and demonstrates the pattern
        let mut results = Vec::new();
        for entity in entities {
            // Safe because A and B are different types (different TypeIds)
            let a = self.components
                .get_mut(&TypeId::of::<A>())
                .unwrap()
                .get_mut(&entity)
                .unwrap()
                .downcast_mut::<A>()
                .unwrap();
            let a_ptr = a as *mut A;

            let b = self.components
                .get_mut(&TypeId::of::<B>())
                .unwrap()
                .get_mut(&entity)
                .unwrap()
                .downcast_mut::<B>()
                .unwrap();
            let b_ptr = b as *mut B;

            // SAFETY: A and B are guaranteed to be different types
            // (TypeId::of::<A>() != TypeId::of::<B>()), so these
            // point to different HashMap entries.
            unsafe {
                results.push((entity, &mut *a_ptr, &mut *b_ptr));
            }
        }
        results
    }
}
```

Yeah — the mutable query is ugly. This is genuinely hard with Rust's borrow checker, because you're trying to get mutable references to different parts of the same data structure. Real ECS crates like `hecs` and `bevy_ecs` solve this with carefully written unsafe code and archetype-based storage. But the concept is clear: systems query for entities with specific component sets.

## Writing Systems

Systems are just functions that take a `&mut World`:

```rust
fn movement_system(world: &mut World) {
    // Collect the data we need first
    let updates: Vec<(Entity, f32, f32)> = world
        .query_two::<Position, Velocity>()
        .iter()
        .map(|&(entity, pos, vel)| {
            (entity, pos.x + vel.dx, pos.y + vel.dy)
        })
        .collect();

    // Apply updates
    for (entity, new_x, new_y) in updates {
        if let Some(pos) = world.get_component_mut::<Position>(entity) {
            pos.x = new_x;
            pos.y = new_y;
        }
    }
}

fn damage_system(world: &mut World) {
    // Find all entities with health below zero and despawn them
    let dead: Vec<Entity> = world
        .query_two::<Health, Name>()
        .iter()
        .filter(|(_, health, _)| health.current <= 0)
        .map(|&(entity, _, name)| {
            println!("{} has been destroyed!", name.0);
            entity
        })
        .collect();

    for entity in dead {
        world.despawn(entity);
    }
}

fn render_system(world: &World) {
    let mut renderables: Vec<(Entity, &Position, &Renderable)> =
        world.query_two::<Position, Renderable>();

    // Sort by z-order
    renderables.sort_by_key(|(_, _, r)| r.z_order);

    for (_, pos, renderable) in renderables {
        println!(
            "Draw '{}' at ({:.1}, {:.1}) z={}",
            renderable.sprite, pos.x, pos.y, renderable.z_order
        );
    }
}
```

## Putting It Together

```rust
fn main() {
    let mut world = World::new();

    // Spawn a player
    let player = world.spawn();
    world.add_component(player, Name("Player".into()));
    world.add_component(player, Position { x: 0.0, y: 0.0 });
    world.add_component(player, Velocity { dx: 1.0, dy: 0.5 });
    world.add_component(player, Health { current: 100, max: 100 });
    world.add_component(player, Renderable {
        sprite: "@".into(),
        z_order: 10,
    });

    // Spawn an enemy
    let goblin = world.spawn();
    world.add_component(goblin, Name("Goblin".into()));
    world.add_component(goblin, Position { x: 5.0, y: 3.0 });
    world.add_component(goblin, Velocity { dx: -0.5, dy: 0.0 });
    world.add_component(goblin, Health { current: 0, max: 30 });
    world.add_component(goblin, Renderable {
        sprite: "g".into(),
        z_order: 5,
    });

    // Spawn a static decoration (no velocity, no health)
    let tree = world.spawn();
    world.add_component(tree, Name("Oak Tree".into()));
    world.add_component(tree, Position { x: 3.0, y: 2.0 });
    world.add_component(tree, Renderable {
        sprite: "T".into(),
        z_order: 1,
    });

    // Game loop
    for tick in 0..3 {
        println!("=== Tick {} ===", tick);
        movement_system(&mut world);
        damage_system(&mut world);
        render_system(&world);
        println!();
    }
}
```

The tree doesn't move — it has no `Velocity`. The goblin gets destroyed in tick 0 because its health is zero. The player moves each tick. Each system only touches the entities it cares about. Adding new entity types requires *zero* changes to existing systems.

## Why ECS Matters Beyond Games

I've used ECS-style architecture in non-game contexts:

**Data pipelines.** Entities are records. Components are fields that may or may not be present (parsed email, geocoded address, enriched metadata). Systems are processing stages.

**IoT monitoring.** Entities are devices. Components are sensor readings, connection status, alert state. Systems check thresholds, aggregate metrics, send notifications.

**Document processing.** Entities are documents. Components are extracted metadata, OCR text, classification labels. Systems run extraction, classification, and routing.

The pattern works whenever you have a large number of "things" with varying, changing properties and you need to process them in bulk by property combinations.

## ECS vs. OOP: The Trade-offs

I won't pretend ECS is always better. Here are the real trade-offs:

**ECS wins at:**
- Flexibility — add/remove capabilities at runtime by adding/removing components
- Performance — cache-friendly iteration over contiguous component arrays
- Parallelism — systems that touch different components can run in parallel
- Avoiding the diamond inheritance problem

**OOP wins at:**
- Encapsulation — ECS exposes all data to all systems
- Discoverability — "what can a Player do?" is easier when methods are on the Player class
- Small-scale simplicity — for 5 entity types with fixed properties, ECS is overkill

In Rust specifically, ECS avoids the inheritance problem entirely (Rust doesn't have inheritance), and the borrow checker's rules align well with ECS's "systems operate on disjoint component sets" model. It's not a coincidence that Bevy — one of the most popular Rust projects — chose ECS as its core architecture.

## Going Further

Our toy ECS uses `HashMap<Entity, Box<dyn Any>>`, which is slow. Real ECS implementations use:

- **Archetype storage** (Bevy, hecs): groups entities by their component set, stores components in contiguous arrays
- **Sparse sets** (EnTT-style): fast insertion/removal at the cost of some iteration speed
- **Bitset-based** (specs): uses bitsets to track which entities have which components

If you're building something real, use `bevy_ecs` (even without the rest of Bevy), `hecs`, or `legion`. They handle the gnarly unsafe code, the storage optimization, and the parallel system scheduling that a production ECS needs.

But understanding the pattern from scratch — entities as IDs, components as data, systems as functions — gives you the mental model to use any ECS framework effectively. It's the most Rust-native architecture pattern I know.
