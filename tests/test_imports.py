"""Faz 2: Excel'den kurulum, aylık çizelgeden açılış bakiyesi, açılış kayıtları. Tüm veriler sentetik."""

from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from hypothesis import given
from hypothesis import strategies as st
from openpyxl import Workbook, load_workbook

from apps.billing import opening
from apps.billing import services as billing
from apps.billing.models import Charge, ChargeBatch, DuesAgreement
from apps.core.context import tenant_context
from apps.imports import grid, setup
from apps.imports.models import ImportJob
from apps.imports.xlsx import SheetError, safe_text
from apps.ledger import services as ledger
from apps.ledger.models import Account
from apps.payments import services as payments
from apps.payments.models import Payment
from apps.people.models import Occupancy, Person
from apps.property.models import Block, Unit, UnitType
from apps.reports.services import unit_balances
from apps.tenancy.models import Role
from domain.cells import AMOUNT, NOTE, owed_by_month, parse_cell, parse_month, to_kurus
from domain.phone import is_valid_phone, mask_phone, normalize_phone

from .conftest import add_member, make_user, panel_client
from .test_accounting import check_consistency

OPEN_DAY = date(2026, 10, 31)


def xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


UNIT_HEAD = [
    "Blok",
    "Bölüm No",
    "Kat",
    "Tür",
    "Bölüm tipi",
    "Tip aidatı",
    "Brüt m²",
    "Arsa payı",
    "Arsa payı paydası",
    "Malik adı",
    "Malik soyadı",
    "Malik telefonu",
    "Malik e-postası",
    "Kiracı adı",
    "Kiracı soyadı",
    "Kiracı telefonu",
    "Kiracı e-postası",
    "Borç sorumlusu",
]


def unit_row(block, no, typ="2+1", fee="1.250,00", owner=("Deniz", "Örnek", "0555 000 00 01"), tenant=None, **kw):
    row = {
        "Blok": block,
        "Bölüm No": no,
        "Kat": 1,
        "Tür": "Daire",
        "Bölüm tipi": typ,
        "Tip aidatı": fee,
        "Brüt m²": 95,
        "Arsa payı": 50,
        "Arsa payı paydası": 1000,
    }
    if owner:
        row.update({"Malik adı": owner[0], "Malik soyadı": owner[1], "Malik telefonu": owner[2]})
    if tenant:
        row.update({"Kiracı adı": tenant[0], "Kiracı soyadı": tenant[1], "Kiracı telefonu": tenant[2]})
    row.update(kw)
    return [row.get(h) for h in UNIT_HEAD]


def setup_file(units=None, openings=None) -> bytes:
    sheets = {}
    if units is not None:
        sheets["Bölümler"] = [UNIT_HEAD, *units]
    if openings is not None:
        sheets["Açılış bakiyeleri"] = [["Blok", "Bölüm No", "Dönem", "Tutar", "Açıklama"], *openings]
    return xlsx(sheets)


OPTIONS = {"opening_date": OPEN_DAY.isoformat(), "start_date": "2026-01-01", "lock": False}


@pytest.fixture
def ctx(org_a):
    with tenant_context(org_a.pk):
        yield


@pytest.fixture
def site(site_a, ctx):
    billing.setup_site(site_a)
    return site_a


# --- saf fonksiyonlar -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "phone"),
    [
        ("0555 123 45 67", "05551234567"),
        ("+90 (555) 123-45-67", "05551234567"),
        ("905551234567", "05551234567"),
        ("5551234567", "05551234567"),
        (5551234567.0, "05551234567"),
        ("0090 555 123 45 67", "05551234567"),
        ("0216 123 45 67", "02161234567"),
    ],
)
def test_normalize_phone(raw, phone):
    assert normalize_phone(raw) == phone
    assert is_valid_phone(phone)


@pytest.mark.parametrize("raw", ["123", "0555 123 45", "01234567890", "", None])
def test_invalid_phone(raw):
    assert not is_valid_phone(normalize_phone(raw))


def test_mask_phone():
    assert mask_phone("05551234567") == "0555 *** ** 67"
    assert mask_phone("x") == "—"


@pytest.mark.parametrize(
    ("value", "kind", "kurus"),
    [
        (1250, AMOUNT, 125000),
        (1250.5, AMOUNT, 125050),
        ("1.250,00", AMOUNT, 125000),
        ("1250 TL", AMOUNT, 125000),
        ("350+230", AMOUNT, 58000),
        ("iade 100", AMOUNT, -10000),
        ("ödedi", NOTE, None),
        ("15.03.2026", NOTE, None),
    ],
)
def test_parse_cell(value, kind, kurus):
    cell = parse_cell(value)
    assert cell.kind == kind
    if kurus is not None:
        assert to_kurus(cell.value) == kurus


def test_to_kurus_rejects_fractions_of_kurus():
    assert to_kurus(Decimal("12.345")) is None


@pytest.mark.parametrize(
    ("value", "month"),
    [
        ("2026-09", date(2026, 9, 1)),
        ("09.2026", date(2026, 9, 1)),
        ("Eylül 2026", date(2026, 9, 1)),
        (date(2026, 9, 15), date(2026, 9, 1)),
        ("15.09.2026", date(2026, 9, 1)),
        ("Eylül", None),
        ("", None),
    ],
)
def test_parse_month(value, month):
    assert parse_month(value) == month


def test_owed_by_month_oldest_first():
    assert owed_by_month([1000, 1000, 1000], 1500) == ([0, 500, 1000], 0)
    assert owed_by_month([1000, 1000], 2500) == ([0, 0], 500)
    assert owed_by_month([1000, 1000], -200) == ([1200, 1000], 0)


@given(st.lists(st.integers(0, 10**7), min_size=1, max_size=24), st.integers(-(10**6), 3 * 10**8))
def test_owed_by_month_preserves_balance(fees, paid):
    remaining, advance = owed_by_month(fees, paid)
    assert len(remaining) == len(fees)
    assert all(r >= 0 for r in remaining) and advance >= 0
    assert sum(remaining) - advance == sum(fees) - paid
    assert not (advance and any(remaining))  # avans varsa açık ay kalmaz


def test_safe_text_neutralises_formulas():
    assert safe_text("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert safe_text("Ali") == "Ali"


# --- kurulum şablonu --------------------------------------------------------------------------


def test_template_has_expected_sheets():
    wb = load_workbook(BytesIO(setup.build_template()))
    assert wb.sheetnames == ["Bölümler", "Açılış bakiyeleri", "Açıklamalar"]
    assert [c.value for c in wb["Bölümler"][1]] == UNIT_HEAD


def test_template_round_trip_parses_without_errors():
    payload, issues = setup.parse(setup.build_template())
    assert not issues.has_errors, issues.as_list()
    assert len(payload["units"]) == 3 and len(payload["openings"]) == 2


@pytest.mark.django_db
def test_setup_creates_structure_people_and_openings(site):
    data = setup_file(
        [
            unit_row("A Blok", "1"),
            unit_row(
                "A Blok",
                "2",
                typ="3+1",
                fee="1.600,00",
                tenant=("Yaz", "Kiracı", "05550000003"),
                owner=("Ekin", "Deneme", "05550000002"),
            ),
            unit_row("B Blok", "1", owner=("Deniz", "Örnek", "0555 000 00 01")),  # aynı kişi iki dairenin maliki
            unit_row("", "D1", typ="", fee=None, owner=None, **{"Tür": "Dükkan"}),
        ],
        [
            ["A Blok", "1", "2026-09", "1.250,00", "Eylül"],
            ["A Blok", "1", "Ekim 2026", "1.250,00", ""],
            ["A Blok", "2", "", "-300,00", "fazla ödeme"],
        ],
    )
    payload, issues = setup.parse(data)
    assert not issues.has_errors, issues.as_list()
    p = setup.plan(site, payload, OPTIONS)
    assert not p.issues.has_errors, p.issues.as_list()
    assert p.summary["yeni_bolum"] == 4 and p.summary["yeni_blok"] == 2 and p.summary["yeni_tip"] == 2
    assert p.summary["yeni_kisi"] == 3 and p.summary["iliski"] == 4

    setup.apply(site, payload, OPTIONS, None)
    assert Block.objects.filter(site=site).count() == 2
    assert UnitType.objects.get(site=site, name="3+1").default_amount == 160000
    assert Unit.objects.filter(site=site).count() == 4
    shop = Unit.objects.get(site=site, number="D1")
    assert shop.kind == "shop" and shop.block is None
    deniz = Person.objects.get(first_name="Deniz")
    assert deniz.phone == "05550000001" and deniz.occupancies.count() == 2
    a2 = Unit.objects.get(site=site, block__name="A Blok", number="2")
    liable = Occupancy.objects.get(unit=a2, is_liable=True)
    assert liable.relation == "tenant"

    a1 = Unit.objects.get(site=site, block__name="A Blok", number="1")
    charges = Charge.objects.filter(unit=a1).order_by("period")
    assert [(c.period, c.amount) for c in charges] == [(date(2026, 9, 1), 125000), (date(2026, 10, 1), 125000)]
    assert charges[0].batch.method == ChargeBatch.Method.OPENING
    assert payments.advance_balance(a2) == 30000
    balances = unit_balances(site)
    assert balances[a1.pk] == 250000 and balances[a2.pk] == -30000
    assert ledger.balance(ledger.system_account(site, Account.Role.OPENING)) == -(250000 - 30000)
    check_consistency(site)


@pytest.mark.django_db
def test_setup_reports_row_errors(site):
    data = setup_file(
        [
            unit_row("A Blok", "1", owner=("Deniz", "", "123")),
            unit_row("A Blok", "1"),
            unit_row("A Blok", "2", **{"Arsa payı": 900, "Arsa payı paydası": 100}),
            unit_row("A Blok", "3", **{"Tür": "Saray"}),
            unit_row("A Blok", "4", owner=None, **{"Borç sorumlusu": "Kiracı"}),
        ],
        [["A Blok", "9", "2026-09", "100", ""], ["A Blok", "1", "", "100", ""]],
    )
    payload, issues = setup.parse(data)
    messages = " | ".join(i["message"] for i in issues.as_list())
    assert "adı ve soyadı birlikte" in messages
    assert "iki kez" in messages
    assert "paydadan büyük" in messages
    assert "Tür tanınmadı" in messages
    assert "kiracı bilgisi yok" in messages
    assert "dönemi okunamadı" in messages
    p = setup.plan(site, payload, OPTIONS)
    assert any("ne sistemde ne de" in i.message for i in p.issues.items)
    with pytest.raises(setup.ApplyError):
        setup.apply(site, payload, OPTIONS, None)
    assert not Unit.objects.filter(site=site).exists()


@pytest.mark.django_db
def test_setup_skips_existing_units_but_applies_openings(site):
    block = Block.objects.create(site=site, name="A Blok")
    unit = Unit.objects.create(site=site, block=block, number="1")
    payload, _ = setup.parse(setup_file([unit_row("a blok", "1")], [["A Blok", "1", "2026-10", "500", ""]]))
    p = setup.plan(site, payload, OPTIONS)
    assert p.summary["yeni_bolum"] == 0 and p.summary["mevcut_bolum"] == 1
    assert any("zaten kayıtlı" in i.message for i in p.issues.items)
    setup.apply(site, payload, OPTIONS, None)
    assert not unit.occupancies.exists()
    assert unit_balances(site)[unit.pk] == 50000


@pytest.mark.django_db
def test_openings_need_open_period_and_date(site):
    payload, _ = setup.parse(setup_file([unit_row("A", "1")], [["A", "1", "2026-10", "500", ""]]))
    assert any(
        "açılış tarihini" in i.message
        for i in setup.plan(site, payload, {**OPTIONS, "opening_date": None}).issues.items
    )
    ledger.lock_period(site, date(2026, 12, 31), None)
    assert any("kilitli" in i.message for i in setup.plan(site, payload, OPTIONS).issues.items)


@pytest.mark.django_db
def test_setup_can_lock_period_at_opening_date(site):
    payload, _ = setup.parse(setup_file([unit_row("A", "1")], [["A", "1", "2026-10", "500", ""]]))
    setup.apply(site, payload, {**OPTIONS, "lock": True}, None)
    assert ledger.locked_until(site) == OPEN_DAY


@pytest.mark.django_db
def test_not_an_excel_file():
    with pytest.raises(SheetError, match="açılamadı"):
        setup.parse(b"bu bir excel degil")
    with pytest.raises(SheetError, match="sayfası yok"):
        setup.parse(xlsx({"Sayfa1": [["a"]]}))


# --- açılış bakiyeleri ------------------------------------------------------------------------


@pytest.fixture
def two_units(site):
    block = Block.objects.create(site=site, name="A Blok")
    return [Unit.objects.create(site=site, block=block, number=str(n)) for n in (1, 2)]


@pytest.mark.django_db
def test_opening_debt_is_paid_oldest_first_and_advance_applies_to_new_charges(site, two_units):
    u1, u2 = two_units
    opening.post_opening(
        site,
        day=OPEN_DAY,
        debts=[opening.OpeningDebt(u1, date(2026, 9, 1), 100000), opening.OpeningDebt(u1, date(2026, 10, 1), 100000)],
        advances=[opening.OpeningAdvance(u2, 40000)],
        user=None,
    )
    pay = payments.create_payment(
        site,
        u1,
        amount=150000,
        day=date(2026, 11, 3),
        channel="cash",
        account=Account.objects.get(site=site, code="100"),
        user=None,
    )
    remaining = {c.period: c.remaining for c in billing.open_charges(u1)}
    assert remaining == {date(2026, 10, 1): 50000}
    assert pay.receipt_number == 1  # açılış avansı makbuz numarası tüketmez

    batch = ChargeBatch.objects.create(
        site=site,
        charge_type=billing.ChargeType.objects.get(site=site, name="Aidat"),
        period=date(2026, 11, 1),
        due_date=date(2026, 11, 10),
        method="per_unit",
        amount=100000,
        description="Kasım",
    )
    billing.approve(batch, None)
    assert unit_balances(site)[u2.pk] == 60000  # 1.000 aidat − 400 avans
    check_consistency(site)


@pytest.mark.django_db
def test_opening_can_be_cancelled(site, two_units):
    u1, u2 = two_units
    result = opening.post_opening(
        site,
        day=OPEN_DAY,
        debts=[opening.OpeningDebt(u1, date(2026, 10, 1), 100000)],
        advances=[opening.OpeningAdvance(u2, 40000)],
        user=None,
    )
    billing.cancel(result.batch, None, "yanlış tutar", day=date(2026, 11, 1))
    payments.cancel_payment(result.advances[0], None, "yanlış", day=date(2026, 11, 1))
    assert unit_balances(site).get(u1.pk, 0) == 0 and unit_balances(site).get(u2.pk, 0) == 0
    check_consistency(site)


@pytest.mark.django_db
def test_opening_validation(site, two_units):
    u1, _ = two_units
    with pytest.raises(billing.BillingError, match="hem açılış borcu hem"):
        opening.post_opening(
            site,
            day=OPEN_DAY,
            debts=[opening.OpeningDebt(u1, date(2026, 10, 1), 100)],
            advances=[opening.OpeningAdvance(u1, 100)],
            user=None,
        )
    with pytest.raises(billing.BillingError, match="sonra olamaz"):
        opening.post_opening(
            site, day=OPEN_DAY, debts=[opening.OpeningDebt(u1, date(2026, 12, 1), 100)], advances=[], user=None
        )
    with pytest.raises(billing.BillingError, match="yok"):
        opening.post_opening(site, day=OPEN_DAY, debts=[], advances=[], user=None)


@pytest.mark.django_db
def test_opening_is_not_counted_as_this_months_collection_or_accrual(site, two_units):
    from apps.reports.services import site_summary

    u1, u2 = two_units
    opening.post_opening(
        site,
        day=date(2026, 11, 1),
        debts=[opening.OpeningDebt(u1, date(2026, 11, 1), 100000)],
        advances=[opening.OpeningAdvance(u2, 40000)],
        user=None,
    )
    summary = site_summary(site, today=date(2026, 11, 20))
    assert summary["accrued"] == 0 and summary["collected"] == 0
    assert summary["receivable"] == 100000 and summary["advance"] == 40000


# --- aylık çizelge (aidat_takip) --------------------------------------------------------------


def grid_file(header=("Daire No", "Ad Soyad", "Ocak", "Şubat", "Mart"), rows=()):
    return xlsx({"Aidat 2026": [["Çınar Evleri aidat çizelgesi"], [], list(header), *rows]})


@pytest.fixture
def grid_site(site):
    t = UnitType.objects.create(site=site, name="2+1", default_amount=100000)
    a = Block.objects.create(site=site, name="A")
    units = [Unit.objects.create(site=site, block=a, number=str(n), unit_type=t) for n in (1, 2, 3)]
    Unit.objects.create(site=site, block=a, number="4")  # tipsiz bölüm
    return site, units


GRID_OPTS = {"first_month": "2026-01-01", "opening_date": "2026-03-31", "lock": False, "unit_column": "Daire No"}


@pytest.mark.django_db
def test_grid_ages_debt_and_detects_advance(grid_site):
    site, (u1, u2, u3) = grid_site
    data = grid_file(
        rows=[
            ["A-1", "x", 1000, 1000, None],  # Mart borçlu
            ["A 2", "x", "1000", "kısmi 500", "ödedi"],  # 1500 ödendi → Şubat 500 + Mart 1000
            ["a3", "x", 2000, 1000, 1000],  # 1000 fazla
            ["Toplam", "", 4000, 2500, 1000],
        ]
    )
    payload, issues = grid.parse(data, GRID_OPTS)
    assert payload["months"] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    kinds = [(i.level, i.message) for i in issues.items]
    assert any(level == "warning" and "okunamadı" in m for level, m in kinds)
    assert any("Toplam satırı" in m for _, m in kinds)
    p = grid.plan(site, payload, GRID_OPTS)
    assert not p.issues.has_errors, p.issues.as_list()
    rows = {r.unit.pk: r for r in p.grid_rows}
    assert (rows[u1.pk].owed, rows[u2.pk].owed, rows[u3.pk].advance) == (100000, 150000, 100000)
    grid.apply(site, payload, GRID_OPTS, None)
    open_u2 = {c.period: c.remaining for c in billing.open_charges(u2)}
    assert open_u2 == {date(2026, 2, 1): 50000, date(2026, 3, 1): 100000}
    assert payments.advance_balance(u3) == 100000
    check_consistency(site)


@pytest.mark.django_db
def test_grid_applies_agreements_and_needs_fee(grid_site):
    site, (u1, *_) = grid_site
    DuesAgreement.objects.create(unit=u1, kind="exempt", valid_from=date(2026, 1, 1))
    data = grid_file(rows=[["A1", "", None, None, None], ["A4", "", None, None, None]])
    payload, _ = grid.parse(data, GRID_OPTS)
    p = grid.plan(site, payload, GRID_OPTS)
    assert any("aidatı yok" in i.message for i in p.issues.items)
    p = grid.plan(site, payload, {**GRID_OPTS, "monthly_fee": 50000})
    rows = {r.label: r for r in p.grid_rows}
    assert rows["A1"].owed == 0 and rows["A4"].owed == 150000


@pytest.mark.django_db
def test_grid_unknown_and_ambiguous_units(grid_site):
    site, _ = grid_site
    Unit.objects.create(site=site, block=Block.objects.create(site=site, name="B"), number="1")
    payload, _ = grid.parse(grid_file(rows=[["Z9", "", 1, 1, 1], ["1", "", 1, 1, 1]]), GRID_OPTS)
    messages = [i.message for i in grid.plan(site, payload, GRID_OPTS).issues.items]
    assert any("bulunamadı" in m for m in messages) and any("birden çok" in m for m in messages)


def test_grid_month_headers():
    with pytest.raises(SheetError, match="İlk ay"):
        grid.parse(grid_file(rows=[["A1", "", 1, 1, 1]]), {})
    with pytest.raises(SheetError, match="ardışık"):
        grid.parse(grid_file(header=("Daire No", "Ocak", "Mart"), rows=[["A1", 1, 1]]), GRID_OPTS)
    payload, _ = grid.parse(
        grid_file(header=("Daire No", date(2025, 12, 1), date(2026, 1, 1)), rows=[["A1", 1, 1]]), {}
    )
    assert payload["months"] == ["2025-12-01", "2026-01-01"]
    with pytest.raises(SheetError, match="Bölüm No|başlıklı sütun"):
        grid.parse(grid_file(header=("Daire", "Ocak"), rows=[["A1", 1]]), GRID_OPTS)


def test_compact_unit_labels():
    assert grid.compact("A Blok · 5") == grid.compact("A-5") == grid.compact("a5") == "a5"


# --- ekranlar ---------------------------------------------------------------------------------


def upload(client, site, data, **fields):
    form = {
        "kind": "setup",
        "start_date": "2026-01-01",
        "opening_date": OPEN_DAY.isoformat(),
        "unit_column": "Daire No",
        "lock": "on",
        "file": SimpleUploadedFile("kurulum.xlsx", data),
    }
    form.update(fields)
    return client.post(reverse("imports:setup_home", args=[site.pk]), form)


@pytest.mark.django_db
def test_upload_preview_apply_flow(client, admin_a, org_a, site_a):
    with tenant_context(org_a.pk):
        billing.setup_site(site_a)
    panel_client(client, admin_a, org_a)
    assert client.get(reverse("imports:setup_home", args=[site_a.pk])).status_code == 200
    response = client.get(reverse("imports:template", args=[site_a.pk]))
    assert response.status_code == 200 and response["Content-Type"].startswith("application/vnd.openxml")

    response = upload(client, site_a, setup_file([unit_row("A", "1")], [["A", "1", "2026-10", "1.000,00", ""]]))
    assert response.status_code == 302
    with tenant_context(org_a.pk):
        job = ImportJob.objects.get()
        assert job.payload["units"][0]["owner"]["phone"] == "05550000001"
    page = client.get(response.url).content.decode()
    assert "Onayla ve uygula" in page and "Deniz Örnek" in page
    with tenant_context(org_a.pk):
        assert not Unit.objects.exists()  # önizleme hiçbir şey yazmaz

    response = client.post(reverse("imports:job_apply", args=[site_a.pk, job.pk]))
    assert response.status_code == 302
    with tenant_context(org_a.pk):
        job.refresh_from_db()
        assert job.status == "applied" and job.payload == {} and job.summary["yeni_bolum"] == 1
        assert Unit.objects.count() == 1 and ledger.locked_until(site_a) == OPEN_DAY
    # ikinci kez uygulanamaz
    client.post(reverse("imports:job_apply", args=[site_a.pk, job.pk]))
    with tenant_context(org_a.pk):
        assert Charge.objects.count() == 1


@pytest.mark.django_db
def test_upload_errors_and_discard(client, admin_a, org_a, site_a):
    with tenant_context(org_a.pk):
        billing.setup_site(site_a)
    panel_client(client, admin_a, org_a)
    response = upload(client, site_a, b"bozuk", file=SimpleUploadedFile("x.xlsx", b"bozuk"))
    assert response.status_code == 200 and "açılamadı" in response.content.decode()
    response = upload(client, site_a, b"", file=SimpleUploadedFile("x.csv", b"a,b"))
    assert ".xlsx" in response.content.decode()

    response = upload(client, site_a, setup_file([unit_row("A", "1", owner=("Ali", "", ""))]))
    page = client.get(response.url).content.decode()
    assert "hata var" in page and "Onayla ve uygula" not in page
    with tenant_context(org_a.pk):
        job = ImportJob.objects.get()
    client.post(reverse("imports:job_discard", args=[site_a.pk, job.pk]))
    with tenant_context(org_a.pk):
        job.refresh_from_db()
        assert job.status == "discarded" and job.payload == {}


@pytest.mark.django_db
def test_grid_upload_defaults_opening_date(client, admin_a, org_a, site_a):
    with tenant_context(org_a.pk):
        billing.setup_site(site_a)
        Unit.objects.create(site=site_a, number="1")
    panel_client(client, admin_a, org_a)
    response = upload(
        client,
        site_a,
        b"",
        kind="grid",
        opening_date="",
        first_month="2026-01",
        monthly_fee="1.000,00",
        file=SimpleUploadedFile("cizelge.xlsx", grid_file(rows=[["1", "", 1000, None, None]])),
    )
    assert response.status_code == 302
    with tenant_context(org_a.pk):
        job = ImportJob.objects.get()
        assert job.options["opening_date"] == "2026-03-31"
    client.post(reverse("imports:job_apply", args=[site_a.pk, job.pk]))
    with tenant_context(org_a.pk):
        assert sum(c.remaining for c in billing.open_charges(site=site_a)) == 200000


@pytest.mark.django_db
def test_setup_requires_permission_and_own_site(client, org_a, site_a, admin_b, org_b):
    accountant = make_user("muhasebe@example.test")
    add_member(accountant, org_a, Role.ACCOUNTANT)
    panel_client(client, accountant, org_a)
    assert client.get(reverse("imports:setup_home", args=[site_a.pk])).status_code == 403
    panel_client(client, admin_b, org_b)
    assert client.get(reverse("imports:setup_home", args=[site_a.pk])).status_code == 404


@pytest.mark.django_db
def test_site_admin_cannot_lock_through_import(client, org_a, site_a):
    with tenant_context(org_a.pk):
        billing.setup_site(site_a)
    user = make_user("site-yonetici@example.test")
    add_member(user, org_a, Role.SITE_ADMIN)
    panel_client(client, user, org_a)
    response = upload(client, site_a, setup_file([unit_row("A", "1")]), lock="on")
    with tenant_context(org_a.pk):
        job = ImportJob.objects.get()
        assert job.options["lock"] is False
    assert response.status_code == 302


@pytest.mark.django_db
def test_opening_advance_has_no_printable_receipt(client, admin_a, org_a, site_a):
    with tenant_context(org_a.pk):
        billing.setup_site(site_a)
        unit = Unit.objects.create(site=site_a, number="1")
        result = opening.post_opening(
            site_a, day=OPEN_DAY, debts=[], advances=[opening.OpeningAdvance(unit, 500)], user=None
        )
        payment = result.advances[0]
        assert payment.receipt_no == "Açılış" and payment.channel == Payment.Channel.OPENING
    panel_client(client, admin_a, org_a)
    assert client.get(reverse("payments:receipt", args=[site_a.pk, payment.pk])).status_code == 404
    page = client.get(reverse("payments:payment_detail", args=[site_a.pk, payment.pk])).content.decode()
    assert "Açılış avansı" in page and "Makbuzu yazdır" not in page
