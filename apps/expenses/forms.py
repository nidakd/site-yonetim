from django import forms
from django.utils import timezone

from apps.core.forms import DateInput, MoneyField
from apps.ledger.services import cash_accounts, expense_accounts

from .models import Vendor


class ExpenseForm(forms.Form):
    date = forms.DateField(label="Tarih", widget=DateInput())
    category = forms.ModelChoiceField(None, label="Gider kalemi", empty_label=None)
    description = forms.CharField(label="Açıklama", max_length=200)
    amount = MoneyField(label="Tutar")
    paid_from = forms.ModelChoiceField(None, label="Ödendiği hesap", empty_label=None)
    vendor = forms.ModelChoiceField(Vendor.unscoped.none(), label="Tedarikçi", required=False, empty_label="—")
    document_number = forms.CharField(label="Fatura / belge no", max_length=60, required=False)

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate()
        self.fields["category"].queryset = expense_accounts(site)
        self.fields["paid_from"].queryset = cash_accounts(site)
        self.fields["vendor"].queryset = Vendor.objects.filter(site=site)


class VendorForm(forms.ModelForm):
    class Meta:
        model = Vendor
        fields = ["name", "tax_number", "phone"]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if Vendor.objects.filter(site=self.site, name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Bu adla bir tedarikçi zaten var.")
        return name

    def save(self, commit=True):
        self.instance.site = self.site
        return super().save(commit)


class TransferForm(forms.Form):
    date = forms.DateField(label="Tarih", widget=DateInput())
    from_account = forms.ModelChoiceField(None, label="Çıkan hesap", empty_label=None)
    to_account = forms.ModelChoiceField(None, label="Giren hesap", empty_label=None)
    amount = MoneyField(label="Tutar")
    description = forms.CharField(label="Açıklama", max_length=200, required=False)

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = timezone.localdate()
        for f in ("from_account", "to_account"):
            self.fields[f].queryset = cash_accounts(site)

    def clean(self):
        data = super().clean()
        if data.get("from_account") and data.get("from_account") == data.get("to_account"):
            raise forms.ValidationError("Çıkan ve giren hesap farklı olmalı.")
        return data


class AccountForm(forms.Form):
    GROUPS = [("bank", "Banka hesabı"), ("cash", "Kasa"), ("expense", "Gider kalemi")]

    group = forms.ChoiceField(label="Hesap türü", choices=GROUPS)
    name = forms.CharField(label="Ad", max_length=120)
    iban = forms.CharField(label="IBAN", max_length=40, required=False)


class PeriodLockForm(forms.Form):
    locked_until = forms.DateField(label="Bu tarih dahil öncesini kilitle", widget=DateInput())
