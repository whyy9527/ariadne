"""
Build a demo Ariadne DB with hand-crafted sample nodes from a fictional
microservice stack (web + gateway + orders-svc + billing-svc + users-svc).

Usage:
  python3 demo.py [--db PATH]

Produces an SQLite DB that the MCP server and CLI can immediately query,
so hosted environments (e.g. Glama.ai's "Try in Browser") return non-empty
results without needing a real codebase to scan.
"""
import argparse
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _DIR)

from normalizer.normalizer import normalize
from scoring.engine import compute_idf, set_idf, score_all_pairs
from store.db import DB


DEMO_NODES = [
    # ── createOrder chain ──────────────────────────────────────────────────
    {
        "id": "web::fe::createOrder",
        "type": "frontend_mutation",
        "raw_name": "createOrder",
        "service": "web",
        "source_file": "web/src/api/orders.ts",
        "fields": ["items", "userId", "shippingAddress"],
    },
    {
        "id": "gateway::gql::Mutation::createOrder",
        "type": "graphql_mutation",
        "raw_name": "createOrder",
        "service": "gateway",
        "source_file": "gateway/schema/orders.graphql",
        "fields": ["input", "items", "userId"],
    },
    {
        "id": "orders-svc::http::POST::/orders",
        "type": "http_endpoint",
        "raw_name": "createOrder",
        "service": "orders-svc",
        "source_file": "orders-svc/src/controllers/OrderController.java",
        "method": "POST",
        "path": "/orders",
        "fields": ["orderRequest", "items", "userId"],
    },
    {
        "id": "orders-svc::kafka::order-created",
        "type": "kafka_topic",
        "raw_name": "order-created",
        "service": "orders-svc",
        "source_file": "orders-svc/src/main/resources/application.yaml",
        "fields": ["orderId", "userId", "totalAmount"],
    },
    {
        "id": "billing-svc::kafka::listener::chargeCustomer",
        "type": "kafka_topic",
        "raw_name": "order-created",
        "service": "billing-svc",
        "source_file": "billing-svc/src/listeners/OrderListener.java",
        "fields": ["orderId", "userId", "totalAmount"],
    },

    # ── getOrder / userOrders query chain ─────────────────────────────────
    {
        "id": "web::fe::getUserOrders",
        "type": "frontend_query",
        "raw_name": "getUserOrders",
        "service": "web",
        "source_file": "web/src/api/orders.ts",
        "fields": ["userId", "status"],
    },
    {
        "id": "gateway::gql::Query::userOrders",
        "type": "graphql_query",
        "raw_name": "userOrders",
        "service": "gateway",
        "source_file": "gateway/schema/orders.graphql",
        "fields": ["userId", "status"],
    },
    {
        "id": "orders-svc::http::GET::/users/{id}/orders",
        "type": "http_endpoint",
        "raw_name": "getUserOrders",
        "service": "orders-svc",
        "source_file": "orders-svc/src/controllers/OrderController.java",
        "method": "GET",
        "path": "/users/{id}/orders",
        "fields": ["userId", "status"],
    },

    # ── user profile chain ────────────────────────────────────────────────
    {
        "id": "web::fe::getUserProfile",
        "type": "frontend_query",
        "raw_name": "getUserProfile",
        "service": "web",
        "source_file": "web/src/api/users.ts",
        "fields": ["userId"],
    },
    {
        "id": "gateway::gql::Query::userProfile",
        "type": "graphql_query",
        "raw_name": "userProfile",
        "service": "gateway",
        "source_file": "gateway/schema/users.graphql",
        "fields": ["userId"],
    },
    {
        "id": "users-svc::http::GET::/users/{id}",
        "type": "http_endpoint",
        "raw_name": "getUserProfile",
        "service": "users-svc",
        "source_file": "users-svc/src/controllers/UserController.java",
        "method": "GET",
        "path": "/users/{id}",
        "fields": ["userId", "name", "email"],
    },
    {
        "id": "web::fe::updateUserProfile",
        "type": "frontend_mutation",
        "raw_name": "updateUserProfile",
        "service": "web",
        "source_file": "web/src/api/users.ts",
        "fields": ["userId", "name", "email"],
    },
    {
        "id": "gateway::gql::Mutation::updateUserProfile",
        "type": "graphql_mutation",
        "raw_name": "updateUserProfile",
        "service": "gateway",
        "source_file": "gateway/schema/users.graphql",
        "fields": ["userId", "input"],
    },
    {
        "id": "users-svc::http::PUT::/users/{id}",
        "type": "http_endpoint",
        "raw_name": "updateUserProfile",
        "service": "users-svc",
        "source_file": "users-svc/src/controllers/UserController.java",
        "method": "PUT",
        "path": "/users/{id}",
        "fields": ["userId", "name", "email"],
    },

    # ── payment / refund chain ────────────────────────────────────────────
    {
        "id": "billing-svc::http::POST::/payments/refund",
        "type": "http_endpoint",
        "raw_name": "refundPayment",
        "service": "billing-svc",
        "source_file": "billing-svc/src/controllers/PaymentController.java",
        "method": "POST",
        "path": "/payments/refund",
        "fields": ["paymentId", "amount", "reason"],
    },
    {
        "id": "billing-svc::kafka::payment-refunded",
        "type": "kafka_topic",
        "raw_name": "payment-refunded",
        "service": "billing-svc",
        "source_file": "billing-svc/src/main/resources/application.yaml",
        "fields": ["paymentId", "orderId", "amount"],
    },
    {
        "id": "gateway::gql::Subscription::orderUpdates",
        "type": "graphql_subscription",
        "raw_name": "orderUpdates",
        "service": "gateway",
        "source_file": "gateway/schema/orders.graphql",
        "fields": ["orderId", "status"],
    },
    {
        "id": "web::fe::subscribeOrderUpdates",
        "type": "frontend_query",
        "raw_name": "subscribeOrderUpdates",
        "service": "web",
        "source_file": "web/src/api/subscriptions.ts",
        "fields": ["orderId", "status"],
    },
]


def build_demo_db(db_path: str) -> None:
    if os.path.exists(db_path):
        os.remove(db_path)
    db = DB(db_path)

    enriched = []
    for node in DEMO_NODES:
        norm = normalize(node["raw_name"], node.get("fields", []))
        node["tokens"] = norm["tokens"]
        node["field_tokens"] = norm["field_tokens"]
        db.upsert_node(node, norm["tokens"], norm["field_tokens"])
        enriched.append(node)
    db.commit()

    idf = compute_idf(enriched)
    db.upsert_token_idf(idf)
    db.commit()
    set_idf(idf)

    edges = score_all_pairs(enriched, min_score=0.12)
    for src_id, tgt_id, scores, total in edges:
        db.upsert_edge(src_id, tgt_id, scores, total)
    db.commit()

    print(
        f"[demo] Built {db_path}: "
        f"{db.node_count()} nodes, {db.edge_count()} edges"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a small Ariadne demo DB from hand-crafted sample nodes."
    )
    parser.add_argument(
        "--db",
        default=os.path.join(_DIR, "ariadne.db"),
        help="Output DB path (default: ariadne.db next to this script)",
    )
    args = parser.parse_args()
    build_demo_db(args.db)


if __name__ == "__main__":
    main()
