# Portfolio Brand Redesign — Design Spec

## Context

Atharva Pandey's Hugo blog at atharvapandey.com needs a UX overhaul. The current GitHub-style homepage shows an outdated README, scattered pinned posts, and a contribution graph — none of which communicate who Atharva is in 2026. The site now serves as a **personal brand platform** that should make visitors think "I need to work with this person" through the strength of shipped work and public thinking.

## Identity

- **Title:** Polyglot Engineer · Technical Leader
- **Tagline:** Shipping AI-native tools and distributed systems in Go, Rust, and TypeScript
- **Bio (one sentence):** Polyglot Engineer · Technical Leader. I ship AI-native tools, distributed systems, and developer platforms in Go, Rust, and TypeScript.

## Architecture: GitHub-Style Brand Card + Two-Tab Feed

Keep the GitHub-style left sidebar / right content split. Upgrade both sides.

### Left Sidebar — Brand Card

The existing `user-profile.html` partial, redesigned:

1. **Avatar** — existing, unchanged
2. **Name + username** — existing, unchanged
3. **Bio** — replace multi-line description with the one-sentence bio above
4. **Location** — existing (Bengaluru, India)
5. **Website link** — existing
6. **Social icons** — existing (GitHub, LinkedIn, X/Twitter, email). Remove the "Organizations" heading that currently wraps them.
7. **Resume link** — NEW. Subtle text link below socials: "📄 Resume" → links to `/resume.pdf` (a static PDF added to `/static/resume.pdf`). Not a tab, not a badge.
8. **Mini activity streak** — NEW. Hugo-rendered (server-side) compact heatmap: count pages by week for the last 12 weeks using Hugo's `.Site.RegularPages` grouped by `.PublishDate`. Display as a small CSS grid of colored squares + "N posts this year" text below. No client-side JS needed — pure Hugo template logic.

### Right Content Area — Two Tabs

#### Rendering Chain (Important)

The current rendering chain is: `index.html` → `partial "home.html"` → `partial "overview.html"` (registers `{{ define "overview" }}` block) + `partial "user-profile.html"` (calls `{{ block "overview" . }}`). This block/partial pattern means `overview.html` is loaded as a partial to register its block definition, which is then rendered inside `user-profile.html` at line 181.

**Strategy:** Replace the `{{ block "overview" . }}{{ end }}` call in `user-profile.html` (line 181) with a direct `{{ partial "homepage-content.html" . }}` call. Remove the `{{ define "overview" }}` wrapper from `overview.html` entirely. This eliminates the fragile block/partial dual-invocation pattern and makes the rendering chain straightforward: `user-profile.html` → `partial "homepage-content.html"`.

#### Tab Bar

Two tabs only:
- **Feed** (default, active on homepage load)
- **Articles**

Implementation: Hugo renders both tab contents into the HTML (both visible in source for SEO). JavaScript shows/hides based on active tab. URL hash (`#feed`, `#articles`) for direct linking. No page reload.

#### Feed Tab (Default)

**Data Source: GitHub Discussions (not Issues)**

Micro-posts are authored as GitHub Discussions in the `gopherine/hugo-blog` repo. Discussions provide a LinkedIn-post-like authoring experience: rich markdown preview, categories, threaded replies, upvotes.

**Discussion Categories** (create in repo settings):
- **Thoughts** — opinions, hot takes, micro-essays
- **Shipped** — project releases, tool launches, milestones

**Data Fetching:** Single GraphQL POST to GitHub's GraphQL API via `resources.GetRemote`:

```graphql
{
  repository(owner: "gopherine", name: "hugo-blog") {
    discussions(categoryId: "<THOUGHTS_CATEGORY_ID>", first: 20, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        title
        body
        createdAt
        url
        labels(first: 5) { nodes { name color } }
        reactionGroups { content users { totalCount } }
        comments { totalCount }
      }
    }
  }
}
```

Two calls needed — one per category (Thoughts, Shipped) — then merge results. Rate limit: 5000 points/hr for authenticated requests (each query costs ~1 point). The `GITHUB_TOKEN` env var is used for auth.

**Giscus coexistence:** Giscus uses the "Announcements" category for blog post comments. Microblog uses separate "Thoughts" and "Shipped" categories. No conflict.

**Merge Strategy:** Build a unified feed by appending normalized items to a Hugo scratch list:

1. Fetch GitHub Discussions (micro-posts + shipped) via `resources.GetRemote` (GraphQL POST) → unmarshal → iterate and append to `$.Scratch` list with normalized fields: `{type, title, body, date, tags, reactions, url}`
2. Iterate Hugo pages (`where .Site.RegularPages "Section" "in" (slice "post")`) → append to same scratch list with: `{type: "article", title: .Title, body: .Summary, date: .PublishDate, tags: .Params.tags, url: .Permalink, readTime: .ReadingTime}`
3. Sort the unified list by `date` descending
4. Range over the sorted list and render each item using the appropriate card template based on `type`

**Feed Item Layout:**
- **Date** (top-left)
- **Label pill** (top-right): `thought` (purple #6f42c1), `shipped` (green #28a745), `article` (blue #0366d6)
- **Title** (bold, if present)
- **Body** (markdownified for micro-posts, `.Summary` for articles)
- **Tags** (small pills below body)
- **Reactions** (for micro-posts only: heart, rocket counts)
- **Read time** (for articles only: "📖 N min read")

**Pinned Post:** One item at the top with purple border and "📌 PINNED" label. Determined by GitHub issue label `pinned` (add this label to the desired issue). The template checks for the `pinned` label when iterating issues and renders that item first, outside the main feed loop. If no issue has the `pinned` label, no pinned section renders.

**Fallback:** If fewer than 3 micro-posts exist after the API call, the feed still looks full because articles are always mixed in. If the API call fails entirely (no `GITHUB_TOKEN`, network error), the feed renders articles only — no crash, no empty state.

**At the bottom of the feed:**
- **Full contribution graph** — the existing `#contributions` div with its `data` attribute (JSON of all posts) and the SVG calendar rendered by `github-style.js`. Moved from the current overview block into the feed tab partial. The JS that reads `#contributions` data continues to work unchanged.
- **Post activity timeline** — existing monthly grouping, kept below the graph.

#### Articles Tab

Content: **All** posts from `/content/post/` rendered as a single list (no pagination — enables client-side filtering).

Top of tab: **Tag filter pills** — generated from all unique tags across posts. Clickable pills: click one to show only matching articles, click again to deselect. "All" pill selected by default. Pure JavaScript `display:none/block` filtering on pre-rendered article cards.

Each article card shows:
- Title (link to full article)
- Date
- Summary (first ~150 chars)
- Tags (as small pills)
- Read time

### Removed Components

- **README box** (`content/readme.md` rendered on homepage) — REMOVED from homepage. File stays for GitHub repo display.
- **Pinned posts grid** (current 6-item grid) — REPLACED by single pinned post in feed + Articles tab.
- **`{{ define "overview" }}` block pattern** — REPLACED with direct partial call.

### Mobile Layout

- **Sidebar** collapses to a compact header: avatar (small) + name + title in one horizontal row. Bio hidden. Socials become a row of small icons.
- **Tabs** become horizontally scrollable pills with `overflow-x: auto; white-space: nowrap`.
- **Feed** goes full-width below the compact header.
- **Right sidebar** (file tree) hides on mobile — already handled by current theme CSS.

### Dark/Light Mode

All new components use existing CSS variables (`--color-*`) from the theme's `light.css` and `dark.css`. No hardcoded colors. The label pills use fixed colors (purple, green, blue) that work in both modes.

## Data Flow

### Micro-posts + Shipped Posts (GitHub Discussions → Feed)

1. Author creates a Discussion in `gopherine/hugo-blog` under "Thoughts" or "Shipped" category
2. GitHub Actions triggers a rebuild + LinkedIn syndication (via `discussion` event in workflow)
3. Hugo's `resources.GetRemote` (POST) calls GitHub GraphQL API at build time
4. `feed-tab.html` partial merges discussions with article previews into a unified sorted feed
5. Only repo owner's discussions appear (GraphQL scoped to the repo, author is implicit)

### Articles (Hugo Content → Feed + Articles Tab)

1. Articles exist as markdown in `/content/post/`
2. Hugo renders them as:
   - Individual pages at `/post/{slug}/` (unchanged)
   - Feed items in the homepage feed (merged with micro-posts by date)
   - List items in the Articles tab (with tag filtering)

### LinkedIn Syndication (GitHub Action)

When a Discussion is created, a GitHub Action auto-posts to LinkedIn.

**Flow:**
1. `discussion` event fires (types: `created`)
2. GitHub Action reads discussion title + body
3. Truncates body to LinkedIn's character limit (~3000 chars for posts)
4. Appends a "Read more → atharvapandey.com" link back to the site
5. Posts via LinkedIn's Posts API (free for personal profiles)
6. OAuth token stored as repo secret: `LINKEDIN_ACCESS_TOKEN`

**LinkedIn API setup (one-time):**
1. Create a LinkedIn App at linkedin.com/developers
2. Request `w_member_social` scope (for posting on your behalf)
3. Generate an access token (valid 60 days, refresh with `r_liteprofile` scope)
4. Store as GitHub repo secret

**LinkedIn Post API endpoint:**
```
POST https://api.linkedin.com/v2/ugcPosts
```

**GitHub Actions workflow addition** (`.github/workflows/hugo_build.yml`):
- Add `discussion` event trigger: `types: [created]`
- Add a new job `syndicate-linkedin` that runs only on `discussion` events
- Conditional: only runs if discussion category is "Thoughts" or "Shipped"

**Token refresh:** LinkedIn access tokens expire after 60 days. A separate scheduled GitHub Action (cron) can refresh the token using the refresh token, or the user manually regenerates quarterly. Document this in a `LINKEDIN_SETUP.md`.

### Future: Additional Syndication

Bluesky (free AT Protocol API) and other platforms can be added later using the same pattern — GitHub Action reads discussion content, posts to external API. Twitter/X requires a $100/mo API plan, so it remains manual copy-paste for high-signal posts only.

## Files to Create/Modify

| File | Action |
|------|--------|
| `themes/github-style/layouts/partials/user-profile.html` | Modify — line 181: replace `{{ block "overview" . }}{{ end }}` with `{{ partial "homepage-content.html" . }}`. Update bio text, add resume link below socials, add mini activity streak (Hugo-rendered), remove "Organizations" heading. |
| `themes/github-style/layouts/partials/homepage-content.html` | New — replaces `overview.html`. Contains tab bar + includes `feed-tab.html` and `articles-tab.html` partials. No `{{ define }}` block. |
| `themes/github-style/layouts/partials/feed-tab.html` | New — fetches GitHub Issues via Search API, merges with article previews into sorted feed. Renders pinned post, feed items, contribution graph, post activity timeline. Contains the `#contributions` div with data attribute. |
| `themes/github-style/layouts/partials/articles-tab.html` | New — renders all posts (no pagination) with tag filter pills. Each card: title, date, summary, tags, read time. |
| `themes/github-style/layouts/partials/microblog.html` | Delete or archive — functionality absorbed into `feed-tab.html`. |
| `.github/workflows/hugo_build.yml` | Modify — add `discussion` event trigger, add `syndicate-linkedin` job |
| `.github/workflows/linkedin-sync.md` | New — setup guide for LinkedIn API OAuth tokens |
| `themes/github-style/layouts/partials/overview.html` | Delete or archive — replaced by `homepage-content.html`. |
| `themes/github-style/layouts/partials/home.html` | Modify — remove `{{ partial "overview.html" . }}`. File becomes a single-line wrapper (`{{ partial "user-profile.html" . }}`). |
| `themes/github-style/static/js/github-style.js` | Modify — add tab switching logic (show/hide divs by data attribute), tag filtering (toggle `display` on article cards), URL hash read/write. Existing contribution graph JS stays. |
| `themes/github-style/static/css/github-style.css` | Modify — tab bar styles, active tab indicator, feed item card styles, label pill colors, pinned post border, mini activity streak grid, tag filter pills, mobile compact header, mobile scrollable tabs. |
| `hugo.toml` | Modify — update `[params]` description to new bio, remove duplicate `[params.microblog]` from theme config, keep it only in root config. Add `pinnedLabel = "pinned"` to `[params.microblog]`. |
| `static/resume.pdf` | New — add resume PDF file. |
| `content/readme.md` | Keep file, but remove rendering from homepage (the `{{ with .Site.GetPage "/readme" }}` block is removed). |

## Verification

1. `hugo server -D` — homepage loads with sidebar brand card + two tabs
2. Feed tab shows micro-posts (from GitHub Issues) + article previews, sorted by date descending
3. Pinned post appears at top with purple border (when an issue has `pinned` label)
4. Articles tab shows all posts with working tag filter pills
5. Tab switching works without page reload, URL hash updates
6. Direct linking: `/#articles` loads with Articles tab active
7. Contribution graph renders at bottom of feed tab
8. Mobile: sidebar collapses to compact header, tabs scroll horizontally
9. Dark/light mode: all components respect theme CSS variables
10. Resume link in sidebar downloads `/resume.pdf`
11. With `GITHUB_TOKEN` unset: feed shows articles only, no crash, no empty state
12. Mini activity streak in sidebar shows correct weekly counts
13. Create a GitHub Discussion in "Thoughts" category → rebuild → appears in feed
14. LinkedIn syndication: Discussion creation triggers the `syndicate-linkedin` job in GitHub Actions
15. LinkedIn post contains truncated body + "Read more" link back to site
