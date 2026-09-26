"""Встроенный шаблон собеседования: компактный отчёт по результатам кандидата."""
import io

from docx import Document
from features_http import synthetic_api
from pypdf import PdfReader
from test_web import open_client

from sozvon.storage.repository import Repository
from sozvon.templates.builtin import builtin_ids, builtin_spec
from sozvon.templates.schema import CORE, TemplateSpec

CANDIDATE_TITLES = {
    "summary": "Итог по кандидату",
    "decisions": "Вердикт и договорённости",
    "proposals": "Рекомендации",
    "tasks": "Следующие шаги",
    "questions": "Не выяснено",
    "risks": "Пробелы и риски",
}


def test_interview_builtin_is_compact_and_candidate_oriented():
    assert "interview" in builtin_ids()
    spec = builtin_spec("interview")
    assert isinstance(spec, TemplateSpec)
    assert spec.name == "Собеседование"
    assert spec.language == "ru"
    assert spec.detail == "brief"
    assert [section.key for section in spec.sections] == list(CORE)
    assert spec.sections[3].kind == "tasks"
    assert all(section.enabled for section in spec.sections)
    assert {section.key: section.title for section in spec.sections} == CANDIDATE_TITLES
    assert all(section.instructions.strip() for section in spec.sections)
    assert all(len(section.instructions) <= 600 for section in spec.sections)
    assert "кандидат" in spec.instructions.lower()
    assert "3" in spec.instructions  # прямой предел компактности
    spec.name = "Изменено"
    assert builtin_spec("interview").name == "Собеседование"


def test_other_builtins_keep_their_titles():
    for key in ("meeting", "client", "technical"):
        spec = builtin_spec(key)
        assert spec.detail == "normal"
        assert [section.title for section in spec.sections] != list(CANDIDATE_TITLES.values())


def test_prompt_maps_detail_to_compactness():
    from sozvon.llm.provider import SYSTEM_PROMPT

    assert "brief" in SYSTEM_PROMPT and "normal" in SYSTEM_PROMPT and "detailed" in SYSTEM_PROMPT
    lowered = SYSTEM_PROMPT.lower()
    assert "пункт" in lowered


def test_existing_database_gains_the_new_builtin_without_bumping_schema(tmp_path):
    repo = Repository(tmp_path)
    with repo.connection() as conn:
        created = conn.execute("INSERT INTO templates VALUES('custom-1',0,0,'2020-01-02')").rowcount
        conn.execute("INSERT INTO template_revisions VALUES('custom-1',1,'{}','2020-01-02')")
        conn.execute("DELETE FROM template_revisions WHERE template_id='interview'")
        conn.execute("DELETE FROM templates WHERE id='interview'")
    assert created == 1

    Repository(tmp_path)
    with repo.connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM templates WHERE id='interview'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM templates WHERE builtin=0").fetchone()[0] == 1
        rows = conn.execute("SELECT revision,spec_json FROM template_revisions WHERE template_id='interview'").fetchall()
    assert len(rows) == 1 and rows[0]["revision"] == 1
    assert TemplateSpec.model_validate_json(rows[0]["spec_json"]) == builtin_spec("interview")

    # Идемпотентность: повторное открытие ничего не дублирует и не переписывает.
    Repository(tmp_path)
    with repo.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM template_revisions WHERE template_id='interview'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM templates").fetchone()[0] == 5


def test_interview_template_creates_and_exports_candidate_report(tmp_path):
    app, client, headers = open_client(tmp_path)
    service = app.state.service
    with client, synthetic_api() as (url, _observed):
        listed = client.get('/api/templates', headers=headers).json()['items']
        interview = next(item for item in listed if item['id'] == 'interview')
        assert interview['builtin'] and interview['spec']['detail'] == 'brief'
        assert {s['title'] for s in interview['spec']['sections']} == set(CANDIDATE_TITLES.values())
        settings = client.put('/api/settings', headers=headers, json={
            'llm': {'base_url': url, 'model': 'synthetic-report'}, 'template': 'interview'})
        assert settings.status_code == 200
        assert settings.json()['template'] == 'interview'
        mid = client.post('/api/import/text', headers=headers, json={
            'title': 'Собеседование на 1С-программиста',
            'text': 'Кандидат: поддержка УТ 11.4.3, внешние печатные формы. Ожидания 80 тыс.'}).json()['id']
        response = client.post(f'/api/meetings/{mid}/report', headers=headers,
                               json={'template_id': 'interview', 'template_revision': 1})
        assert response.status_code == 202, response.text
        assert service.wait(25)
        assert service.repo.job(response.json()['job_id'])['status'] == 'succeeded'
        detail = client.get(f'/api/meetings/{mid}').json()
        report = detail['reports'][0]
        assert report['meta']['template_snapshot']['id'] == 'interview'
        assert report['meta']['template_snapshot']['spec']['detail'] == 'brief'
        assert [section['title'] for section in report['sections']] == list(CANDIDATE_TITLES.values())
        assert [section['key'] for section in report['sections']] == list(CORE)
        for fmt, reader in (('docx', lambda data: '\n'.join(p.text for p in Document(io.BytesIO(data)).paragraphs)),
                            ('pdf', lambda data: '\n'.join(p.extract_text() for p in PdfReader(io.BytesIO(data)).pages))):
            export = client.get(f'/api/meetings/{mid}/export?format={fmt}&content=report')
            assert export.status_code == 200, export.text[:200]
            text = reader(export.content)
            for title in CANDIDATE_TITLES.values():
                assert title in text, (fmt, title)
            assert 'Кратко' not in text and 'Решения' not in text
    _again, next_client, _ = open_client(tmp_path)
    with next_client:
        assert next_client.get(f'/api/meetings/{mid}').json()['reports'][0]['meta']['template_snapshot']['id'] == 'interview'
