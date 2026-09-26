"""Копировать только свой проект в независимую Windows-папку, без удаления чужих файлов."""
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
TARGET = Path("/mnt/d/sozvon")


def main():
    if ROOT.name != "sozvon" or TARGET.name != "sozvon":
        raise RuntimeError("Неверная папка проекта")
    TARGET.mkdir(parents=True, exist_ok=True)
    for name in ("pyproject.toml", "uv.lock", "README.md"):
        source = ROOT / name
        if source.is_file():
            shutil.copy2(source, TARGET / name)
    for name in ("sozvon", "tests", "packaging", "scripts", "docs"):
        source = ROOT / name
        if source.exists():
            shutil.copytree(source, TARGET / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    models = ROOT / "artifacts" / "models" / "tiny"
    if models.is_dir():
        shutil.copytree(models, TARGET / "artifacts" / "models" / "tiny", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".cache"))
    print(f"Скопировано: {TARGET}; окружение и данные не удалялись")


if __name__ == "__main__":
    main()
