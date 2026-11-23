from django import template

register = template.Library()


@register.filter
def add_class(field, css: str):
    """Form alanını verilen sınıflarla çizer; hatalıysa hata sınıfını da ekler."""
    classes = css + (" input-error" if field.errors else "")
    attrs = {"class": classes}
    if field.errors:
        attrs["aria-invalid"] = "true"
        attrs["aria-describedby"] = f"{field.auto_id}_error"
    return field.as_widget(attrs=attrs)


@register.filter
def tl(kurus):
    """Kuruş → '1.234,56 ₺'. Boş değer için tire."""
    if kurus is None or kurus == "":
        return "—"
    from domain.money import format_try

    return format_try(int(kurus))


@register.filter
def tl_plain(kurus):
    from domain.money import format_try

    return "" if kurus is None else format_try(int(kurus), symbol=False)


@register.filter
def tr_date(value):
    return value.strftime("%d.%m.%Y") if value else "—"


MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


@register.filter
def period(value):
    """date(2026, 11, 1) → 'Kasım 2026'."""
    return f"{MONTHS[value.month - 1]} {value.year}" if value else "—"


@register.simple_tag(takes_context=True)
def site_can(context, perm):
    from apps.tenancy.permissions import has_perm

    site = context.get("site")
    return has_perm(context["request"], perm, site.pk if site else None)


@register.filter
def absval(value):
    return abs(value) if value is not None else value


# Site içindeki gruplu yan menü: (grup, [(etiket, url adı, yetki, etkin sayılan görünümler, simge)])
# Etkin görünüm "uygulama:" ile bitiyorsa o uygulamanın tüm sayfaları sayılır.
SITE_MENU = [
    (None, [("Özet", "tenancy:site_detail", "site.view", ("tenancy:site_detail",), "home")]),
    (
        "Yönetim",
        [
            ("Bloklar", "property:block_list", "site.view", ("property:block_list", "property:block_update"), "block"),
            (
                "Daireler",
                "property:unit_list",
                "site.view",
                (
                    "property:unit_list",
                    "property:unit_create",
                    "property:unit_bulk_create",
                    "property:unit_detail",
                    "property:unit_update",
                    "people:occupancy_create",
                    "people:occupancy_end",
                    "billing:agreement_create",
                    "billing:agreement_end",
                    "reports:unit_statement",
                ),
                "door",
            ),
            ("Kişiler", "people:person_list", "people.view", ("people:person_", "people:person_create"), "people"),
        ],
    ),
    (
        "Muhasebe",
        [
            ("Borçlandırma", "billing:batch_list", "finance.view", ("billing:batch_",), "receipt"),
            ("Tahsilat", "payments:payment_list", "finance.view", ("payments:",), "cash"),
            ("Giderler", "expenses:expense_list", "finance.view", ("expenses:expense_",), "out"),
            (
                "Kasa ve banka",
                "expenses:cash_bank",
                "finance.view",
                ("expenses:cash_bank", "ledger:account_detail"),
                "bank",
            ),
            ("Borçlular", "reports:debtors", "finance.view", ("reports:debtors",), "alert"),
            ("Raporlar", "ledger:trial_balance", "finance.view", ("ledger:trial_balance", "ledger:journal"), "chart"),
        ],
    ),
    (
        "Ayarlar",
        [
            (
                "Daire tipleri ve aidat",
                "property:structure",
                "site.view",
                ("property:structure", "property:unit_type_update"),
                "tag",
            ),
            (
                "Muhasebe ayarları",
                "ledger:settings",
                "finance.view",
                ("ledger:settings", "billing:charge_type_"),
                "gear",
            ),
            ("Excel'den kurulum", "imports:setup_home", "setup.manage", ("imports:",), "upload"),
            ("Sakin portalı", "residents_panel:portal_settings", "site.update", ("residents_panel:",), "phone"),
            ("Site bilgileri", "tenancy:site_update", "site.update", ("tenancy:site_update",), "info"),
        ],
    ),
]

# Telefonda alt çubukta görünenler
SITE_TABBAR = ["Bloklar", "Daireler", "Tahsilat", "Kişiler"]


def _active(view_name: str, patterns) -> bool:
    return any(view_name == p or (p.endswith(("_", ":")) and view_name.startswith(p)) for p in patterns)


@register.simple_tag(takes_context=True)
def site_menu(context):
    """Yetkiye göre süzülmüş menü grupları: [{"title", "items": [{"label","url","active","icon"}]}]."""
    from django.urls import reverse

    from apps.tenancy.permissions import has_perm

    request, site = context["request"], context.get("site")
    if site is None:
        return []
    view_name = request.resolver_match.view_name if request.resolver_match else ""
    groups = []
    for title, items in SITE_MENU:
        visible = [
            {
                "label": label,
                "url": reverse(url_name, args=[site.pk]),
                "active": _active(view_name, patterns),
                "icon": icon,
                "tabbar": label in SITE_TABBAR,
            }
            for label, url_name, perm, patterns, icon in items
            if has_perm(request, perm, site.pk)
        ]
        if visible:
            groups.append({"title": title, "items": visible})
    return groups
