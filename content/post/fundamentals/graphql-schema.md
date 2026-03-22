---
title: "Lesson 2: Schema Design — Types, queries, mutations, and subscriptions"
author: Atharva Pandey
keywords:
  - GraphQL schema design
  - GraphQL types
  - queries mutations subscriptions
  - schema-first development
  - GraphQL best practices
  - API design
tags:
  - fundamentals
  - GraphQL
  - API
series: "GraphQL in Practice"
lesson: 2
date: '2024-09-08T00:00:00.000Z'
---

The schema is the most important artifact in any GraphQL API. It is simultaneously the contract between your client and server, the documentation for every engineer who works with the API, and the boundary that forces you to think clearly about your domain before writing any implementation code. A well-designed schema makes everything easier. A poorly designed schema compounds every mistake downstream.

I've seen both. The experience of inheriting a badly designed GraphQL schema — full of inconsistent naming, misused types, nullable fields everywhere because someone wasn't sure — is one of the more persistent forms of technical debt I've encountered. Unlike a poorly written function, a bad schema is public-facing. You can't just refactor it; you have to version and deprecate carefully.

## Schema-First vs Code-First

Before getting into the specifics, it's worth noting the two main approaches to writing a GraphQL schema.

In **schema-first** development, you write your schema definition language (SDL) file first — the `.graphql` file that defines your types — and generate server stubs from it. The schema is the source of truth.

In **code-first** development, you define types in code (in whatever language you're using) and generate the SDL from them. The code is the source of truth.

Both approaches work. I generally prefer schema-first because it forces you to think about the API contract before you get distracted by implementation details. You can share the SDL with frontend engineers before a single resolver is written. It also makes schema evolution more deliberate — you have to explicitly change the SDL, not just accidentally change the generated output by refactoring code.

Code-first tends to win in teams where type safety is paramount and the overhead of keeping SDL and code in sync feels burdensome. Libraries like gqlgen (for Go) and TypeGraphQL (for Node) make code-first more ergonomic.

## Types: The Vocabulary of Your Schema

**Scalar types** are the primitives: `String`, `Int`, `Float`, `Boolean`, `ID`. You can define custom scalars for things like `DateTime`, `URL`, or `JSON`. Use custom scalars when the built-in types don't carry enough semantic meaning. A field typed as `String` could be anything; a field typed as `DateTime` tells the client how to parse and display it.

**Object types** are the core building blocks. They represent entities in your domain. Keep object types focused on domain concepts, not on UI screens. A common mistake is creating a type called `UserProfilePageData` that bundles together everything a specific screen needs. This couples your schema to your current UI, which is exactly backwards. UI changes; domain concepts are more stable.

**Input types** are used for arguments to mutations. They are distinct from object types, which is occasionally annoying but important — input types cannot have circular references and cannot have resolver functions, which keeps mutation arguments clean and serializable.

**Enums** are underused in most schemas I've seen. If a field has a fixed set of valid values — a status field, a category, a priority level — make it an enum, not a string. Enums are documented in the schema, validated at the protocol level, and caught by static analysis in typed clients. A string can be anything; an enum makes the valid set explicit.

**Interfaces and unions** handle polymorphism. Use an interface when types share fields but have different implementations. Use a union when you need to return one of several completely different types. A search result that can be a `Post`, `User`, or `Comment` is a natural union. A `Node` interface with an `id` field that every entity implements is a natural interface pattern (Relay uses this heavily).

## Nullability: Make It a Conscious Choice

By default in GraphQL, fields are nullable. This is one of the spec's most controversial decisions, and in my experience it's responsible for a lot of messy schemas.

If you don't think about nullability and just accept the defaults, you end up with clients that have to handle null at every level — `user?.profile?.avatar?.url` — even for fields that are logically always present. Every null check is cognitive overhead and a potential source of bugs.

My rule: make fields non-null by default. Use `!` (non-null in SDL) aggressively. Only make a field nullable when there is a genuine business reason it might be absent. If a `User` always has an `email`, that field should be `email: String!`. If a user profile sometimes has an avatar and sometimes doesn't, `avatar: String` is nullable for a real reason.

The corollary: be careful about making list items nullable. `[Post]` means a list where each element could be null. `[Post!]!` means a non-null list of non-null posts. The latter is usually what you want.

## Query Design

Queries represent read operations. They are idempotent and should have no side effects.

Name queries as nouns or noun phrases: `user(id: ID!): User`, `posts(filter: PostFilter): [Post!]!`. Avoid verbs in query names — verbs signal actions, which is the mutation domain.

Design for the client, but don't design for specific clients. There's a difference. Designing for clients means thinking about what data consumers actually need to compose screens, not just exposing raw domain entities. Designing for specific clients means building `userProfileQuery` that returns exactly the fields for one screen — which you want to avoid.

Pagination deserves its own mention. If a query can return many results, it must be paginated. Not "should" — must. The two dominant patterns are offset/limit pagination and cursor-based pagination. Cursor-based (as specified by the Relay Connection spec) is more robust for large, frequently-updated datasets. It's also more complex to implement. Offset pagination is simpler and works fine for datasets that don't change between pages.

For new schemas, I default to cursor-based pagination from the start. Retrofitting it later is painful.

## Mutation Design

Mutations represent write operations. Every mutation should have its own named input type and its own named return type.

This pattern — `createUser(input: CreateUserInput!): CreateUserPayload!` — seems verbose at first, but it pays dividends. You can add fields to the input without changing the mutation signature. You can return the updated entity, validation errors, and metadata in the payload without breaking clients. The payload pattern is particularly valuable: return the entity you just created or updated so clients don't need a follow-up query.

Be specific about what each mutation does. `updateUser` that accepts an input with 15 optional fields is hard to reason about — what happens if you pass nothing? What's the behavior for fields you don't include? Prefer narrow mutations: `updateUserEmail`, `updateUserDisplayName`. Each does one thing with clear semantics.

For operations that can fail in business-logic ways — creating a user who already exists, publishing a post that fails validation — model failures in the type system rather than throwing errors. A `CreateUserPayload` with a union return type or an `errors` field lets clients handle domain failures with the same type-safe flow as successes, rather than parsing error messages from exceptions.

## Subscriptions

Subscriptions give clients real-time updates over a persistent connection. They are the most operationally complex part of a GraphQL API.

Use subscriptions when you genuinely need low-latency push — a live chat, a collaborative editing cursor, a real-time notification. Don't use them just because your data changes. Polling a query every 30 seconds is simpler to implement, simpler to debug, and sufficient for most use cases. Subscriptions require WebSocket infrastructure, connection management, and careful thought about message ordering.

If you do use subscriptions, keep them narrow. A subscription that broadcasts every field change on a large entity to every connected client will overwhelm clients and produce chatty WebSocket traffic. Subscribe to specific events and return minimal payloads. Let the client query for full details when it needs them.

## Schema Versioning and Deprecation

GraphQL's recommended approach to versioning is to not version — instead, evolve the schema and use the `@deprecated` directive to mark fields that should no longer be used. This works well in practice.

Add new fields freely. Removing fields requires caution: mark them deprecated, communicate with clients, monitor usage, then remove. Most GraphQL servers emit deprecation warnings in introspection that tooling surfaces to developers.

The practical challenge is knowing when a deprecated field has zero clients left. This requires query-level analytics — tracking which fields are actually queried in production. Building that instrumentation early, before you have technical debt to clean up, is worth the investment.

## The Schema Review

Before shipping a schema, I do a quick checklist review:

- Are all enum fields actually enums, not strings?
- Are non-null fields truly always present?
- Do all mutations have dedicated input and payload types?
- Are all list queries paginated?
- Are field names consistent across types? (camelCase in SDL, consistent verb tenses in mutations)
- Are there any fields that expose internal IDs, implementation details, or database column names that clients don't need?

The last one gets missed often. Your schema is a public interface. Exposing `created_at_unix_timestamp` because that's what your database column is called couples clients to your storage schema. Expose `createdAt: DateTime!` and translate in the resolver.

The next lesson covers implementing all of this in Go with gqlgen, including resolvers, middleware, and DataLoader for the N+1 problem we discussed in the first lesson.
