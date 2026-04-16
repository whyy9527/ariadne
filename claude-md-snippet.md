
## Ariadne: cross-service chain hinter

For any task involving business features, API calls, or cross-service flows, **query Ariadne first, then read files**.

### Usage protocol

**At task start:**

1. `query_chains(hint="business term or operation name")` — find candidate cross-service chains
2. `expand_node(name="specific node name")` — expand one hop from a key node
3. Decide which repos / files to read based on the result

**How to write hints:**
- Business terms: `"createOrder"` / `"userSubscription"` / `"paymentRefund"`
- Operation names: `"createTask"` / `"order.created"`
- Natural language: `"checkout flow"` / `"refund eligibility"`

**When to skip Ariadne:**
- Task is clearly scoped to a single repo (e.g. pure frontend styling)
- You already know the full call path; no discovery needed

### Feedback protocol

After using each Ariadne result, call `rate_result` to mark whether it helped:

```
rate_result(hint="...", cluster_rank=1, node_ids=[...], accepted=true/false)
```

- `accepted=true`: result helped locate files / understand the chain
- `accepted=false`: result was irrelevant or misleading
