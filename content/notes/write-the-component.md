---
title: Write the component, not the framework
date: 2026-09-24
tags: [architecture, scaling]
---

Most systems are read-heavy long before they are write-heavy. Cache the read path first, and you buy yourself months of runway before the write path becomes the problem.

This is a note — a short, markdown-native post that lives in `content/notes/`, not a discussion thread. It uses the same pipeline as every article on the site: commit, and it ships.
