"""Make labelled form rows readable to a screen reader.

Qt does not connect a ``QFormLayout`` label to its field on its own, so a
screen reader announces the field's type but not its purpose. Pairing the
two with ``setBuddy`` also gives the label's mnemonic (if any) a target,
and an accessible name covers the cases where the platform ignores the
buddy relation.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QLabel


def pair_form_labels(form: QFormLayout) -> None:
    """Give every labelled field a buddy and an accessible name.

    Rows without a label (a checkbox, a hint, a row that spans both
    columns) are left alone: there is no label text to borrow.
    """
    for row in range(form.rowCount()):
        label_item = form.itemAt(row, QFormLayout.LabelRole)
        field_item = form.itemAt(row, QFormLayout.FieldRole)
        if label_item is None or field_item is None:
            continue
        label = label_item.widget()
        field = field_item.widget()
        if not isinstance(label, QLabel) or field is None:
            continue
        # A label field (a preview line) has nothing to focus or name.
        if isinstance(field, QLabel):
            continue
        text = label.text().strip().rstrip(":")
        if not text:
            continue
        label.setBuddy(field)
        if not field.accessibleName():
            field.setAccessibleName(text)
