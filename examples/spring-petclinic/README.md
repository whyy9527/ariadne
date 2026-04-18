# Example: Spring PetClinic Microservices

End-to-end public demo: clone a well-known open-source microservice sample,
scan it with Ariadne, run a cross-service query, and see a real cluster.

[spring-petclinic-microservices](https://github.com/spring-petclinic/spring-petclinic-microservices)
is the canonical Spring Boot + Spring Cloud sample: five services, REST
endpoints, `*Resource.java` naming. No private fixtures — anyone can
reproduce this.

## Run it

```bash
# 1. Pick a workspace directory
mkdir ~/ariadne-demo && cd ~/ariadne-demo

# 2. Clone the sample
git clone --depth 1 https://github.com/spring-petclinic/spring-petclinic-microservices.git

# 3. Drop this config in (same directory as the clone)
curl -O https://raw.githubusercontent.com/whyy9527/ariadne/main/examples/spring-petclinic/ariadne.config.json

# 4. Scan (assumes ariadne cloned elsewhere, e.g. ~/src/ariadne)
python3 ~/src/ariadne/main.py --db ./petclinic.db scan --config ./ariadne.config.json

# 5. Ask a cross-service question
python3 ~/src/ariadne/main.py --db ./petclinic.db query "owner"
```

## What you'll see

```
[auto-detect] spring-petclinic-api-gateway: scanners = ['http', 'kafka', 'backend_clients']
[auto-detect] spring-petclinic-customers-service: scanners = ['http', 'kafka', 'backend_clients']
[auto-detect] spring-petclinic-vets-service: scanners = ['http', 'kafka', 'backend_clients']
[auto-detect] spring-petclinic-visits-service: scanners = ['http', 'kafka', 'backend_clients']
[auto-detect] spring-petclinic-genai-service: scanners = ['http', 'kafka', 'backend_clients']
[1/4] Scanning 5 repos ...
  spring-petclinic-api-gateway: RESCAN http=2, kafka=0, backend_clients=0
  spring-petclinic-customers-service: RESCAN http=8, kafka=0, backend_clients=0
  spring-petclinic-vets-service: RESCAN http=1, kafka=0, backend_clients=0
  spring-petclinic-visits-service: RESCAN http=3, kafka=0, backend_clients=0
  spring-petclinic-genai-service: RESCAN http=0, kafka=0, backend_clients=0
...
Nodes: 14, Edges: 33
```

And the query result — the point of the tool:

```
Query: owner
==================================================

Top Cluster #1  [confidence: 0.555]
  Services: spring-petclinic-api-gateway, spring-petclinic-customers-service
  - [spring-petclinic-customers-service] HTTP PUT  /owners/{ownerId}:      updateOwner
  - [spring-petclinic-api-gateway]       HTTP GET  /api/gateway/owners/{ownerId}: getOwnerDetails
  - [spring-petclinic-customers-service] HTTP GET  /owners/{ownerId}:      findOwner

Top Cluster #2  [confidence: 0.52]
  Services: spring-petclinic-customers-service, spring-petclinic-visits-service
  - [spring-petclinic-customers-service] HTTP POST /owners:                createOwner
  - [spring-petclinic-visits-service]    HTTP POST /owners/*/pets/{petId}/visits: create
```

Cluster #1 shows the gateway → customers-service hop for owner lookup.
Cluster #2 shows how `owner` fans out into pet-visit creation on a
different service. Five repos' worth of REST endpoints, one business
term, ~500 tokens.

## Stack coverage on this demo

| Scanner          | Hit | Why |
|------------------|-----|-----|
| `http`           | 14 endpoints | Spring `@GetMapping` / `@PostMapping` etc. on `*Resource.java` |
| `kafka`          | 0    | PetClinic doesn't use Kafka |
| `backend_clients`| 0    | Gateway uses Spring Cloud Gateway (declarative routes), not `RestClient` |
| `graphql`        | —    | Not declared (no SDL in repos) |
| `frontend_*`     | —    | Frontend is Thymeleaf templates, not a TypeScript SPA |

This demo is deliberately narrow — one stack (Spring Boot + REST) — so
it's easy to reason about what the tool found. For a richer chain (REST
+ Kafka + GraphQL BFF + TS frontend), point Ariadne at your own
polyglot workspace or combine multiple samples.
