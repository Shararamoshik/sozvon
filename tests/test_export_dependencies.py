def test_required_renderers_import():
    import reportlab
    import docx
    import pypdf
    import pypdfium2
    assert reportlab and docx and pypdf and pypdfium2
