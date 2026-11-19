from django import forms

from domain.money import InvalidAmount, format_try, parse_try


class MoneyInput(forms.TextInput):
    input_type = "text"

    def __init__(self, attrs=None):
        super().__init__({"inputmode": "decimal", "autocomplete": "off", "placeholder": "0,00", **(attrs or {})})

    def format_value(self, value):
        if isinstance(value, int):
            return format_try(value, symbol=False)
        return super().format_value(value)


class MoneyField(forms.Field):
    """Türkçe yazılmış tutarı ('1.234,56') kuruşa (int) çevirir."""

    widget = MoneyInput
    default_error_messages = {"invalid": "Geçerli bir tutar girin (ör. 1.250,00)."}

    def __init__(self, *, min_value: int | None = 0, allow_zero: bool = False, **kwargs):
        self.min_value, self.allow_zero = min_value, allow_zero
        super().__init__(**kwargs)

    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, int):
            return value
        try:
            return parse_try(str(value))
        except InvalidAmount as exc:
            raise forms.ValidationError(self.error_messages["invalid"], code="invalid") from exc

    def validate(self, value):
        super().validate(value)
        if value is not None and value == 0 and not self.allow_zero:
            raise forms.ValidationError("Tutar sıfırdan büyük olmalı.", code="zero")


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, attrs=None):
        super().__init__(attrs, format="%Y-%m-%d")
