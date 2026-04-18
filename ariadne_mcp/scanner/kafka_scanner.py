"""
Scans Kafka topics from:
  1. application.yaml  kafka.topic.* values
  2. @KafkaListener(topics = "...") in Java/Kotlin source
  3. kafkaProducer.send / kafkaTemplate.send / KafkaProducer.send calls
"""
import re
from pathlib import Path

from ariadne_mcp.scanner import BaseScanner

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class KafkaScanner(BaseScanner):
    """Scan Kafka topics from YAML config and Java/Kotlin source."""

    def scan(self, repo_path: str, service: str) -> list[dict]:
        return scan_kafka(repo_path, service)


def scan_kafka(repo_path: str, service: str) -> list[dict]:
    nodes = []
    repo = Path(repo_path)

    # 1. YAML config: kafka.topic.*
    for yaml_file in repo.rglob("application*.yaml"):
        try:
            text = yaml_file.read_text(encoding="utf-8")
            if HAS_YAML:
                data = yaml.safe_load(text)
                kafka_topics = _extract_yaml_topics(data)
            else:
                kafka_topics = _regex_yaml_topics(text)
        except Exception:
            continue
        for prop_name, topic_name in kafka_topics.items():
            nodes.append(_make_topic_node(service, topic_name, str(yaml_file), [prop_name], "config"))

    # 2. Java/Kotlin source: @KafkaListener and producer.send
    prop_map = _build_prop_map(repo)  # ${kafka.topic.xxx} → actual topic name

    for src in list(repo.rglob("*.java")) + list(repo.rglob("*.kt")):
        if "src/test" in str(src):
            continue
        try:
            text = src.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        # @KafkaListener(topics = "..." or ["..."] or ["\${...}"])  — Java + Kotlin
        for m in re.finditer(
            r'@KafkaListener\s*\([^)]*?topics\s*=\s*(?:\[[^\]]*\]|\{[^}]*\}|"[^"]*"|\$\{[^}]+\})',
            text
        ):
            topic_val = _resolve_topic_val(m.group(0), prop_map)
            for tv in topic_val:
                nodes.append(_make_topic_node(service, tv, str(src), ["consumer"], "consume"))

        # kafkaProducer.send(topic, ...) — topic is either string literal or field ref
        # Pattern 1: kafkaProducer.send(kafkaTopicProperties.getXxx(), ...)
        for m in re.finditer(
            r'kafkaProducer\.send\s*\(\s*kafkaTopicProperties\.get(\w+)\s*\(\)',
            text
        ):
            prop_name = m.group(1)  # e.g. "OrderCreated"
            # Convert getter name to camelCase prop key
            prop_key = prop_name[0].lower() + prop_name[1:]
            topic_name = prop_map.get(prop_key) or _camel_to_kebab(prop_key)
            nodes.append(_make_topic_node(service, topic_name, str(src), [prop_key], "produce"))

        # Pattern 2: kafkaTemplate.send("topic-name", ...)
        for m in re.finditer(
            r'(?:kafkaTemplate|kafkaProducer)\.send\s*\(\s*"([^"]+)"',
            text
        ):
            nodes.append(_make_topic_node(service, m.group(1), str(src), [], "produce"))

    return _dedup_by_id(nodes)


def _make_topic_node(service: str, topic_name: str, source_file: str,
                     fields: list[str], role: str) -> dict:
    return {
        "id": f"{service}::kafka::{role}::{topic_name}",
        "type": "kafka_topic",
        "raw_name": topic_name,
        "service": service,
        "source_file": source_file,
        "fields": fields + [role],
        "method": role,
        "path": None,
    }


def _build_prop_map(repo: Path) -> dict:
    """Build map: camelCase prop name → actual topic string from yaml."""
    prop_map = {}
    for yaml_file in repo.rglob("application*.yaml"):
        try:
            text = yaml_file.read_text(encoding="utf-8")
            if HAS_YAML:
                data = yaml.safe_load(text)
                prop_map.update(_extract_yaml_topics(data))
            else:
                prop_map.update(_regex_yaml_topics(text))
        except Exception:
            continue
    return prop_map


def _resolve_topic_val(annotation_text: str, prop_map: dict) -> list[str]:
    """Resolve topic values from annotation, handling ${...} refs and [...] array syntax."""
    results = []
    # Property refs: ${kafka.topic.propName}  (check first, before string literals)
    for prop_ref in re.findall(r'\$\{([^}]+)\}', annotation_text):
        prop_key = prop_ref.split('.')[-1]
        resolved = prop_map.get(prop_key)
        if resolved:
            results.append(resolved)
        else:
            results.append(prop_key)
    # Plain string literals (not containing ${})
    for lit in re.findall(r'"([^"$][^"]*)"', annotation_text):
        if lit and not lit.startswith("$") and "${" not in lit:
            results.append(lit)
    return results or ["unknown"]


def _camel_to_kebab(name: str) -> str:
    import re as _re
    s = _re.sub(r'([A-Z])', r'-\1', name).lower().lstrip('-')
    return s


def _extract_yaml_topics(data, prefix="") -> dict:
    results = {}
    if not isinstance(data, dict):
        return results
    for k, v in data.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            results.update(_extract_yaml_topics(v, full_key))
        elif isinstance(v, str) and "kafka.topic" in full_key:
            results[full_key.split(".")[-1]] = v
    return results


def _regex_yaml_topics(text: str) -> dict:
    results = {}
    in_topic_section = False
    for line in text.splitlines():
        if re.match(r'\s+topic:', line):
            in_topic_section = True
        elif re.match(r'\S', line) and in_topic_section:
            in_topic_section = False
        if in_topic_section:
            m = re.match(r'\s+(\w+):\s+(\S+)', line)
            if m:
                results[m.group(1)] = m.group(2)
    return results


def _dedup_by_id(nodes: list[dict]) -> list[dict]:
    """
    Dedup by (service, raw_name): same topic scanned from config + producer
    should be one node. Prefer config > produce > consume ordering.
    """
    role_prio = {"config": 0, "produce": 1, "consume": 2}
    best: dict[tuple, dict] = {}
    for n in nodes:
        key = (n["service"], n["raw_name"])
        cur_role = n.get("method", "consume")
        cur_prio = role_prio.get(cur_role, 9)
        if key not in best:
            best[key] = n
        else:
            prev_role = best[key].get("method", "consume")
            prev_prio = role_prio.get(prev_role, 9)
            if cur_prio < prev_prio:
                best[key] = n
    return list(best.values())
