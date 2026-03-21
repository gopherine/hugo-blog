---
title: "Lesson 5: Pagination Done Right — Offset pagination breaks at scale"
author: Atharva Pandey
series: "Go API and Service Design"
lesson: 5
keywords: [Go, golang, API, backend, pagination, cursor pagination, keyset pagination]
tags: [Go tutorial, golang, backend]
date: '2024-10-28T00:00:00.000Z'
---

I have shipped offset-based pagination more times than I would like to admit. It looks clean, it is trivially easy to implement, and it works perfectly — until the table reaches about 100,000 rows. Then it starts to slow down, and by a million rows it is actively harmful. I had a support dashboard that froze every time someone clicked to page 47. That was the moment I stopped using `OFFSET`.

## The Problem

Offset pagination (`LIMIT n OFFSET m`) is intuitive: skip `m` rows, return the next `n`. The problem is what happens under the hood. Most databases cannot jump directly to row `m` — they scan through all the rows before it and discard them. For page 1, the cost is low. For page 100 with a page size of 50, the database reads 5,000 rows and discards 4,950 of them. For page 1,000, it reads 50,000 rows to return 50. The query time scales linearly with the page number.

There is also a data consistency problem. If a row is inserted or deleted between page fetches, rows shift. The user might see a duplicate (a row that was on page 1 is now the last row of page 2) or miss a row entirely. For a list of financial transactions or a feed of events, that is unacceptable.

## The Idiomatic Way

Cursor-based pagination avoids both problems. Instead of telling the database to skip `m` rows, you tell it to return rows where an indexed column is greater or less than a cursor value you received from the previous page. The database finds that value using an index and starts reading from there. The cost is constant regardless of which page you are on.

Here is the core implementation:

```go
package api

import (
    "encoding/base64"
    "encoding/json"
    "fmt"
    "time"
)

// Cursor encodes the pagination position opaquely so callers cannot rely on its structure
type Cursor struct {
    ID        string    `json:"id"`
    CreatedAt time.Time `json:"created_at"`
}

func EncodeCursor(c Cursor) string {
    b, _ := json.Marshal(c)
    return base64.URLEncoding.EncodeToString(b)
}

func DecodeCursor(s string) (Cursor, error) {
    b, err := base64.URLEncoding.DecodeString(s)
    if err != nil {
        return Cursor{}, fmt.Errorf("invalid cursor")
    }
    var c Cursor
    if err := json.Unmarshal(b, &c); err != nil {
        return Cursor{}, fmt.Errorf("invalid cursor")
    }
    return c, nil
}

// Page is the response envelope for paginated endpoints
type Page[T any] struct {
    Items      []T    `json:"items"`
    NextCursor string `json:"next_cursor,omitempty"` // absent on last page
    HasMore    bool   `json:"has_more"`
}
```

The repository query uses the cursor to anchor the WHERE clause:

```go
func (r *UserRepository) List(ctx context.Context, limit int, cursor *Cursor) ([]User, error) {
    if cursor == nil {
        // First page — no WHERE clause needed
        rows, err := r.db.QueryContext(ctx,
            `SELECT id, name, email, created_at
             FROM users
             ORDER BY created_at DESC, id DESC
             LIMIT $1`,
            limit+1, // fetch one extra to determine HasMore
        )
        return scanUsers(rows, err)
    }

    // Subsequent pages — use keyset condition
    rows, err := r.db.QueryContext(ctx,
        `SELECT id, name, email, created_at
         FROM users
         WHERE (created_at, id) < ($1, $2)
         ORDER BY created_at DESC, id DESC
         LIMIT $3`,
        cursor.CreatedAt, cursor.ID, limit+1,
    )
    return scanUsers(rows, err)
}
```

Fetching `limit+1` rows is a standard trick for detecting whether more pages exist without a separate `COUNT` query. If you get `limit+1` rows back, there is a next page. Return only `limit` rows to the client and encode the last one as the cursor.

```go
func (s *Server) handleListUsers(w http.ResponseWriter, r *http.Request) {
    limit, err := QueryInt(r, "limit", 20, 1, 100)
    if err != nil {
        WriteError(w, r, http.StatusBadRequest, "invalid_param", err.Error(), nil)
        return
    }

    var cursor *Cursor
    if raw := r.URL.Query().Get("cursor"); raw != "" {
        c, err := DecodeCursor(raw)
        if err != nil {
            WriteError(w, r, http.StatusBadRequest, "invalid_cursor", "Invalid pagination cursor.", nil)
            return
        }
        cursor = &c
    }

    users, err := s.users.List(r.Context(), limit, cursor)
    if err != nil {
        s.logger.Error("list users failed", "error", err)
        WriteError(w, r, http.StatusInternalServerError, "internal_error", "Failed to list users.", nil)
        return
    }

    page := Page[User]{Items: users, HasMore: false}

    if len(users) > limit {
        page.HasMore = true
        page.Items = users[:limit]
        last := page.Items[len(page.Items)-1]
        page.NextCursor = EncodeCursor(Cursor{
            ID:        last.ID,
            CreatedAt: last.CreatedAt,
        })
    }

    writeJSON(w, http.StatusOK, page)
}
```

The response looks like:

```json
{
  "items": [...],
  "next_cursor": "eyJpZCI6IjEyMyIsImNyZWF0ZWRfYXQiOiIyMDI0LTAxLTAxVDAwOjAwOjAwWiJ9",
  "has_more": true
}
```

Clients that want the next page pass `?cursor=<next_cursor>`. When `has_more` is `false`, they stop fetching. The cursor is opaque base64 JSON — callers should treat it as a black box.

## In The Wild

The compound sort key `(created_at DESC, id DESC)` is deliberate. If you sort only by `created_at`, two rows created at the exact same millisecond are unstable in sort order. The database might return them in different order across queries. Adding `id` as a tiebreaker makes the sort completely deterministic. Your cursor needs to encode both fields for the WHERE clause to work correctly.

Ensure the database has a compound index:

```sql
CREATE INDEX idx_users_created_at_id ON users (created_at DESC, id DESC);
```

Without this index, even cursor pagination degrades to a full table scan. The query planner needs to be able to seek to the cursor position efficiently.

## The Gotchas

**Backward pagination is harder.** Cursor pagination naturally goes forward. If you need "previous page" functionality, you either store the cursor history client-side or implement a bidirectional cursor, which requires reversing the ORDER BY and then re-reversing the results. For most APIs, forward-only pagination is sufficient. If you need bidirectional, consider a framework like Relay's connection spec.

**Cursor expiry.** If a user bookmarks a cursor and comes back after a row deletion that shifts the data, cursor pagination handles it gracefully — it picks up from the nearest existing row. But if you delete the row that the cursor points to exactly, the next fetch will start from the first row after the gap. This is usually acceptable behaviour; document it.

**Do not expose cursor internals.** Encoding the cursor as base64 JSON does not prevent a clever client from decoding and manipulating it. If the cursor encodes a `created_at` timestamp that gets injected into a SQL query, validate it before use. Use parameterised queries (as shown above) and never interpolate cursor values directly into SQL strings.

**Count queries are expensive.** Offset pagination often includes a `SELECT COUNT(*)` to show "page 3 of 47." Cursor pagination does not support this naturally. If you need a total count, run it asynchronously, cache it, and accept that it may be slightly stale. Exact live counts on large tables are expensive enough to be worth avoiding.

## Key Takeaway

Offset pagination is a trap that works in development and fails in production. The cost grows linearly with page number, and concurrent writes cause duplicates and gaps. Cursor-based pagination with a compound keyset gives you constant-time page fetches regardless of dataset size and stable results even as data changes. Encode the cursor as opaque base64, detect next-page existence by fetching one extra row, and index the sort columns. Your paginated endpoints will scale to hundreds of millions of rows without slowing down.

---

**Series: Go API and Service Design**

[← Lesson 4: Error Responses](/post/go/go-api-error-responses) | [Lesson 6: Idempotency in APIs →](/post/go/go-api-idempotency)
