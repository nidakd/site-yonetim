from django import forms
from django.contrib.auth import password_validation

from domain.phone import is_mobile, normalize_phone

from .models import PortalSettings


class PhoneForm(forms.Form):
    phone = forms.CharField(
        label="Cep telefonu",
        max_length=20,
        widget=forms.TextInput(attrs={"inputmode": "tel", "autocomplete": "tel", "placeholder": "05xx xxx xx xx"}),
    )

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if not is_mobile(phone):
            raise forms.ValidationError("Cep telefonunuzu 05xx xxx xx xx biçiminde girin.")
        return phone


class CodeForm(forms.Form):
    code = forms.CharField(
        label="SMS ile gelen kod",
        max_length=10,
        widget=forms.TextInput(
            attrs={"inputmode": "numeric", "autocomplete": "one-time-code", "pattern": "[0-9 ]*", "autofocus": True}
        ),
    )
    remember = forms.BooleanField(label="Bu cihazda 30 gün oturumum açık kalsın", required=False)


class PasswordLoginForm(PhoneForm):
    password = forms.CharField(label="Şifre", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))
    remember = forms.BooleanField(label="Bu cihazda 30 gün oturumum açık kalsın", required=False)


class ResidentPasswordForm(forms.Form):
    password1 = forms.CharField(label="Yeni şifre", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))
    password2 = forms.CharField(
        label="Yeni şifre (tekrar)", widget=forms.PasswordInput(attrs={"autocomplete": "new-password"})
    )

    def clean(self):
        data = super().clean()
        p1, p2 = data.get("password1"), data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Şifreler aynı değil.")
        if p1:
            password_validation.validate_password(p1)
        return data


class PortalSettingsForm(forms.ModelForm):
    class Meta:
        model = PortalSettings
        fields = ["portal_enabled", "show_collection_rate", "show_cash_balance", "show_expenses", "contact_note"]
        labels = {
            "portal_enabled": "Sakin portalı açık",
            "show_collection_rate": "Bu ayın tahsilat oranı",
            "show_cash_balance": "Kasa ve banka toplamı",
            "show_expenses": "Giderler (kategori toplamları)",
            "contact_note": "İletişim notu",
        }
