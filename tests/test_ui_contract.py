"""UI contracts; no backend, hardware, model, or credentials are required."""
from html.parser import HTMLParser
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "sozvon" / "web"


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


def render_page():
    env = Environment(
        loader=FileSystemLoader(WEB / "templates"), autoescape=select_autoescape()
    )
    return env.get_template("index.html").render(version="test-version")


def test_application_shell_is_local_accessible_and_has_real_workflow_controls():
    template = WEB / "templates" / "index.html"
    assert template.is_file(), "The application template must exist"
    html = render_page()
    page = Page(html)
    ids = [attrs["id"] for _, attrs in page.elements if "id" in attrs]
    assert len(ids) == len(set(ids)), "DOM ids must be unique"
    for expected in (
        "library-view", "settings-view", "about-view", "meeting-view", "new-dialog",
        "new-text-form", "new-audio-form", "record-form", "settings-form", "notes-form",
        "global-error", "job-banner", "meeting-list", "new-recording",
    ):
        assert expected in ids, f"Missing workflow: {expected}"
    assert "test-version" in html
    assert "Техническая версия" in html
    for tag, attrs in page.elements:
        if tag == "script" or (tag == "link" and attrs.get("rel") == "stylesheet"):
            url = attrs.get("src") or attrs.get("href")
            assert url and url.startswith("/static/"), "Assets must be local"
        if tag == "button":
            assert attrs.get("type") in {"button", "submit"}, "Explicit button behavior"
    for tab in ("report", "transcript", "notes", "sources"):
        assert any(attrs.get("data-detail-tab") == tab for _, attrs in page.elements)
    assert any(tag == "dialog" for tag, _ in page.elements)


def test_inputs_follow_server_limits_and_supported_audio_types():
    page = Page(render_page())
    elements = {attrs.get("id"): attrs for _, attrs in page.elements if attrs.get("id")}
    assert elements["new-title"].get("maxlength") == "200"
    assert elements["new-text"].get("maxlength") == "500000"
    assert elements["meeting-notes"].get("maxlength") == "100000"
    assert elements["audio-file"]["accept"] == ".wav,.mp3,.m4a,.flac,.ogg"


def test_client_assets_use_safe_dom_and_no_persistent_session_storage():
    script = WEB / "static" / "js" / "app.js"
    style = WEB / "static" / "app.css"
    assert script.is_file(), "The real application client must exist"
    assert style.is_file(), "The local stylesheet must exist"
    scripts = list((WEB / "static" / "js").glob("*.js"))
    source = "\n".join(path.read_text(encoding="utf-8") for path in scripts)
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "localStorage", "sessionStorage"):
        assert forbidden not in source, f"Unsafe client primitive: {forbidden}"
    for expected in ("/api/session", "X-CSRF-Token", "same-origin", "replaceState", "detail"):
        assert expected in source
    css = style.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css
    assert "/static/fonts/GolosText-Regular.ttf" in css
    assert "focus-visible" in css


def test_editor_and_import_modules_cover_the_explicit_api_contract():
    js = WEB / "static" / "js"
    for filename in ("settings.js", "meeting.js", "import.js"):
        assert (js / filename).is_file(), f"Missing real workflow module: {filename}"
    settings = (js / "settings.js").read_text(encoding="utf-8")
    for value in ("/api/settings", "api_key", "delete_key", "allow_remote", "model_path", "/api/devices"):
        assert value in settings
    meeting = (js / "meeting.js").read_text(encoding="utf-8")
    for value in ("/transcribe", "/report", "/notes", "segment_id", "quote"):
        assert value in meeting
    export = (js / "export.js").read_text(encoding="utf-8")
    for value in ("/export?", "URLSearchParams", "include_transcript", "include_quotes", "application/pdf", "wordprocessingml.document", "application/json", "response.ok"):
        assert value in export
    imports = (js / "import.js").read_text(encoding="utf-8")
    for value in ("/api/import/text", "/api/import/audio", "/api/record/start", "max_seconds", "allow_partial"):
        assert value in imports
