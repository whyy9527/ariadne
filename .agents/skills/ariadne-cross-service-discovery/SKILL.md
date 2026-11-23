---
name: ariadne-cross-service-discovery
description: |
  Use Ariadne to discover candidate GraphQL, HTTP, Kafka, and frontend call chains when a task crosses services or owning repositories/files are unknown. Query first for ambiguous cross-service discovery, verify returned paths in source, and do not freeze mutable repository or scanner inventory in this skill.
---

# Ariadne cross-service discovery

## Use it when

- a business term may span repositories;
- the upstream/downstream owner of an endpoint, event, or operation is unknown;
- you need candidate files before tracing source;
- a symptom may cross frontend, gateway, service, or event boundaries.

Skip it when the task is demonstrably local to one known file/repository or
the complete call path is already supplied. Ariadne is a discovery index, not
a substitute for reading current source.

## Current workflow

1. Start from a discriminative business term, operation, route, field, or
   event:

   ```text
   query_chains(hint="createOrder")
   ```

2. Choose a relevant returned node and expand one hop:

   ```text
   expand_node(name="<exact returned node name>")
   ```

3. Read the returned files and verify the chain against current code,
   configuration, and runtime evidence.
4. After usefulness is known, record feedback:

   ```text
   rate_result(hint="createOrder", cluster_rank=1, accepted=true)
   ```

   Supply `node_ids` when the useful nodes are known. Use `cluster_rank=0`
   for feedback about an `expand_node` result.

## Freshness and setup

`query_chains` and `expand_node` return `stale_warning`.

- If it is non-null, call `rescan()` once when refreshing the local index is
  within task scope, then retry the query.
- If rescan is unavailable or fails, disclose the stale-index limit and
  continue with repository search.
- If results are empty or setup is unclear, call `show_help()` and inspect the
  currently installed configuration/index rather than assuming a hard-coded
  repository list.

The installed configuration is authoritative for repository coverage and
scanner overrides. Repositories and scanner auto-detection change over time;
do not infer coverage from an old table copied into instructions.

## Hint selection

Prefer the most specific known identifier:

1. exact operation, endpoint, field, event, or client name;
2. domain identifier used in code;
3. concise natural-language business phrase.

If a broad term returns noisy clusters, refine with an identifier from the
best candidate. Copy exact node names into `expand_node` because partial
matching can be ambiguous.

## Evidence boundary

A returned edge is a candidate from the indexed snapshot. Before asserting a
live chain:

- inspect the cited files;
- check conditionals, feature flags, generated clients, and configuration;
- distinguish static reachability from a runtime-observed call;
- use logs/traces/network evidence for runtime claims;
- note staleness or missing repositories.

Feedback describes whether the result helped discovery; it does not certify
the chain as correct.

## Failure fallback

When Ariadne is absent, stale beyond safe use, or lacks the repository:

1. search exact operation/route/event identifiers with `rg`;
2. follow imports, client definitions, handlers, and consumers;
3. inspect the active workspace config;
4. report the discovery boundary.
