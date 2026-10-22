from pathlib import Path

from ariadne_mcp.scanner.kafka_scanner import scan_kafka


def test_scans_nested_yaml_alias_and_flow_mapping_and_resolves_listener_property(
    tmp_path: Path,
):
    (tmp_path / "application.yaml").write_text(
        """\
kafka:
  topic: &topic_names
    order:
      created: commerce.order.created
    payment: {captured: commerce.payment.captured}
  topic_alias: *topic_names
""",
        encoding="utf-8",
    )
    source = tmp_path / "src/main/java/OrderListener.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        '''
@KafkaListener(topics = "${kafka.topic.created}")
public void consumeOrder() {}
''',
        encoding="utf-8",
    )

    nodes = scan_kafka(str(tmp_path), "orders")
    assert {node["raw_name"] for node in nodes} == {
        "commerce.order.created",
        "commerce.payment.captured",
    }
