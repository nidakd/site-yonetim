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
