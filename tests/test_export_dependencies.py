def test_required_renderers_import():
    import docx
    import pypdf
    import pypdfium2
    import reportlab
    assert reportlab and docx and pypdf and pypdfium2
