"""Общие конфликты редакторов и ресурсов для HTTP 409."""


class RevisionConflict(ValueError):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__("Данные изменились в другой вкладке. Ваш черновик не сохранён.")


class ResourceConflict(ValueError):
    """Ресурс сейчас нельзя изменить либо использовать для выбранной операции."""
