# Portfolio Brand Redesign — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform atharvapandey.com from an outdated GitHub-style blog into a brand-first portfolio with a microblog feed (GitHub Discussions), articles tab, and LinkedIn auto-syndication.

**Architecture:** GitHub-style left sidebar (brand card) + right content area (two tabs: Feed, Articles). Feed merges GitHub Discussions micro-posts with Hugo article previews into one chronological stream. LinkedIn syndication via GitHub Action on discussion creation.

**Tech Stack:** Hugo 0.158, GitHub GraphQL API, GitHub Actions, LinkedIn Posts API, vanilla JS (tabs/filtering), Primer CSS classes

**Spec:** `docs/superpowers/specs/2026-03-21-portfolio-brand-redesign.md`

---

## Chunk 1: Rendering Chain + Config Updates

### Task 1: Update site config and identity

**Files:**
- Modify: `hugo.toml`
- Modify: `themes/github-style/hugo.toml`

- [ ] **Step 1: Update root hugo.toml — new bio, consolidated microblog params**

In `hugo.toml`, replace the `[params.microblog]` section and add new params:

```toml
[params]
description = "Polyglot Engineer · Technical Leader. I ship AI-native tools, distributed systems, and developer platforms in Go, Rust, and TypeScript."

[params.microblog]
enabled = true
repo = "gopherine/hugo-blog"
author = "Gopherine"
maxPosts = 20
pinnedLabel = "pinned"
thoughtsCategoryId = ""
shippedCategoryId = ""
```

The `thoughtsCategoryId` and `shippedCategoryId` will be filled after creating Discussion categories in Task 2.

- [ ] **Step 2: Update theme hugo.toml — new bio description**

In `themes/github-style/hugo.toml`, update the `description` param to match:

```toml
description = "Polyglot Engineer · Technical Leader. I ship AI-native tools, distributed systems, and developer platforms in Go, Rust, and TypeScript."
```

Remove the duplicate `[params.microblog]` section from this file (keep it only in root config).

Also update `[author]` section:

```toml
[author]
name = "Atharva Pandey"
bio = "Polyglot Engineer · Technical Leader. I ship AI-native tools, distributed systems, and developer platforms in Go, Rust, and TypeScript."
```

- [ ] **Step 3: Verify config loads correctly**

Run: `hugo config | grep -A5 "description"`
Expected: Shows the new bio text.

- [ ] **Step 4: Commit**

```bash
git add hugo.toml themes/github-style/hugo.toml
git commit -m "chore: update site identity and consolidate microblog config"
```

### Task 2: Create GitHub Discussion categories

- [ ] **Step 1: Create "Thoughts" and "Shipped" discussion categories**

Run:
```bash
gh api graphql -f query='mutation { createDiscussionCategory(input: {repositoryId: "R_kgDOLTrS1w", name: "Thoughts", description: "Opinions, hot takes, micro-essays", format: DISCUSSION, emoji: "💭"}) { category { id name } } }'
gh api graphql -f query='mutation { createDiscussionCategory(input: {repositoryId: "R_kgDOLTrS1w", name: "Shipped", description: "Project releases, tool launches, milestones", format: DISCUSSION, emoji: "🚀"}) { category { id name } } }'
```

Record the returned category IDs.

- [ ] **Step 2: Update hugo.toml with category IDs**

Fill in `thoughtsCategoryId` and `shippedCategoryId` with the values from step 1.

- [ ] **Step 3: Commit**

```bash
git add hugo.toml
git commit -m "chore: add GitHub Discussion category IDs to config"
```

### Task 3: Rewire the rendering chain

**Important: There are THREE `home.html` files in this project:**
- `themes/github-style/layouts/index.html` — layout template with `{{ define "content" }}`
- `themes/github-style/layouts/home.html` — duplicate layout (same content as index.html)
- `themes/github-style/layouts/_default/home.html` — another duplicate layout
- `themes/github-style/layouts/partials/home.html` — the partial that renders sidebar + overview

The layout templates (`index.html`, `home.html`, `_default/home.html`) define the `"content"` block that `baseof.html` renders. They call `{{ partial "home.html" . }}`. **Do NOT modify these layout files.** Only modify the partial.

**Files:**
- Modify: `themes/github-style/layouts/partials/home.html` (the PARTIAL, not the layouts)
- Modify: `themes/github-style/layouts/partials/user-profile.html` (line 181)
- Create: `themes/github-style/layouts/partials/homepage-content.html`

- [ ] **Step 1: Create homepage-content.html — empty shell**

Create `themes/github-style/layouts/partials/homepage-content.html`:

```html
{{/* Homepage content area — replaces the old overview block */}}
<div class="homepage-tabs">
  <p>Homepage content placeholder — tabs go here</p>
</div>
```

- [ ] **Step 2: Update user-profile.html — replace block with partial call**

In `themes/github-style/layouts/partials/user-profile.html`, replace line 181:

```html
        {{ block "overview" . }}{{ end }}
```

With:

```html
        {{ partial "homepage-content.html" . }}
```

- [ ] **Step 3: Update the PARTIAL home.html — remove overview.html registration**

In `themes/github-style/layouts/partials/home.html` (the PARTIAL at `partials/home.html`), replace with:

```html
<div class="application-main">
  <main>
    {{ partial "user-profile.html" . }}
  </main>
</div>
```

This removes the `{{ partial "overview.html" . }}` line that previously registered the `{{ define "overview" }}` block. The `homepage-content.html` partial (called directly from `user-profile.html`) replaces it.

**Do NOT touch** `themes/github-style/layouts/index.html`, `themes/github-style/layouts/home.html`, or `themes/github-style/layouts/_default/home.html` — these are layout templates that must keep their `{{ define "content" }}` wrapper.

- [ ] **Step 4: Verify the rendering chain works**

Run: `hugo server -D`
Expected: Homepage loads with sidebar profile + "Homepage content placeholder" text in the content area. No errors, no warnings about missing blocks.

- [ ] **Step 5: Commit**

```bash
git add themes/github-style/layouts/partials/home.html themes/github-style/layouts/partials/user-profile.html themes/github-style/layouts/partials/homepage-content.html
git commit -m "refactor: replace block/define rendering chain with direct partial call"
```

### Task 4: Update sidebar — brand card

**Files:**
- Modify: `themes/github-style/layouts/partials/user-profile.html`
- Modify: `themes/github-style/static/css/github-style.css`

- [ ] **Step 1: Update bio section**

In `user-profile.html`, find the bio div (around line 61-63):

```html
    <div class="mb-3 p-note user-profile-bio f4">
      <div>{{ .Site.Params.description }}</div>
    </div>
```

This already renders `description` from config, so updating the config (Task 1) handles the bio text. No template change needed here.

- [ ] **Step 2: Remove "Organizations" heading, clean up socials section**

In `user-profile.html`, find line ~107:

```html
        <div class="clearfix pt-3 mt-3 border-top color-border-secondary hide-sm hide-md">
          <h2 class="mb-2 h4">Organizations</h2>
```

Replace `<h2 class="mb-2 h4">Organizations</h2>` with nothing — just remove the heading. Keep the containing div and all social icon links.

- [ ] **Step 3: Add resume link below socials**

After the closing `</div>` of the social icons container (after the RSS link around line 173), add:

```html
        <div class="pt-3 mt-3 border-top color-border-secondary hide-sm hide-md">
          <a href="/resume.pdf" class="link-gray-dark text-small">
            <svg class="octicon mr-1" viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
              <path d="M3.75 1.5a.25.25 0 0 0-.25.25v11.5c0 .138.112.25.25.25h8.5a.25.25 0 0 0 .25-.25V6H9.75A1.75 1.75 0 0 1 8 4.25V1.5Zm5.75 0v2.75c0 .138.112.25.25.25h2.75L9.5 1.5ZM2 1.75C2 .784 2.784 0 3.75 0h5.086c.464 0 .909.184 1.237.513l3.414 3.414c.329.328.513.773.513 1.237v8.086A1.75 1.75 0 0 1 12.25 15h-8.5A1.75 1.75 0 0 1 2 13.25Z"/>
            </svg>
            Resume
          </a>
        </div>
```

- [ ] **Step 4: Add mini activity streak**

After the resume link div, add:

```html
        <div class="pt-3 mt-3 border-top color-border-secondary hide-sm hide-md">
          <div class="text-small text-bold mb-2">Activity</div>
          <div class="activity-streak">
            {{ $now := now }}
            {{ $pages := where .Site.RegularPages "Section" "in" (slice "post") }}
            {{ $totalThisYear := 0 }}
            {{ range $pages }}
              {{ if eq (.PublishDate.Format "2006") ($now.Format "2006") }}
                {{ $totalThisYear = add $totalThisYear 1 }}
              {{ end }}
            {{ end }}
            {{ range $week := slice 11 10 9 8 7 6 5 4 3 2 1 0 }}
              {{ $count := 0 }}
              {{ range $pages }}
                {{ $daysDiff := div (sub $now.Unix .PublishDate.Unix) 86400 }}
                {{ if and (ge $daysDiff (mul $week 7)) (lt $daysDiff (mul (add $week 1) 7)) }}
                  {{ $count = add $count 1 }}
                {{ end }}
              {{ end }}
              {{ $level := cond (eq $count 0) "0" (cond (le $count 1) "1" (cond (le $count 2) "2" "3")) }}
              <div class="streak-cell streak-level-{{ $level }}"></div>
            {{ end }}
          </div>
          <div class="text-small text-gray mt-1">{{ $totalThisYear }} posts this year</div>
        </div>
```

- [ ] **Step 5: Add CSS for activity streak and resume link**

Append to `themes/github-style/static/css/github-style.css`:

```css
/* Activity streak mini heatmap */
.activity-streak {
  display: grid;
  grid-template-columns: repeat(12, 1fr);
  gap: 2px;
}

.streak-cell {
  aspect-ratio: 1;
  border-radius: 2px;
}

.streak-level-0 { background-color: var(--color-calendar-graph-day-bg, #ebedf0); }
.streak-level-1 { background-color: var(--color-calendar-graph-day-L1-bg, #9be9a8); }
.streak-level-2 { background-color: var(--color-calendar-graph-day-L2-bg, #40c463); }
.streak-level-3 { background-color: var(--color-calendar-graph-day-L3-bg, #30a14e); }
```

- [ ] **Step 6: Verify sidebar renders correctly**

Run: `hugo server -D`
Expected: Sidebar shows updated bio, no "Organizations" heading, resume link visible, mini activity streak grid visible with "N posts this year" text.

- [ ] **Step 7: Commit**

```bash
git add themes/github-style/layouts/partials/user-profile.html themes/github-style/static/css/github-style.css
git commit -m "feat: upgrade sidebar to brand card — bio, resume link, activity streak"
```

---

## Chunk 2: Tab System + Feed Tab

### Task 5: Build the tab system

**Files:**
- Modify: `themes/github-style/layouts/partials/homepage-content.html`
- Modify: `themes/github-style/static/js/github-style.js`
- Modify: `themes/github-style/static/css/github-style.css`

- [ ] **Step 1: Build tab bar + content containers in homepage-content.html**

Replace `themes/github-style/layouts/partials/homepage-content.html` with:

```html
{{/* Homepage content area — two-tab system */}}
<div class="homepage-tabs">
  <div class="UnderlineNav">
    <div class="UnderlineNav-body">
      <a class="UnderlineNav-item" data-tab="feed" onclick="switchTab('feed')" aria-current="page">
        <svg class="octicon mr-1" viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
          <path d="M1.75 1h8.5c.966 0 1.75.784 1.75 1.75v5.5A1.75 1.75 0 0 1 10.25 10H7.061l-2.574 2.573A1.458 1.458 0 0 1 2 11.543V10h-.25A1.75 1.75 0 0 1 0 8.25v-5.5C0 1.784.784 1 1.75 1Z"/>
        </svg>
        Feed
      </a>
      <a class="UnderlineNav-item" data-tab="articles" onclick="switchTab('articles')">
        <svg class="octicon mr-1" viewBox="0 0 16 16" width="16" height="16" fill="currentColor">
          <path d="M0 1.75A.75.75 0 0 1 .75 1h4.253c1.227 0 2.317.59 3 1.501A3.744 3.744 0 0 1 11.006 1h4.245a.75.75 0 0 1 .75.75v10.5a.75.75 0 0 1-.75.75h-4.507a2.25 2.25 0 0 0-1.591.659l-.622.621a.75.75 0 0 1-1.06 0l-.623-.62A2.25 2.25 0 0 0 5.258 13H.75a.75.75 0 0 1-.75-.75Zm7.251 10.324.004-5.073-.002-2.253A2.25 2.25 0 0 0 5.003 2.5H1.5v9h3.757a3.75 3.75 0 0 1 1.994.574ZM8.755 4.75l-.004 7.322a3.752 3.752 0 0 1 1.992-.572H14.5v-9h-3.495a2.25 2.25 0 0 0-2.25 2.25Z"/>
        </svg>
        Articles
      </a>
    </div>
  </div>

  <div class="tab-content" data-tab-content="feed">
    {{ partial "feed-tab.html" . }}
  </div>

  <div class="tab-content" data-tab-content="articles" style="display:none;">
    {{ partial "articles-tab.html" . }}
  </div>
</div>
```

- [ ] **Step 2: Create empty feed-tab.html and articles-tab.html placeholders**

Create `themes/github-style/layouts/partials/feed-tab.html`:
```html
<div class="mt-3">
  <p>Feed loading...</p>
</div>
```

Create `themes/github-style/layouts/partials/articles-tab.html`:
```html
<div class="mt-3">
  <p>Articles loading...</p>
</div>
```

- [ ] **Step 3: Add tab switching JS**

Append to `themes/github-style/static/js/github-style.js`:

```javascript
// Tab switching
function switchTab(tabName) {
  document.querySelectorAll('.tab-content').forEach(function(el) {
    el.style.display = 'none';
  });
  document.querySelectorAll('.UnderlineNav-item').forEach(function(el) {
    el.removeAttribute('aria-current');
  });
  var target = document.querySelector('[data-tab-content="' + tabName + '"]');
  if (target) target.style.display = 'block';
  var tab = document.querySelector('[data-tab="' + tabName + '"]');
  if (tab) tab.setAttribute('aria-current', 'page');
  history.replaceState(null, '', '#' + tabName);
}

// Read hash on load
document.addEventListener('DOMContentLoaded', function() {
  var hash = window.location.hash.replace('#', '');
  if (hash === 'articles') {
    switchTab('articles');
  }
});
```

- [ ] **Step 4: Add tab CSS**

Append to `themes/github-style/static/css/github-style.css`:

```css
/* Tab system */
.homepage-tabs .UnderlineNav {
  border-bottom: 1px solid var(--color-border-primary);
  margin-bottom: 16px;
}

.homepage-tabs .UnderlineNav-body {
  display: flex;
  gap: 0;
}

.homepage-tabs .UnderlineNav-item {
  padding: 8px 16px;
  font-size: 14px;
  color: var(--color-text-secondary);
  cursor: pointer;
  border-bottom: 2px solid transparent;
  display: flex;
  align-items: center;
  text-decoration: none;
}

.homepage-tabs .UnderlineNav-item[aria-current="page"] {
  font-weight: 600;
  color: var(--color-text-primary);
  border-bottom-color: #6f42c1;
}

.homepage-tabs .UnderlineNav-item:hover {
  color: var(--color-text-primary);
}

/* Mobile scrollable tabs + compact header */
@media (max-width: 768px) {
  .homepage-tabs .UnderlineNav-body {
    overflow-x: auto;
    white-space: nowrap;
    -webkit-overflow-scrolling: touch;
  }

  /* Compact sidebar on mobile — avatar + name + title in one row */
  .h-card .clearfix.d-flex {
    flex-direction: row;
    align-items: center;
  }

  .h-card .avatar-user {
    width: 48px !important;
    height: 48px !important;
  }

  .h-card .p-note.user-profile-bio {
    display: none;
  }

  .h-card .activity-streak {
    display: none;
  }

  .h-card .vcard-details {
    display: none;
  }
}
```

- [ ] **Step 5: Verify tabs work**

Run: `hugo server -D`
Expected: Homepage shows two tabs (Feed, Articles). Clicking switches content. URL hash updates. `/#articles` direct link works.

- [ ] **Step 6: Commit**

```bash
git add themes/github-style/layouts/partials/homepage-content.html themes/github-style/layouts/partials/feed-tab.html themes/github-style/layouts/partials/articles-tab.html themes/github-style/static/js/github-style.js themes/github-style/static/css/github-style.css
git commit -m "feat: add two-tab system (Feed + Articles) to homepage"
```

### Task 6: Build the feed tab — GitHub Discussions + article merge

**Files:**
- Modify: `themes/github-style/layouts/partials/feed-tab.html`
- Modify: `themes/github-style/static/css/github-style.css`

- [ ] **Step 1: Implement feed-tab.html — full template**

Replace `themes/github-style/layouts/partials/feed-tab.html` with:

```html
{{/* Feed tab — merges GitHub Discussions + Hugo articles into one stream */}}
<div class="mt-3">

  {{/* ---- Initialize feed list ---- */}}
  {{ $.Scratch.Set "feed" slice }}

  {{/* ---- Fetch GitHub Discussions (Thoughts + Shipped) ---- */}}
  {{ if .Site.Params.microblog.enabled }}
    {{ $token := getenv "GITHUB_TOKEN" }}
    {{ if $token }}
      {{ $headers := dict "Authorization" (printf "Bearer %s" $token) "Content-Type" "application/json" }}

      {{/* Fetch each category */}}
      {{ $categories := dict "thought" .Site.Params.microblog.thoughtsCategoryId "shipped" .Site.Params.microblog.shippedCategoryId }}
      {{ range $type, $catId := $categories }}
        {{ if $catId }}
          {{ $query := printf `{"query": "{ repository(owner: \"gopherine\", name: \"hugo-blog\") { discussions(categoryId: \"%s\", first: %d, orderBy: {field: CREATED_AT, direction: DESC}) { nodes { title body createdAt url labels(first: 5) { nodes { name color } } reactionGroups { content users { totalCount } } comments { totalCount } } } } }"}` $catId $.Site.Params.microblog.maxPosts }}
          {{ $opts := dict "method" "POST" "headers" $headers "body" $query }}
          {{ with resources.GetRemote "https://api.github.com/graphql" $opts }}
            {{ $data := .Content | transform.Unmarshal }}
            {{ with $data.data.repository.discussions.nodes }}
              {{ range . }}
                {{/* Check for pinned label */}}
                {{ $isPinned := false }}
                {{ $labels := slice }}
                {{ with .labels.nodes }}
                  {{ range . }}
                    {{ if eq .name $.Site.Params.microblog.pinnedLabel }}
                      {{ $isPinned = true }}
                    {{ else }}
                      {{ $labels = append $labels .name }}
                    {{ end }}
                  {{ end }}
                {{ end }}

                {{/* Sum reactions */}}
                {{ $reactions := 0 }}
                {{ with .reactionGroups }}
                  {{ range . }}
                    {{ $reactions = add $reactions .users.totalCount }}
                  {{ end }}
                {{ end }}

                {{ $.Scratch.Add "feed" (dict
                  "type" $type
                  "title" .title
                  "body" (.body | markdownify)
                  "date" .createdAt
                  "tags" $labels
                  "reactions" $reactions
                  "comments" .comments.totalCount
                  "url" .url
                  "pinned" $isPinned
                  "readTime" 0
                ) }}
              {{ end }}
            {{ end }}
          {{ end }}
        {{ end }}
      {{ end }}
    {{ end }}
  {{ end }}

  {{/* ---- Add Hugo articles to feed ---- */}}
  {{ $posts := where .Site.RegularPages "Section" "in" (slice "post") }}
  {{ range $posts }}
    {{ $.Scratch.Add "feed" (dict
      "type" "article"
      "title" .Title
      "body" (.Summary | safeHTML)
      "date" (.PublishDate.Format "2006-01-02T15:04:05Z")
      "tags" .Params.tags
      "reactions" 0
      "comments" 0
      "url" .Permalink
      "pinned" false
      "readTime" .ReadingTime
    ) }}
  {{ end }}

  {{/* ---- Sort feed by date descending ---- */}}
  {{ $feed := sort ($.Scratch.Get "feed") "date" "desc" }}

  {{/* ---- Render pinned post (if any) ---- */}}
  {{ range $feed }}
    {{ if .pinned }}
    <div class="feed-item feed-item--pinned Box mb-3">
      <div class="Box-body p-3">
        <div class="d-flex flex-justify-between flex-items-center mb-1">
          <span class="pinned-badge">📌 PINNED</span>
          <span class="feed-label feed-label--{{ .type }}">{{ .type }}</span>
        </div>
        {{ with .title }}<div class="mb-1"><strong class="f5">{{ . }}</strong></div>{{ end }}
        <div class="markdown-body f6">{{ .body }}</div>
        {{ with .tags }}
        <div class="mt-2">
          {{ range . }}<span class="feed-tag">{{ . }}</span>{{ end }}
        </div>
        {{ end }}
      </div>
    </div>
    {{ end }}
  {{ end }}

  {{/* ---- Render feed items ---- */}}
  {{ range $feed }}
    {{ if not .pinned }}
    <div class="feed-item Box mb-3">
      <div class="Box-body p-3">
        <div class="d-flex flex-justify-between flex-items-center mb-1">
          <span class="text-small text-gray">{{ dateFormat "Jan 2, 2006" .date }}</span>
          <span class="feed-label feed-label--{{ .type }}">{{ .type }}</span>
        </div>
        {{ with .title }}
        <div class="mb-1">
          {{ if eq (index $ "type") "article" }}
            <a href="{{ $.url }}" class="text-bold f5 link-gray-dark">{{ . }}</a>
          {{ else }}
            <strong class="f5">{{ . }}</strong>
          {{ end }}
        </div>
        {{ end }}
        <div class="markdown-body f6">{{ .body }}</div>
        {{ with .tags }}
        <div class="mt-2">
          {{ range . }}<span class="feed-tag">{{ . }}</span>{{ end }}
        </div>
        {{ end }}
        <div class="mt-2 d-flex flex-items-center text-small text-gray">
          {{ if gt .readTime 0 }}<span class="mr-3">📖 {{ .readTime }} min read</span>{{ end }}
          {{ if gt .reactions 0 }}<span class="mr-3">❤️ {{ .reactions }}</span>{{ end }}
          {{ if gt .comments 0 }}<span>💬 {{ .comments }}</span>{{ end }}
        </div>
      </div>
    </div>
    {{ end }}
  {{ end }}

  {{/* ---- Contribution graph (moved from overview.html) ---- */}}
  {{ $section := where .Site.RegularPages "Section" "in" (slice "post") }}
  <div class="mt-4 position-relative" id="contributions" data='[{{ range $index, $elem := $section }}
  {
    "title": "{{ .Title }}",
    "link": "{{ .Permalink }}",
    "publishDate": "{{ .PublishDate.Format "2006-01-02 15:04:05" }}"
  }{{ if ne $index (sub (len $section) 1) }},{{ end }}
  {{ end }}]'>
    <div class="js-yearly-contributions">
      <div class="position-relative">
        <h2 class="mb-2 f4 text-normal" id="posts-count"></h2>
        <div class="py-2 border graph-before-activity-overview">
          <div class="pt-1 mx-3 overflow-hidden text-center js-calendar-graph mx-md-2 d-flex flex-column flex-items-end flex-xl-items-center is-graph-loading graph-canvas calendar-graph height-full">
            <svg width="828" height="128" class="js-calendar-graph-svg">
              <g transform="translate(10, 20)" id="graph-svg"></g>
            </svg>
          </div>
          <div class="clearfix px-3 pb-1 mx-3 mt-1 contrib-footer">
            <div class="float-left text-gray"></div>
            <div class="contrib-legend text-gray">
              Less
              <ul class="legend">
                <li style="background-color: var(--color-calendar-graph-day-bg)"></li>
                <li style="background-color: var(--color-calendar-graph-day-L1-bg)"></li>
                <li style="background-color: var(--color-calendar-graph-day-L2-bg)"></li>
                <li style="background-color: var(--color-calendar-graph-day-L3-bg)"></li>
                <li style="background-color: var(--color-calendar-graph-day-L4-bg)"></li>
              </ul>
              More
            </div>
          </div>
        </div>
      </div>
    </div>

    {{/* Post activity timeline */}}
    <div class="activity-listing contribution-activity">
      <div class="d-none d-lg-block">
        <div class="float-right pl-5 bg-white js-profile-timeline-year-list col-2 is-placeholder"
          style="visibility: hidden; display: none; height: 210px;"></div>
        <div style="top: 74px; position: static;"
          class="float-right pl-5 bg-white js-profile-timeline-year-list js-sticky col-2">
          <ul class="filter-list small" id="year-list"></ul>
        </div>
      </div>
      <h2 class="mt-4 mb-3 f4 text-normal">Post activity</h2>
      <div id="posts-activity"></div>
    </div>
  </div>

  <div id="pinned-items-modal-wrapper"></div>
  <div id="svg-tip" class="svg-tip svg-tip-one-line" style="pointer-events: none; display: none;"></div>

</div>
```

Note: The contribution graph HTML and `#contributions` data attribute are copied directly from the old `overview.html` (lines 88-154). The existing `github-style.js` reads this data and renders the SVG calendar — no changes needed to that JS.

- [ ] **Step 2: Add feed item CSS**

Append to `github-style.css`:

```css
/* Feed items */
.feed-item { border-radius: 6px; }

.feed-label {
  display: inline-block;
  padding: 1px 8px;
  font-size: 12px;
  font-weight: 500;
  line-height: 18px;
  border-radius: 2em;
  color: #fff;
}

.feed-label--thought { background-color: #6f42c1; }
.feed-label--shipped { background-color: #28a745; }
.feed-label--article { background-color: #0366d6; }

.feed-tag {
  display: inline-block;
  padding: 2px 8px;
  font-size: 11px;
  border-radius: 2em;
  background: var(--color-bg-secondary);
  border: 1px solid var(--color-border-primary);
  margin-right: 4px;
}

/* Pinned post */
.feed-item--pinned {
  border-color: #6f42c1;
  border-width: 2px;
}

.feed-item--pinned .pinned-badge {
  font-size: 11px;
  color: #6f42c1;
  font-weight: 600;
}
```

- [ ] **Step 3: Verify feed renders**

Run: `GITHUB_TOKEN=$(gh auth token) hugo server -D`
Expected: Feed tab shows article previews (always), plus any GitHub Discussions if categories exist. Contribution graph renders at the bottom.

- [ ] **Step 4: Test fallback — no GITHUB_TOKEN**

Run: `hugo server -D` (no token)
Expected: Feed shows articles only. No errors. No empty state.

- [ ] **Step 5: Commit**

```bash
git add themes/github-style/layouts/partials/feed-tab.html themes/github-style/static/css/github-style.css
git commit -m "feat: build feed tab — GitHub Discussions + article merge + contribution graph"
```

---

## Chunk 3: Articles Tab + Cleanup

### Task 7: Build the articles tab with tag filtering

**Files:**
- Modify: `themes/github-style/layouts/partials/articles-tab.html`
- Modify: `themes/github-style/static/js/github-style.js`

- [ ] **Step 1: Implement articles-tab.html**

Replace `themes/github-style/layouts/partials/articles-tab.html`:

```html
{{/* Articles tab — all posts with tag filtering */}}
<div class="mt-3">
  {{/* Collect all unique tags */}}
  {{ $.Scratch.Set "allTags" slice }}
  {{ $posts := where .Site.RegularPages "Section" "in" (slice "post") }}
  {{ range $posts }}
    {{ with .Params.tags }}
      {{ range . }}
        {{ $.Scratch.Add "allTags" . }}
      {{ end }}
    {{ end }}
  {{ end }}
  {{ $allTags := uniq ($.Scratch.Get "allTags") }}

  {{/* Tag filter pills */}}
  <div class="tag-filters mb-3">
    <button class="tag-pill tag-pill--active" onclick="filterArticles('all', this)">All</button>
    {{ range $allTags }}
    <button class="tag-pill" onclick="filterArticles('{{ . }}', this)">{{ . }}</button>
    {{ end }}
  </div>

  {{/* Article cards */}}
  {{ range $posts.ByPublishDate.Reverse }}
  <div class="article-card Box mb-3" data-tags="{{ delimit .Params.tags "," }}">
    <div class="Box-body p-3">
      <div class="d-flex flex-justify-between flex-items-center mb-1">
        <span class="text-small text-gray">{{ dateFormat "Jan 2, 2006" .PublishDate }}</span>
        <span class="text-small text-gray">📖 {{ .ReadingTime }} min read</span>
      </div>
      <a href="{{ .Permalink }}" class="text-bold f5 link-gray-dark">{{ .Title }}</a>
      <div class="mt-1 text-small text-gray">{{ .Summary | truncate 150 }}</div>
      {{ with .Params.tags }}
      <div class="mt-2">
        {{ range . }}<span class="feed-tag">{{ . }}</span>{{ end }}
      </div>
      {{ end }}
    </div>
  </div>
  {{ end }}
</div>
```

- [ ] **Step 2: Add tag filtering JS**

Append to `github-style.js`:

```javascript
// Article tag filtering
function filterArticles(tag, btn) {
  document.querySelectorAll('.tag-pill').forEach(function(el) {
    el.classList.remove('tag-pill--active');
  });
  btn.classList.add('tag-pill--active');

  document.querySelectorAll('.article-card').forEach(function(card) {
    if (tag === 'all') {
      card.style.display = 'block';
    } else {
      var tags = (card.getAttribute('data-tags') || '').split(',');
      card.style.display = tags.indexOf(tag) !== -1 ? 'block' : 'none';
    }
  });
}
```

- [ ] **Step 3: Add tag pill CSS**

Append to `github-style.css`:

```css
/* Tag filter pills */
.tag-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag-pill {
  padding: 4px 12px;
  font-size: 12px;
  border-radius: 2em;
  border: 1px solid var(--color-border-primary);
  background: var(--color-bg-secondary);
  color: var(--color-text-secondary);
  cursor: pointer;
}

.tag-pill:hover {
  color: var(--color-text-primary);
  border-color: var(--color-text-secondary);
}

.tag-pill--active {
  background: #6f42c1;
  color: #fff;
  border-color: #6f42c1;
}
```

- [ ] **Step 4: Verify articles tab**

Run: `hugo server -D`
Click "Articles" tab. Expected: All posts listed with tag pills. Click a tag → filters to matching articles. Click "All" → shows all.

- [ ] **Step 5: Commit**

```bash
git add themes/github-style/layouts/partials/articles-tab.html themes/github-style/static/js/github-style.js themes/github-style/static/css/github-style.css
git commit -m "feat: build articles tab with tag filtering"
```

### Task 8: Cleanup — remove old components

**Files:**
- Delete: `themes/github-style/layouts/partials/overview.html`
- Delete: `themes/github-style/layouts/partials/microblog.html`

- [ ] **Step 1: Delete old overview.html**

```bash
git rm themes/github-style/layouts/partials/overview.html
```

- [ ] **Step 2: Delete old microblog.html**

```bash
git rm themes/github-style/layouts/partials/microblog.html
```

- [ ] **Step 3: Verify build still works**

Run: `GITHUB_TOKEN=$(gh auth token) hugo --minify`
Expected: Builds successfully, no errors referencing overview.html or microblog.html.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: remove deprecated overview.html and microblog.html partials"
```

### Task 9: Add resume PDF placeholder

**Files:**
- Create: `static/resume.pdf`

- [ ] **Step 1: Add placeholder resume PDF**

Ask user to provide their resume PDF. Place it at `static/resume.pdf`. If not available yet, create a placeholder text file:

```bash
echo "Resume placeholder — replace with actual PDF" > static/resume.pdf
```

- [ ] **Step 2: Verify resume link works**

Run: `hugo server -D`
Navigate to `http://localhost:1313/resume.pdf`
Expected: File downloads.

- [ ] **Step 3: Commit**

```bash
git add static/resume.pdf
git commit -m "chore: add resume PDF placeholder"
```

---

## Chunk 4: LinkedIn Syndication + GitHub Actions

### Task 10: Update GitHub Actions — add discussion trigger

**Files:**
- Modify: `.github/workflows/hugo_build.yml`

- [ ] **Step 1: Add discussion event trigger**

In `.github/workflows/hugo_build.yml`, update the `on:` section:

```yaml
on:
  push:
    branches:
      - main
  pull_request:
    branches:
      - main
  issues:
    types: [opened, edited, closed, labeled, unlabeled]
  discussion:
    types: [created, edited, labeled]
```

- [ ] **Step 2: Verify workflow file is valid**

Run: `cat .github/workflows/hugo_build.yml | python3 -c "import sys,yaml; yaml.safe_load(sys.stdin); print('Valid YAML')"` (requires PyYAML)
Or just: `gh workflow view hugo_build.yml` after pushing.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/hugo_build.yml
git commit -m "feat: add GitHub Discussion event trigger to build workflow"
```

### Task 11: Add LinkedIn syndication job

**Files:**
- Modify: `.github/workflows/hugo_build.yml`
- Create: `docs/LINKEDIN_SETUP.md`

- [ ] **Step 1: Add syndicate-linkedin job to workflow**

Add a new job to `.github/workflows/hugo_build.yml` after the `build` job:

```yaml
  syndicate-linkedin:
    runs-on: ubuntu-latest
    if: github.event_name == 'discussion' && github.event.action == 'created'
    steps:
    - name: Check discussion category
      id: check
      env:
        CATEGORY: ${{ github.event.discussion.category.name }}
      run: |
        if [[ "$CATEGORY" == "Thoughts" || "$CATEGORY" == "Shipped" ]]; then
          echo "should_post=true" >> "$GITHUB_OUTPUT"
        else
          echo "should_post=false" >> "$GITHUB_OUTPUT"
        fi

    - name: Post to LinkedIn
      if: steps.check.outputs.should_post == 'true'
      env:
        LINKEDIN_ACCESS_TOKEN: ${{ secrets.LINKEDIN_ACCESS_TOKEN }}
        LINKEDIN_PERSON_ID: ${{ secrets.LINKEDIN_PERSON_ID }}
        TITLE: ${{ github.event.discussion.title }}
        BODY: ${{ github.event.discussion.body }}
        URL: ${{ github.event.discussion.html_url }}
      run: |
        TRUNCATED_BODY=$(echo "$BODY" | head -c 2800)
        POST_TEXT="${TITLE}

        ${TRUNCATED_BODY}

        Read more → https://atharvapandey.com"

        curl -s -X POST "https://api.linkedin.com/v2/ugcPosts" \
          -H "Authorization: Bearer $LINKEDIN_ACCESS_TOKEN" \
          -H "Content-Type: application/json" \
          -d "{
            \"author\": \"urn:li:person:${LINKEDIN_PERSON_ID}\",
            \"lifecycleState\": \"PUBLISHED\",
            \"specificContent\": {
              \"com.linkedin.ugc.ShareContent\": {
                \"shareCommentary\": { \"text\": $(echo "$POST_TEXT" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))') },
                \"shareMediaCategory\": \"NONE\"
              }
            },
            \"visibility\": { \"com.linkedin.ugc.MemberNetworkVisibility\": \"PUBLIC\" }
          }"
```

- [ ] **Step 2: Create LinkedIn setup guide**

Create `docs/LINKEDIN_SETUP.md`:

```markdown
# LinkedIn API Setup for Auto-Syndication

## One-Time Setup

1. Go to https://www.linkedin.com/developers/apps and create a new app
2. Under "Products", request access to "Share on LinkedIn"
3. Under "Auth" tab, add redirect URL: `https://localhost:3000/callback`
4. Note your Client ID and Client Secret

## Get Access Token

1. Generate OAuth URL:
   ```
   https://www.linkedin.com/oauth/v2/authorization?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=https://localhost:3000/callback&scope=w_member_social%20openid%20profile
   ```
2. Visit URL, authorize, copy the `code` param from redirect
3. Exchange for token:
   ```bash
   curl -X POST https://www.linkedin.com/oauth/v2/accessToken \
     -d "grant_type=authorization_code&code=YOUR_CODE&redirect_uri=https://localhost:3000/callback&client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET"
   ```
4. Save the `access_token` from the response

## Get Your Person ID

```bash
curl -H "Authorization: Bearer YOUR_TOKEN" https://api.linkedin.com/v2/userinfo
```

The `sub` field is your person ID.

## Store as GitHub Secrets

In your repo settings → Secrets → Actions:
- `LINKEDIN_ACCESS_TOKEN` — the access token
- `LINKEDIN_PERSON_ID` — your person ID

## Token Refresh

LinkedIn tokens expire after 60 days. Regenerate manually by repeating the OAuth flow above.
```

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/hugo_build.yml docs/LINKEDIN_SETUP.md
git commit -m "feat: add LinkedIn auto-syndication for GitHub Discussions"
```

---

## Chunk 5: Create Test Discussion + End-to-End Verification

### Task 12: Create a test Discussion and verify full flow

- [ ] **Step 1: Create a test Discussion**

```bash
gh api graphql -f query='mutation {
  createDiscussion(input: {
    repositoryId: "R_kgDOLTrS1w",
    categoryId: "<THOUGHTS_CATEGORY_ID>",
    title: "On Go error handling",
    body: "The if err != nil pattern gets hate but it forces you to think about every failure mode at the call site. That'\''s not boilerplate — that'\''s engineering discipline.\n\nCompare with try/catch where errors silently bubble up three layers before anyone notices."
  }) { discussion { url } }
}'
```

- [ ] **Step 2: Rebuild and verify feed**

```bash
GITHUB_TOKEN=$(gh auth token) hugo server -D
```

Open `http://localhost:1313/`. Expected:
- Feed tab shows the new Discussion as a "thought" card
- Article previews appear alongside it, sorted by date
- Contribution graph renders at bottom

- [ ] **Step 3: Verify Articles tab**

Click "Articles" tab. Expected:
- All posts listed with tag pills
- Tag filtering works
- Read time displayed

- [ ] **Step 4: Verify mobile layout**

Open browser DevTools → toggle device toolbar → select mobile size.
Expected: Sidebar collapses to compact header, tabs are scrollable.

- [ ] **Step 5: Verify dark/light mode**

Toggle theme switch. Expected: All new components (tabs, feed items, tag pills, streak) respect theme colors.

- [ ] **Step 6: Verify URL hash**

Navigate to `http://localhost:1313/#articles`. Expected: Articles tab is active on load.

- [ ] **Step 7: Verify fallback without token**

```bash
hugo server -D
```

Expected: Feed shows articles only, no crash, no empty state.

- [ ] **Step 8: Final commit (if any uncommitted changes remain)**

```bash
git status
git add docs/ .superpowers/
git commit -m "docs: add design spec and implementation plan for brand redesign"
```
