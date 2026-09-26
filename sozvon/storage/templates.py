"""Шаблоны и их неизменяемые спецификации в SQLite."""
import json
from uuid import uuid4

from sozvon.core.errors import ResourceConflict, RevisionConflict
from sozvon.storage.repository import now
from sozvon.templates.schema import TemplateSpec


def _load(conn, template_id, revision=None):
    head = conn.execute("SELECT * FROM templates WHERE id=?", (template_id,)).fetchone()
    if head is None:
        raise KeyError(template_id)
    if revision is None:
        row = conn.execute("SELECT * FROM template_revisions WHERE template_id=? "
                           "ORDER BY revision DESC LIMIT 1", (template_id,)).fetchone()
    else:
        row = conn.execute("SELECT * FROM template_revisions WHERE template_id=? AND revision=?",
                           (template_id, revision)).fetchone()
    if row is None:
        raise KeyError(template_id)
    return {"id": template_id, "builtin": bool(head["builtin"]),
            "archived": bool(head["archived"]), "revision": row["revision"],
            "spec": json.loads(row["spec_json"])}


def get_template(repo, template_id, revision=None):
    with repo.connection() as conn:
        conn.execute("BEGIN")
        return _load(conn, template_id, revision)


def list_templates(repo):
    with repo.connection() as conn:
        conn.execute("BEGIN")
        ids = [row[0] for row in conn.execute(
            "SELECT id FROM templates WHERE archived=0 ORDER BY builtin DESC,created_at,id")]
        return [_load(conn, value) for value in ids]


def create_template(repo, spec: TemplateSpec):
    template_id = uuid4().hex
    with repo.connection() as conn:
        conn.execute("INSERT INTO templates VALUES(?,0,0,?)", (template_id, now()))
        conn.execute("INSERT INTO template_revisions VALUES(?,1,?,?)",
                     (template_id, spec.model_dump_json(), now()))
        return _load(conn, template_id)


def save_template(repo, template_id, base_revision, spec: TemplateSpec):
    with repo.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = _load(conn, template_id)
        if old["builtin"] or old["archived"]:
            raise ResourceConflict("Создайте копию встроенного или архивного шаблона")
        if old["revision"] != base_revision:
            raise RevisionConflict(old["revision"])
        old_sections = {item["key"]: item["kind"] for item in old["spec"]["sections"]}
        if any(item.key in old_sections and item.kind != old_sections[item.key]
               for item in spec.sections):
            raise ValueError("Тип существующего раздела менять нельзя: создайте новый")
        if old["spec"] == spec.model_dump():
            return old
        conn.execute("INSERT INTO template_revisions VALUES(?,?,?,?)",
                     (template_id, base_revision + 1, spec.model_dump_json(), now()))
        return _load(conn, template_id)


def archive_template(repo, template_id, base_revision, default_id):
    with repo.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = _load(conn, template_id)
        if old["builtin"] or template_id == default_id:
            raise ResourceConflict("Встроенный или выбранный шаблон нельзя архивировать")
        if old["revision"] != base_revision:
            raise RevisionConflict(old["revision"])
        conn.execute("UPDATE templates SET archived=1 WHERE id=?", (template_id,))
