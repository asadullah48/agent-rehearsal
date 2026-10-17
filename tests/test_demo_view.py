"""Check actual generated HTML against persisted evidence, without a browser dependency."""

import json
from dataclasses import dataclass, field
from html import escape
from html.parser import HTMLParser

import pytest

from agent_rehearsal import demo
from agent_rehearsal.scenarios import load_scenario, parse_scenario


@dataclass
class Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    @property
    def text(self):
        return "".join(c if isinstance(c, str) else c.text for c in self.children)

    def find(self, tag=None, **attrs):
        matches = []
        for child in self.children:
            if isinstance(child, Element):
                if (tag is None or child.tag == tag) and all(
                    child.attrs.get(k) == v for k, v in attrs.items()
                ):
                    matches.append(child)
                matches.extend(child.find(tag, **attrs))
        return matches


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Element("document")
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Element(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"meta", "link", "br", "hr", "img", "input", "source"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        assert self.stack[-1].tag == tag, f"unbalanced HTML: {tag}"
        self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)


@pytest.fixture
def viewer(tmp_path):
    output = tmp_path / "demo"
    demo.generate_demo(output)
    reports = {
        run.name: json.loads((run / "report.json").read_text(encoding="utf-8"))
        for run in (output / "runs").iterdir()
    }
    pages = {
        path.stem: Page(path.read_text(encoding="utf-8")).root for path in output.glob("*.html")
    }
    return output, reports, pages


def field_text(node, name):
    fields = node.find("dd", **{"data-field": name})
    assert len(fields) == 1
    return fields[0].text


def test_demo_rendered_values_match_each_report(viewer):
    _, reports, pages = viewer
    for slug, report in reports.items():
        (card,) = pages["index"].find("article", **{"data-case": slug})
        case = pages[slug]
        scenario = report["metadata"]["scenario"]
        expected = {
            "Scenario": f"{scenario['catalog_id']} / {scenario['id']}",
            "Controller": demo.SCRIPTED_LABEL
            if report["adapter"]["kind"] == "scripted"
            else report["adapter"]["label"],
            "Execution status": report["execution_status"],
            "Business outcome": report["business_outcome"],
            "Evaluation verdict": report["verdict"].upper(),
        }
        for name, value in expected.items():
            assert field_text(card, name) == field_text(case, name) == value
        assert field_text(case, "Recorded adapter label") == report["adapter"]["label"]
        assert field_text(case, "PO count") == str(len(report["final_state"]["purchase_orders"]))
        rejected = [
            c
            for c in report["tool_calls"]
            if c["tool"] == "create_purchase_order"
            and c["status"] == "error"
            and c["response"]["error"]["certainty"] == "not_committed"
        ]
        assert field_text(case, "Blocked attempt count") == str(len(rejected))
        if slug == "stale-approval":
            assert len(rejected) == 2
            assert case.text.count("BLOCKED:") == 2
        # Visible JSON payloads must be the original saved assertions/events/calls.
        payloads = [json.loads(pre.text) for pre in case.find("pre")]
        for record in report["assertions"] + report["events"] + report["tool_calls"]:
            assert record in payloads
        timeline_ids = [li.attrs["id"] for li in case.find("li")]
        assert timeline_ids == [f"event-{e['event_id']}" for e in report["events"]]


def test_demo_every_evidence_link_resolves_to_saved_event(viewer):
    _, reports, pages = viewer
    for slug, report in reports.items():
        page = pages[slug]
        ids = [node.attrs["id"] for node in page.find() if "id" in node.attrs]
        assert len(ids) == len(set(ids))
        links = page.find("a", **{"class": "evidence"})
        expected = [ref for a in report["assertions"] for ref in a["evidence_event_ids"]]
        assert [link.text for link in links] == expected
        event_ids = {e["event_id"] for e in report["events"]}
        for link in links:
            assert link.text in event_ids
            assert link.attrs["href"] == f"#event-{link.text}"
            (target,) = page.find("li", id=link.attrs["href"][1:])
            (detail,) = target.find("details")
            assert "open" in detail.attrs


def test_demo_escapes_unsafe_fixture_text(tmp_path, monkeypatch):
    unsafe = '<script>alert("unsafe")</script> & <img src=x onerror=alert(1)>'
    doc = load_scenario("happy-path").model_dump(mode="json")
    doc["title"] = doc["description"] = unsafe
    doc["initial_world"]["suppliers"][0]["name"] = unsafe
    fixture = parse_scenario(json.dumps(doc))
    monkeypatch.setattr(demo, "list_scenarios", lambda: [fixture])
    demo.generate_demo(tmp_path / "demo")
    for slug in ("index", "happy-path"):
        html = (tmp_path / "demo" / f"{slug}.html").read_text(encoding="utf-8")
        assert escape(unsafe) in html
        page = Page(html).root
        assert unsafe in page.text
        assert not page.find("script")
        assert not page.find("img")


def test_demo_no_external_resources(viewer):
    output, _, pages = viewer
    for slug, page in pages.items():
        for node in page.find():
            for attribute in ("src", "href", "action", "srcset"):
                if attribute not in node.attrs:
                    continue
                value = node.attrs[attribute]
                assert not value.startswith(("/", "http:", "https:", "file:", "data:"))
                target, _, fragment = value.partition("#")
                assert ":" not in target
                linked = output / target if target else output / f"{slug}.html"
                assert linked.exists()
                if fragment:
                    assert pages[linked.stem].find(id=fragment)
        assert not any(page.find(tag) for tag in ("script", "iframe", "object", "embed", "link"))
        for style in page.find("style"):
            assert "url(" not in style.text and "@import" not in style.text


def test_demo_false_success_fail_badge_differs_from_expected_refusal(viewer):
    _, reports, pages = viewer
    assert reports["false-success"]["verdict"] == "fail"
    for page in (pages["false-success"], pages["index"]):
        assert page.find("span", **{"class": "badge fail"})
    assert field_text(pages["false-success"], "Evaluation verdict") == "FAIL"
    assert "Evaluation FAIL:" in pages["false-success"].text
    for slug in ("approval-revoked", "supplier-changed", "stale-approval"):
        assert field_text(pages[slug], "Evaluation verdict") == "PASS"
        assert "Expected refusal - evaluation PASS" in pages[slug].text
    for heading in ("Controller handles disruptions", "Evaluator catches a bad agent"):
        assert heading in [h.text for h in pages["index"].find("h2")]
    # Assertion-level NOT_APPLICABLE remains explicit, never silently shown as PASS.
    assert any(s.text == "NOT_APPLICABLE" for p in pages.values() for s in p.find("span"))


def test_demo_all_pages_have_accessible_structure(viewer):
    output, _, pages = viewer
    for slug, page in pages.items():
        assert page.find("html", lang="en")
        (title,) = page.find("title")
        (heading,) = page.find("h1")
        assert title.text == heading.text and title.text.strip()
        assert page.find("h2") and page.find("main", id="main")
        assert page.find("a", href="#main")
        html = (output / f"{slug}.html").read_text(encoding="utf-8")
        assert ":focus-visible" in html and "outline:3px solid" in html
        assert 'name="viewport"' in html and "overflow-wrap:anywhere" in html
