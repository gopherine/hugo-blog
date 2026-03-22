#!/usr/bin/env python3
"""Tech Radar Draft Generator — fetches from multiple sources, generates deep technical content."""

import json
import os
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
import re
import base64
from datetime import datetime


def fetch_url(url):
    req = urllib.request.Request(url, headers={"User-Agent": "TechRadar/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read().decode("utf-8", errors="replace")


def groq(prompt, max_tokens=800):
    result = subprocess.run(
        ["curl", "-s", "https://api.groq.com/openai/v1/chat/completions",
         "-H", f"Authorization: Bearer {os.environ['GROQ_API_KEY']}",
         "-H", "Content-Type: application/json",
         "-d", json.dumps({
             "model": "llama-3.3-70b-versatile",
             "messages": [{"role": "user", "content": prompt}],
             "temperature": 0.7,
             "max_tokens": max_tokens
         })],
        capture_output=True, text=True)
    resp = json.loads(result.stdout)
    return resp["choices"][0]["message"]["content"]


def fetch_github_trending():
    """Fetch recently created repos — use a 7-day window instead of month-start
    to avoid the same repos sitting at the top for weeks."""
    items = []
    from datetime import timedelta
    # Look back 7 days so results rotate daily
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    for lang in ["go", "rust", "typescript"]:
        try:
            data = fetch_url(
                f"https://api.github.com/search/repositories?q=created:>{week_ago}+language:{lang}&sort=stars&order=desc&per_page=5"
            )
            repos = json.loads(data).get("items", [])
            # Skip top 1-2 (likely already covered), take next 2-3 for freshness
            import random
            sample = repos[1:5] if len(repos) > 4 else repos[:3]
            random.shuffle(sample)
            for r in sample[:2]:
                readme = ""
                try:
                    rd = json.loads(fetch_url(f"https://api.github.com/repos/{r['full_name']}/readme"))
                    readme = base64.b64decode(rd["content"]).decode("utf-8", errors="replace")[:1500]
                except Exception:
                    pass
                items.append(
                    f"[GitHub/{lang}] {r['full_name']} — {r.get('description', '')}\n"
                    f"Stars: {r['stargazers_count']}\nURL: {r['html_url']}\n"
                    f"README: {readme[:800]}"
                )
        except Exception as e:
            print(f"  GitHub {lang}: {e}")
    return items


def fetch_lobsters():
    items = []
    try:
        rss = fetch_url("https://lobste.rs/rss")
        root = ET.fromstring(rss)
        relevant_tags = {
            "programming", "rust", "go", "ai", "distributed", "performance",
            "systems", "ml", "typescript", "security", "compilers", "devops"
        }
        for item in root.findall(".//item")[:10]:
            tags = [c.text for c in item.findall("category") if c.text]
            if any(t in relevant_tags for t in tags):
                desc = re.sub(r"<[^>]+>", "", item.findtext("description", ""))
                items.append(
                    f"[Lobsters] {item.findtext('title', '')} (tags: {', '.join(tags)})\n"
                    f"URL: {item.findtext('link', '')}\n{desc[:500]}"
                )
    except Exception as e:
        print(f"  Lobsters: {e}")
    return items


def fetch_hn():
    """Fetch from both top and best stories for more variety."""
    items = []
    import random
    # Mix top + best stories for variety — top changes slowly, best rotates more
    endpoints = ["topstories", "beststories"]
    seen_ids = set()
    for endpoint in endpoints:
        try:
            with urllib.request.urlopen(f"https://hacker-news.firebaseio.com/v0/{endpoint}.json") as r:
                story_ids = json.loads(r.read())[:30]
            # Shuffle to avoid always picking the same top ones
            random.shuffle(story_ids)
            for sid in story_ids:
                if sid in seen_ids:
                    continue
                seen_ids.add(sid)
                try:
                    with urllib.request.urlopen(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json") as r:
                        s = json.loads(r.read())
                        if s.get("score", 0) > 80 and s.get("url"):
                            items.append(f"[HN/{s['score']}pts] {s['title']}\nURL: {s['url']}")
                            if len(items) >= 8:
                                return items
                except Exception:
                    continue
        except Exception as e:
            print(f"  HN {endpoint}: {e}")
    return items


def fetch_arxiv():
    items = []
    try:
        arxiv_rss = fetch_url("https://rss.arxiv.org/rss/cs.DC+cs.AI+cs.PL+cs.SE")
        root = ET.fromstring(arxiv_rss)
        ns = {"rss": "http://purl.org/rss/1.0/"}
        for item in root.findall(".//rss:item", ns)[:5]:
            title = item.findtext("rss:title", "", ns).strip()
            link = item.findtext("rss:link", "", ns).strip()
            desc = re.sub(r"<[^>]+>", "", item.findtext("rss:description", "", ns))
            if title:
                items.append(f"[arXiv] {title}\nURL: {link}\nAbstract: {desc[:500]}")
    except Exception as e:
        print(f"  arXiv: {e}")
    return items


PROMPT_TEMPLATE = """You are Atharva — a polyglot engineer who builds production systems in Go, Rust, and TypeScript. You've been a CTO, shipped AI tooling and MCP servers, and you have strong opinions about systems design. You write like you talk — direct, technical, occasionally blunt.

Raw material from GitHub Trending, Lobste.rs, Hacker News, and arXiv:

{context}

Review ALL items across all sources. Pick the ONE most interesting item you haven't seen before — prioritize novelty over star count. Prefer items from different sources each time. Read its README/abstract carefully. Write a LinkedIn post (200-350 words) as if you just discovered this and are telling your engineer friends about it.

VOICE EXAMPLES (study how these sound human, not AI):

Example 1:
"Stumbled on Zeroboot today. It spins up VM sandboxes in under a millisecond.

The trick is copy-on-write forking. Instead of booting a fresh VM, it fork()s a running snapshot. Parent and child share memory pages until someone writes — then the kernel copies just that page. Neat.

Here's what nobody mentions though: this falls apart for write-heavy agents. If your LLM agent allocates 100MB of heap on startup, you're triggering thousands of page faults. Each one copies a 4KB page. Your "fast sandbox" becomes a page fault storm.

They went with libc fork() directly instead of a hypervisor — so these are processes, not VMs. Faster, but you're trusting process isolation, not hardware isolation. For running untrusted agent code, that's a real security call.

Same pattern as Redis BGSAVE honestly — fork, let the child work while the parent keeps serving. Works beautifully when reads dominate writes.

Worth a look if you're building agent infra: [link]"

Example 2:
"I keep seeing Go repos reinvent error handling with generics. Please stop.

Yes, Result<T, E> works in Rust. No, wrapping every Go return in a generic monad does not make your code better. It makes it unreadable to every other Go developer on your team.

if err != nil isn't a bug — it's a feature. It forces you to handle errors at the call site. Try/catch lets you pretend errors don't exist for three stack frames.

Go's error handling is verbose on purpose. The verbosity IS the error handling."

WHAT MAKES THESE HUMAN (follow these rules strictly):
- First person — "I", "you", "we"
- NO bold headers, NO section labels, NO "The architecture decision:" format
- NO uniform paragraph lengths — mix 1 sentence paras with 3-4 sentence paras
- Has a real opinion — agrees, disagrees, warns, recommends
- Uses contractions — "it's", "don't", "here's", "that's"
- Uses dashes, rhetorical questions, sentence fragments
- Sounds like a Slack message to a senior engineer, not a conference talk
- At least one specific technical detail (syscall, data structure, number)
- ZERO marketing words (exciting, innovative, powerful, game-changer, revolutionary)
- ZERO filler openings (In today's world, As engineers, Let me share, Let's dive)
- ZERO "This approach" "This technique" "This pattern" repetition
- Include source URL as markdown link
- Title = a hook that makes engineers stop scrolling (claim, question, or hot take)

Return ONLY valid JSON: {{"title": "your hook", "body": "your post"}}"""


def main():
    print("Fetching sources...")
    gh = fetch_github_trending()
    lb = fetch_lobsters()
    hn = fetch_hn()
    ax = fetch_arxiv()

    print(f"Sources: {len(gh)} GitHub, {len(lb)} Lobsters, {len(hn)} HN, {len(ax)} arXiv")

    import random
    all_items = gh + lb + hn + ax
    random.shuffle(all_items)  # Shuffle so the LLM doesn't always fixate on first items
    if len(all_items) < 2:
        print("Not enough content. Skipping.")
        return

    # Fetch existing discussions once for dedup
    repo_id = os.environ.get("REPO_ID", "R_kgDOLTrS1w")
    cat_id = os.environ.get("THOUGHTS_CAT_ID", "DIC_kwDOLTrS184C468G")
    existing_titles = []
    dedup_query = json.dumps({"query": f'{{ repository(owner: "gopherine", name: "hugo-blog") {{ discussions(categoryId: "{cat_id}", first: 30, orderBy: {{field: CREATED_AT, direction: DESC}}) {{ nodes {{ title }} }} }} }}'})
    dedup_result = subprocess.run(
        ["curl", "-s", "-X", "POST", "https://api.github.com/graphql",
         "-H", f"Authorization: Bearer {os.environ['GH_TOKEN']}",
         "-H", "Content-Type: application/json",
         "-d", dedup_query],
        capture_output=True, text=True)
    try:
        existing = json.loads(dedup_result.stdout)
        existing_titles = [d["title"].lower() for d in existing.get("data", {}).get("repository", {}).get("discussions", {}).get("nodes", [])]
        print(f"Dedup: {len(existing_titles)} existing discussions loaded")
    except Exception as e:
        print(f"Dedup check failed: {e}, proceeding without dedup")

    # Stop words to ignore in dedup overlap check
    STOP_WORDS = {
        "a", "an", "the", "is", "it", "in", "on", "of", "to", "for", "and",
        "or", "but", "with", "this", "that", "are", "was", "be", "has", "had",
        "not", "no", "do", "does", "can", "could", "will", "would", "should",
        "from", "by", "at", "as", "if", "so", "just", "how", "what", "why",
        "when", "where", "who", "which", "your", "you", "my", "i", "we",
        "-", "--", "—", "vs", "about", "into", "its", "i'm", "here's",
    }

    def is_duplicate(title):
        """Check if title overlaps significantly with existing discussions."""
        title_words = set(title.lower().split()) - STOP_WORDS
        # Remove very short words (1-2 chars)
        title_words = {w for w in title_words if len(w) > 2}
        for et in existing_titles:
            et_words = set(et.split()) - STOP_WORDS
            et_words = {w for w in et_words if len(w) > 2}
            overlap = title_words & et_words
            # Require 4+ meaningful word overlap to consider it a duplicate
            if len(overlap) >= 4:
                print(f"  Dedup match: '{et}' (overlap: {overlap})")
                return True
        return False

    # Try up to 3 times with different source slices to get a non-duplicate draft
    max_attempts = 3
    posted = False
    for attempt in range(max_attempts):
        # Rotate which items go first so the LLM picks different topics
        offset = attempt * 4
        rotated = all_items[offset:] + all_items[:offset]
        context = "\n\n---\n\n".join(rotated[:12])
        prompt = PROMPT_TEMPLATE.format(context=context)

        print(f"Generating with Groq (attempt {attempt + 1}/{max_attempts})...")
        content = groq(prompt)

        start = content.find("{")
        end = content.rfind("}") + 1
        raw_json = content[start:end]
        # Fix unescaped newlines inside JSON string values
        raw_json = raw_json.replace("\n", "\\n").replace("\t", "\\t")
        # But don't double-escape already escaped ones
        raw_json = raw_json.replace("\\\\n", "\\n")
        try:
            post = json.loads(raw_json)
        except json.JSONDecodeError:
            # Fallback: extract title and body manually
            title_match = re.search(r'"title"\s*:\s*"(.*?)"', raw_json)
            body_start = raw_json.find('"body"')
            if title_match and body_start != -1:
                title_val = title_match.group(1)
                body_val = raw_json[body_start:].split(':', 1)[1].strip().strip('"').rstrip('}"').strip('"')
                post = {"title": title_val, "body": body_val.replace("\\n", "\n")}
            else:
                print(f"  Failed to parse response: {raw_json[:200]}")
                continue

        title = post.get("title", "")
        body = post.get("body", "")

        if len(body) < 100:
            print(f"  Content too short ({len(body)} chars). Retrying...")
            continue

        print(f"  Generated: {title} ({len(body)} chars)")

        if is_duplicate(title):
            print(f"  Duplicate detected, retrying with different sources...")
            continue

        # Success — we have a non-duplicate draft
        posted = True
        break

    if not posted:
        print("All attempts produced duplicates or failures. Skipping this run.")
        return

    # Create draft discussion
    # No preamble — the "draft" label is the only gate.
    # Content goes directly to site + LinkedIn when label is removed.
    draft_body = body

    # Create discussion
    mutation = json.dumps({
        "query": (
            f'mutation {{ createDiscussion(input: {{'
            f'repositoryId: "{repo_id}", '
            f'categoryId: "{cat_id}", '
            f'title: {json.dumps(title)}, '
            f'body: {json.dumps(draft_body)}'
            f'}}) {{ discussion {{ id url }} }} }}'
        )
    })

    result = subprocess.run(
        ["curl", "-s", "-X", "POST", "https://api.github.com/graphql",
         "-H", f"Authorization: Bearer {os.environ['GH_TOKEN']}",
         "-H", "Content-Type: application/json",
         "-d", mutation],
        capture_output=True, text=True)

    data = json.loads(result.stdout)
    discussion = data.get("data", {}).get("createDiscussion", {}).get("discussion", {})
    url = discussion.get("url", "unknown")
    disc_id = discussion.get("id", "")
    print(f"Discussion created: {url}")

    # Add "draft" label to the discussion
    if disc_id:
        # First get the label ID
        label_result = subprocess.run(
            ["curl", "-s", "-X", "POST", "https://api.github.com/graphql",
             "-H", f"Authorization: Bearer {os.environ['GH_TOKEN']}",
             "-H", "Content-Type: application/json",
             "-d", json.dumps({"query": f'{{ repository(owner: "gopherine", name: "hugo-blog") {{ label(name: "draft") {{ id }} }} }}'})],
            capture_output=True, text=True)
        label_data = json.loads(label_result.stdout)
        label_id = label_data.get("data", {}).get("repository", {}).get("label", {}).get("id", "")

        if label_id:
            add_label = json.dumps({"query": f'mutation {{ addLabelsToLabelable(input: {{labelableId: "{disc_id}", labelIds: ["{label_id}"]}}) {{ clientMutationId }} }}'})
            label_resp = subprocess.run(
                ["curl", "-s", "-X", "POST", "https://api.github.com/graphql",
                 "-H", f"Authorization: Bearer {os.environ['GH_TOKEN']}",
                 "-H", "Content-Type: application/json",
                 "-d", add_label],
                capture_output=True, text=True)
            if "errors" in label_resp.stdout:
                print(f"WARNING: Failed to add draft label: {label_resp.stdout[:200]}")
            else:
                print(f"Draft label added. Remove label to publish.")
        else:
            print("WARNING: Could not find 'draft' label. Discussion created WITHOUT draft label!")


if __name__ == "__main__":
    main()
