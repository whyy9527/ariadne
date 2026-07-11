from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_usage_feedback_form_is_closed_ended_and_private():
    form_path = ROOT / ".github" / "ISSUE_TEMPLATE" / "usage-feedback.yml"
    form = form_path.read_text(encoding="utf-8")

    assert "  - feedback" in form
    for field_id in ("outcome", "stack", "stage"):
        field_start = form.index(f"    id: {field_id}")
        field_end = form.find("\n  - type:", field_start)
        block = form[field_start:field_end if field_end != -1 else None]
        assert "type: dropdown" in form[max(0, field_start - 30):field_start]
        assert "required: true" in block
        assert block.count("        - ") >= 4
    assert "id: note" in form
    assert "required: false" in form
    assert "Do not paste private source code" in form
    assert "submitted only when you choose" in form


def test_readme_links_feedback_and_discloses_zero_telemetry():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.split())
    assert "issues/new?template=usage-feedback.yml" in readme
    assert "sends no usage data automatically" in normalized
    assert "No source, query, or usage data is transmitted" in normalized
