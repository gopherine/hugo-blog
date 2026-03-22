---
title: "Lesson 4: Recommendation Systems — Collaborative filtering, embeddings, and the cold start problem"
author: "Atharva Pandey"
series: "ML System Design"
lesson: 4
tags:
  - fundamentals
  - ML
  - system design
  - AI
keywords:
  - recommendation system design
  - collaborative filtering
  - embeddings recommendation
  - cold start problem ML
  - two-tower model
date: "2024-12-13T00:00:00.000Z"
---

Recommendation systems are the most economically consequential ML systems most engineers will ever build. Netflix estimates that its recommendation system saves over a billion dollars per year in avoided cancellations. Amazon's "customers who bought this also bought" drives a significant fraction of its revenue. Spotify's Discover Weekly has become a user retention flywheel. The stakes are real, and the engineering is genuinely interesting.

I got deep into recommendation systems when I was working on a content platform that had about 500,000 items and needed to surface relevant content for each user without overwhelming the ranking team with engineering requests. What I found was that the architecture of a production recommender is almost always the same shape, regardless of the domain — and the hardest problem is not the ML, it's the cold start.

## The ML Pipeline

A production recommendation system has three stages that chain together:

```
Candidate Generation (Recall)
        ↓
Ranking (Precision)
        ↓
Re-ranking / Business Rules (Diversity, Freshness, Constraints)
```

**Candidate generation (recall layer)** narrows a catalog of millions of items down to a few hundred plausible candidates for a given user. Speed matters here — this runs against the full item catalog. Precision is less important; recall is critical (you must not miss the item the user would love).

**Ranking (precision layer)** scores each candidate with a detailed model that uses rich features about the user, the item, and the context. Precision matters here — the user sees only the top 10–20 items. The ranking model can be expensive because it runs on ~200 candidates, not millions.

**Re-ranking** applies business rules on top of the ranked list: diversity constraints (don't show five items from the same category consecutively), freshness boosts (prefer new items), sponsored item injection, and filter exclusions (don't recommend items the user has already purchased).

## Architecture

**Collaborative Filtering**

Collaborative filtering is the foundational idea: recommend items that users similar to you liked. "Similar" is defined by behavioral overlap — users who interacted with many of the same items. The classic formulation is matrix factorization.

Let M be a user-item interaction matrix where M[u][i] = 1 if user u interacted with item i (or a rating, if available). Most entries are 0 — the matrix is extremely sparse. Matrix factorization decomposes M into two low-rank matrices: U (user embeddings, shape n_users × k) and V (item embeddings, shape n_items × k), such that M ≈ U × V^T. The embedding dimension k is typically 32–256.

```go
// User and item embeddings trained via matrix factorization
// For a given user, retrieve top-N items by embedding similarity

type EmbeddingStore interface {
    GetUserEmbedding(ctx context.Context, userID string) ([]float32, error)
    GetItemEmbedding(ctx context.Context, itemID string) ([]float32, error)
    // ANN search: find k nearest item embeddings to a query vector
    SearchNearest(ctx context.Context, queryVec []float32, k int) ([]ScoredItem, error)
}

func generateCandidates(ctx context.Context, userID string, store EmbeddingStore, k int) ([]ScoredItem, error) {
    userVec, err := store.GetUserEmbedding(ctx, userID)
    if err != nil {
        return nil, fmt.Errorf("user embedding not found for %s: %w", userID, err)
    }
    // Dot product similarity in embedding space → candidate generation
    return store.SearchNearest(ctx, userVec, k)
}

func dotProduct(a, b []float32) float32 {
    var sum float32
    for i := range a {
        sum += a[i] * b[i]
    }
    return sum
}
```

The item embeddings are stored in a vector index (Faiss, HNSW, or a managed service like Pinecone or Weaviate). At serving time, the user embedding is retrieved and an **approximate nearest neighbor (ANN)** search returns the top-K items by cosine or dot product similarity. ANN rather than exact search is used because exact search over millions of vectors at millisecond latency is not feasible — ANN trades a small recall penalty for dramatic speed improvements.

**Two-Tower Model**

Modern recommenders replace simple matrix factorization with a two-tower neural network. One tower encodes the user (their embedding plus contextual features — device, time of day, recent activity), and the other tower encodes the item (embedding plus item features — category, age, popularity). The two towers are trained jointly so that the dot product of their output vectors predicts whether a user-item interaction will occur.

The advantage over matrix factorization: the towers can incorporate rich feature sets beyond just interaction history. A user who just searched for "running shoes" has that query incorporated into their user tower embedding in real time, influencing candidates immediately.

The candidate generation step is still ANN — the item tower embeddings are pre-computed and indexed. The user tower runs at query time to produce the query vector, which is then searched against the item index.

**Ranking with Cross-Features**

The recall layer produces candidates based on similarity between independent user and item representations. The ranking layer uses **cross-features** — features that encode the interaction between a specific user and a specific item. Examples: "has this user clicked on items from this category before?", "what is the user's historical engagement with this author?", "how recently was this item published relative to the user's recency preference?"

Cross-features are expensive to compute at scale (every user × item pair for all candidates), which is why they're reserved for the ranking stage, not candidate generation.

**Approximate Nearest Neighbor at Scale**

For a catalog of 50 million items, an exact nearest neighbor search would require 50M dot products per query. At 10ms per query, you couldn't serve more than 1 query per second — not viable.

HNSW (Hierarchical Navigable Small World graphs) is the dominant ANN algorithm. It builds a layered graph where higher layers are sparse (fast traversal) and lower layers are dense (precise results). Search starts at the top layer and descends, narrowing to the most promising region of the embedding space. HNSW achieves ~95% recall compared to exact search while being 100–1000× faster.

```go
// Conceptual HNSW query — in practice, use a library like Faiss or hnswlib
type VectorIndex interface {
    Insert(id string, vec []float32) error
    Search(query []float32, k int, efSearch int) ([]SearchResult, error)
    // efSearch controls recall vs. speed tradeoff:
    // higher efSearch → better recall, slower query
}

type SearchResult struct {
    ID       string
    Distance float32
}
```

## Production Challenges

**The cold start problem**

Cold start is the fundamental challenge of recommenders: how do you recommend to a new user with no interaction history, or recommend a new item with no engagement history?

For **new users** (user cold start):
- Collect explicit signals at onboarding: ask the user their interests, genre preferences, or goals
- Use demographic and contextual signals available without history: device, time zone, referral source, language
- Fall back to popularity-based recommendations: "what most users like you engaged with in their first session"
- Use a hybrid model that blends content-based recommendations (based on item attributes) with collaborative signals (which accumulate over time)

For **new items** (item cold start):
- Represent new items purely by their content embeddings (text, image, audio features extracted by a separate content encoder)
- A new article can be embedded based on its text content before a single user has read it
- As the item accumulates interaction data, its collaborative embedding becomes more reliable and gradually replaces the content embedding
- This blend of content-based and collaborative filtering is called a **hybrid recommender**

The cold start problem never fully goes away — it recurs every time you add a new item to the catalog or acquire a new user segment. Production systems always maintain fallback strategies.

**Feedback loops and filter bubbles**

Recommendation systems are self-reinforcing. The items you recommend get seen; the items you don't recommend don't get seen. User interactions train the next model version. If the model systematically under-ranks certain items, those items accumulate less interaction data, making the model even more likely to under-rank them in the next training cycle. This is a feedback loop that amplifies initial biases.

Mitigation strategies:
- **Exploration**: intentionally recommend a small fraction of random or low-confidence items to gather interaction data across the catalog
- **Counterfactual logging**: log not just what you recommended and whether users interacted, but also (via randomized experiments) what they would have done with alternative recommendations
- **Diversity regularization in training**: add a loss term that penalizes over-concentration on popular items

**Offline vs. online metrics**

Recommendation models are evaluated offline using historical interaction data (precision@K, recall@K, NDCG). But offline metrics are poor predictors of online performance. A model that improves offline NDCG by 2% might decrease active days (a business metric) by 1% because it over-exploits user history and stops surfacing the serendipitous discoveries that keep users engaged.

The only reliable signal is an online A/B test measuring business metrics directly. Offline evaluation is for fast iteration during model development; online evaluation is for launch decisions.

## Interview Tips

Recommendation systems come up frequently in senior-level ML system design interviews. The questions are broad by design; the interviewer is assessing whether you can structure an ambiguous problem.

**Always frame the two-stage architecture.** Candidate generation + ranking is the industry-standard pattern. State it early and then discuss each stage. This shows you understand why the two-stage design exists (you can't run expensive cross-feature models on millions of items).

**Explain ANN and why exact search doesn't work.** Saying "I'll find the nearest neighbors in the embedding space" without addressing how is incomplete. Mention HNSW or Faiss, and explain the recall vs. speed tradeoff.

**Address cold start directly.** It will be asked. Have a crisp answer: content-based embeddings for new items, explicit preference collection plus popularity-based fallback for new users, hybrid models as history accumulates.

**Talk about feedback loops.** This signals production awareness. Pure exploitation of the recommendation model creates filter bubbles and catalog coverage problems. Mention exploration (epsilon-greedy or Thompson sampling style) as the mitigation.

**Be honest about offline vs. online metrics.** Candidates who only talk about offline metrics look inexperienced. Acknowledge that offline NDCG improvements don't reliably translate to business metric improvements, and that A/B tests are the source of truth.

## Key Takeaway

A production recommendation system is not a single model — it's a pipeline of three stages (candidate generation, ranking, re-ranking) that balance recall, precision, and business constraints respectively. The two-tower model with ANN search is the modern standard for candidate generation. The cold start problem is a persistent operational challenge, not a one-time solvable bug, and hybrid models that blend content signals with collaborative signals are the practical answer. The deepest lesson: every interaction with a recommender produces training data for the next model version, which means the recommender shapes its own future training data. Building in exploration and diversity from the start is not idealistic — it's how you prevent the system from converging on a narrow, stale understanding of user interests.

---

**Previous:** [Lesson 3: Model Serving](/post/fundamentals/ml-model-serving/)

---

## 🎓 Course Complete!

You've finished **ML System Design**. Over four lessons we covered the full journey from raw data to deployed, production recommendations:

- **ML Pipelines**: the six-stage pipeline from data ingestion to deployment, orchestration DAGs, idempotent stages, and why the pipeline breaks silently if you don't design it to fail loudly
- **Feature Stores**: the offline/online split, point-in-time correctness to prevent temporal leakage, materialization pipelines, and training-serving skew as the root cause of most production ML failures
- **Model Serving**: latency budget decomposition, dynamic batching for GPU efficiency, shadow mode and canary rollouts, A/B testing ML models, and distinguishing data drift from concept drift
- **Recommendation Systems**: the two-stage architecture (recall + ranking), two-tower models, HNSW for approximate nearest neighbor search, the cold start problem, and the feedback loop risk

These four systems capture the core patterns that appear in virtually every production ML design question: pipelines, feature management, serving infrastructure, and learning from implicit user feedback. Understanding them end to end — not just the models but the infrastructure around them — is what separates engineers who can build ML systems that work in production from those who can only build models that work in notebooks.
