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
