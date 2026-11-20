"""Excel okuma yardımcıları: boyut sınırı, başlık satırı bulma, satırları hücre adresiyle okuma."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.exceptions import InvalidFileException

from domain.cells import norm

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
HEADER_SEARCH_ROWS = 15
FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


class SheetError(ValueError):
    """Dosya açılamadı ya da yapısı beklenenle uyuşmuyor."""


@dataclass
class Issue:
    level: str  # "error" | "warning" | "info"
    where: str  # "Bölümler!B5" ya da "Bölümler satır 5"
    message: str

    def as_dict(self) -> dict:
        return {"level": self.level, "where": self.where, "message": self.message}


@dataclass
class Issues:
    items: list[Issue] = field(default_factory=list)

    def error(self, where: str, message: str) -> None:
        self.items.append(Issue("error", where, message))

    def warning(self, where: str, message: str) -> None:
        self.items.append(Issue("warning", where, message))

    def info(self, where: str, message: str) -> None:
        self.items.append(Issue("info", where, message))

    @property
    def has_errors(self) -> bool:
        return any(i.level == "error" for i in self.items)

    def as_list(self) -> list[dict]:
        return [i.as_dict() for i in self.items]


def open_workbook(data: bytes) -> Workbook:
    if len(data) > MAX_BYTES:
        raise SheetError("Dosya 5 MB'tan büyük olamaz.")
    try:
        return load_workbook(BytesIO(data), data_only=True, read_only=True)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, InvalidFileException) as exc:
        raise SheetError(f"Excel dosyası açılamadı ({type(exc).__name__}). Dosya .xlsx biçiminde olmalı.") from None


def label(value: object) -> str:
    """Hücre değerini tek satırlık metne çevirir (5.0 → '5', tarih → '2026-09')."""
    if value is None:
        return ""
    if isinstance(value, datetime | date):
        return f"{value.year}-{value.month:02d}"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).split())


@dataclass
class Row:
    number: int  # Excel satır numarası
    values: dict[str, object]  # sütun anahtarı → ham değer
    coords: dict[str, str]  # sütun anahtarı → "B5"

    def text(self, key: str) -> str:
        return label(self.values.get(key))


@dataclass
class Table:
    sheet: str
    header_row: int
    columns: dict[str, int]  # anahtar → 0 tabanlı sütun
    rows: list[Row]
    headers: list[object] = field(default_factory=list)


def read_table(ws, columns: dict[str, tuple[str, ...]], required: set[str]) -> Table:
    """Başlıkları `columns` (anahtar → kabul edilen başlık adları) ile eşleştirip satırları okur."""
    grid = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i > MAX_ROWS + HEADER_SEARCH_ROWS:
            raise SheetError(f"'{ws.title}' sayfasında en fazla {MAX_ROWS} satır olabilir.")
        grid.append(list(row))
    wanted = {key: {norm(n) for n in names} for key, names in columns.items()}
    for header_no in range(1, min(len(grid), HEADER_SEARCH_ROWS) + 1):
        header = [norm(label(v)) for v in grid[header_no - 1]]
        found = {}
        for key, names in wanted.items():
            for idx, text in enumerate(header):
                if text in names:
                    found[key] = idx
                    break
        if required <= set(found):
            break
    else:
        need = ", ".join(columns[k][0] for k in sorted(required))
        raise SheetError(f"'{ws.title}' sayfasında başlık satırı bulunamadı (gerekli sütunlar: {need}).")
    rows = []
    for number in range(header_no + 1, len(grid) + 1):
        values = grid[number - 1]
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in values):
            continue
        rows.append(
            Row(
                number,
                {k: values[i] if i < len(values) else None for k, i in found.items()},
                {k: f"{get_column_letter(i + 1)}{number}" for k, i in found.items()},
            )
        )
    return Table(ws.title, header_no, found, rows, grid[header_no - 1])


def find_sheet(wb: Workbook, names: tuple[str, ...]):
    wanted = {norm(n) for n in names}
    for ws in wb.worksheets:
        if norm(ws.title) in wanted:
            return ws
    return None


def safe_text(value: object) -> object:
    """Dışa aktarılan metin hücresi formül gibi başlıyorsa önüne ' koyar (formül enjeksiyonu)."""
    if isinstance(value, str) and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value
