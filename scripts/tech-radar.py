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
    items = []
    for lang in ["go", "rust", "typescript"]:
        try:
            date_prefix = datetime.now().strftime("%Y-%m-") + "01"
            data = fetch_url(
                f"https://api.github.com/search/repositories?q=created:>{date_prefix}+language:{lang}&sort=stars&order=desc&per_page=3"
            )
            for r in json.loads(data).get("items", [])[:2]:
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
    items = []
    try:
        with urllib.request.urlopen("https://hacker-news.firebaseio.com/v0/topstories.json") as r:
            top_ids = json.loads(r.read())[:15]
        for sid in top_ids:
            try:
                with urllib.request.urlopen(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json") as r:
                    s = json.loads(r.read())
                    if s.get("score", 0) > 100 and s.get("url"):
                        items.append(f"[HN/{s['score']}pts] {s['title']}\nURL: {s['url']}")
                        if len(items) >= 5:
                            break
            except Exception:
                continue
    except Exception as e:
        print(f"  HN: {e}")
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


PROMPT_TEMPLATE = """You are writing a structured technical LinkedIn post for senior engineers who build production systems in Go, Rust, and TypeScript. They work on AI/LLM tooling, distributed systems, and developer infrastructure.

Raw material from GitHub Trending, Lobste.rs, Hacker News, and arXiv:

{context}

YOUR JOB: Pick the ONE most technically interesting item. Read its README/abstract/description carefully. Write a structured technical post (300-450 words).

FORMAT YOUR POST WITH THIS EXACT STRUCTURE using markdown bold headers:

1. Opening line — one punchy sentence that states the core technical insight (no intro fluff)

2. **The architecture decision:**
   2-3 sentences explaining what technical choice was made and WHY. Name specific patterns, data structures, syscalls, protocols.

3. **The tradeoff nobody talks about:**
   2-3 sentences on the cost of this approach. When does it break? What workloads defeat it? Be specific — mention numbers, thresholds, failure modes.

4. **The implementation detail:**
   2-3 sentences about something you'd only learn by reading the source code or paper. A specific function, algorithm choice, or design constraint.

5. **The engineering principle:**
   2-3 sentences connecting this to a broader pattern. "This is the same pattern as X in Y" — help the reader connect new knowledge to what they already know.

6. Source link as markdown.

EXAMPLE POST:

Zeroboot creates VM sandboxes in under a millisecond using copy-on-write forking. Here's why that matters technically:

**The architecture decision:**
Instead of booting a fresh VM, Zeroboot fork()s an already-running VM snapshot. The child process shares the parent's memory pages until either process writes — then the kernel copies just that page. Full isolation with near-zero startup cost.

**The tradeoff nobody talks about:**
CoW forking is fast for READ-heavy workloads. But if your AI agent writes to many memory pages quickly, you trigger a storm of page faults — each one copies a 4KB page. An agent that allocates 100MB of heap on startup defeats the entire purpose.

**The implementation detail:**
Zeroboot uses libc directly for fork semantics rather than a hypervisor layer. Sandboxes are Linux processes, not VMs — they share the host kernel. Faster, but the isolation boundary is the process, not hardware-enforced.

**The engineering principle:**
Same pattern as Redis BGSAVE — fork the process, let the child serialize state while the parent continues serving. CoW makes the fork nearly free. If your workload is read-heavy with occasional writes, CoW forking gives you snapshot isolation at almost zero cost.

RULES:
- ZERO marketing words (exciting, innovative, game-changer, powerful, revolutionary)
- ZERO filler intros (In today's world, As engineers, Let's dive in)
- Every sentence teaches something specific
- Title = a specific technical claim, NOT a topic label
- Include source URL as markdown link

Return ONLY valid JSON: {{"title": "your specific technical claim", "body": "your structured post"}}"""


def main():
    print("Fetching sources...")
    gh = fetch_github_trending()
    lb = fetch_lobsters()
    hn = fetch_hn()
    ax = fetch_arxiv()

    print(f"Sources: {len(gh)} GitHub, {len(lb)} Lobsters, {len(hn)} HN, {len(ax)} arXiv")

    all_items = gh + lb + hn + ax
    if len(all_items) < 2:
        print("Not enough content. Skipping.")
        return

    context = "\n\n---\n\n".join(all_items[:12])
    prompt = PROMPT_TEMPLATE.format(context=context)

    print("Generating with Groq...")
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
            print(f"Failed to parse response: {raw_json[:200]}")
            return

    title = post.get("title", "")
    body = post.get("body", "")

    if len(body) < 100:
        print("Content too short. Skipping.")
        return

    print(f"Generated: {title} ({len(body)} chars)")

    # Create draft discussion
    repo_id = os.environ.get("REPO_ID", "R_kgDOLTrS1w")
    cat_id = os.environ.get("THOUGHTS_CAT_ID", "DIC_kwDOLTrS184C468G")

    draft_body = (
        f"**DRAFT** — Review before publishing\n\n---\n\n{body}\n\n---\n\n"
        f"*Auto-generated. Remove [DRAFT] prefix from title to publish to site + LinkedIn.*"
    )

    mutation = json.dumps({
        "query": (
            f'mutation {{ createDiscussion(input: {{'
            f'repositoryId: "{repo_id}", '
            f'categoryId: "{cat_id}", '
            f'title: {json.dumps("[DRAFT] " + title)}, '
            f'body: {json.dumps(draft_body)}'
            f'}}) {{ discussion {{ url }} }} }}'
        )
    })

    result = subprocess.run(
        ["curl", "-s", "-X", "POST", "https://api.github.com/graphql",
         "-H", f"Authorization: Bearer {os.environ['GH_TOKEN']}",
         "-H", "Content-Type: application/json",
         "-d", mutation],
        capture_output=True, text=True)

    data = json.loads(result.stdout)
    url = data.get("data", {}).get("createDiscussion", {}).get("discussion", {}).get("url", "unknown")
    print(f"Draft created: {url}")


if __name__ == "__main__":
    main()
