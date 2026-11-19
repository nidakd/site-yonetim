from django import forms

from apps.core.forms import DateInput, MoneyField, MonthField
from apps.ledger.models import Account
from apps.property.models import Block, Unit

from .models import ChargeBatch, ChargeType, DuesAgreement
from .services import overlapping_agreements


class AgreementForm(forms.ModelForm):
    amount = MoneyField(label="Aylık tutar", required=False, allow_zero=True)

    class Meta:
        model = DuesAgreement
        fields = ["kind", "amount", "discount_percent", "valid_from", "valid_to", "decision_ref", "note"]
        widgets = {"valid_from": DateInput(), "valid_to": DateInput()}
        help_texts = {"decision_ref": "Ör. 12.03.2026 tarihli genel kurul kararı, madde 4"}

    def __init__(self, *args, unit, **kwargs):
        super().__init__(*args, **kwargs)
        self.unit = unit
        self.fields["kind"].widget.attrs["x-model"] = "kind"

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        if kind == DuesAgreement.Kind.FIXED and data.get("amount") is None:
            self.add_error("amount", "Sabit tutarı girin.")
        if kind == DuesAgreement.Kind.DISCOUNT:
            pct = data.get("discount_percent")
            if pct is None or not 0 < pct <= 100:
                self.add_error("discount_percent", "0 ile 100 arasında bir oran girin.")
        start, end = data.get("valid_from"), data.get("valid_to")
        if start and end and end < start:
            self.add_error("valid_to", "Bitiş, başlangıçtan önce olamaz.")
        if start and overlapping_agreements(self.unit, start, end).exists():
            raise forms.ValidationError(
                "Bu tarihlerde bölümün başka bir özel aidat anlaşması var; önce onu sonlandırın."
            )
        if kind != DuesAgreement.Kind.FIXED:
            data["amount"] = None
        if kind != DuesAgreement.Kind.DISCOUNT:
            data["discount_percent"] = None
        return data

    def save(self, commit=True):
        self.instance.unit = self.unit
        self.instance.amount = self.cleaned_data.get("amount")
        return super().save(commit)


class BatchForm(forms.ModelForm):
    SCOPES = [("site", "Tüm site"), ("block", "Bir blok"), ("unit", "Tek bölüm")]

    charge_type = forms.ModelChoiceField(ChargeType.unscoped.none(), label="Borç türü")
    block = forms.ModelChoiceField(Block.unscoped.none(), label="Blok", required=False)
    unit = forms.ModelChoiceField(Unit.unscoped.none(), label="Bölüm", required=False)
    period = MonthField(label="Dönem")
    amount = MoneyField(label="Tutar", required=False)
    scope = forms.ChoiceField(label="Kapsam", choices=SCOPES, initial="site")

    class Meta:
        model = ChargeBatch
        fields = [
            "charge_type",
            "period",
            "due_date",
            "method",
            "amount",
            "block",
            "unit",
            "description",
            "decision_ref",
        ]
        widgets = {"due_date": DateInput()}
        help_texts = {"description": "Sakinin ekstresinde görünür. Boş bırakırsanız tür ve dönemden oluşturulur."}

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.fields["charge_type"].queryset = ChargeType.objects.filter(site=site, is_active=True)
        self.fields["charge_type"].empty_label = None
        self.fields["block"].queryset = Block.objects.filter(site=site)
        self.fields["unit"].queryset = Unit.objects.filter(site=site, is_archived=False).select_related("block")
        self.fields["description"].required = False
        for name in ("block", "unit"):
            self.fields[name].required = False
        self.fields["method"].widget.attrs["x-model"] = "method"
        self.fields["scope"].widget.attrs["x-model"] = "scope"

    def clean(self):
        data = super().clean()
        method, amount, scope = data.get("method"), data.get("amount"), data.get("scope")
        if method and method != ChargeBatch.Method.UNIT_TYPE and not amount:
            self.add_error("amount", "Tutarı girin.")
        if method == ChargeBatch.Method.UNIT_TYPE:
            data["amount"] = None
        if scope == "block" and not data.get("block"):
            self.add_error("block", "Blok seçin.")
        if scope == "unit" and not data.get("unit"):
            self.add_error("unit", "Bölüm seçin.")
        if scope != "block":
            data["block"] = None
        if scope != "unit":
            data["unit"] = None
        period, due = data.get("period"), data.get("due_date")
        if period and due and due < period:
            self.add_error("due_date", "Son ödeme tarihi dönemden önce olamaz.")
        if not data.get("description") and data.get("charge_type") and period:
            from apps.core.templatetags.ui import period as period_label

            data["description"] = f"{period_label(period)} {data['charge_type'].name.lower()}"
        return data

    def save(self, commit=True):
        self.instance.site = self.site
        for f in ("amount", "block", "unit", "description"):
            setattr(self.instance, f, self.cleaned_data.get(f))
        return super().save(commit)


class ChargeTypeForm(forms.ModelForm):
    income_account = forms.ModelChoiceField(Account.unscoped.none(), label="Gelir hesabı")

    class Meta:
        model = ChargeType
        fields = ["name", "income_account", "priority", "apply_agreements", "once_per_period", "is_active"]

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.fields["income_account"].queryset = Account.objects.filter(site=site, kind=Account.Kind.INCOME)

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if ChargeType.objects.filter(site=self.site, name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Bu adla bir borç türü zaten var.")
        return name

    def save(self, commit=True):
        self.instance.site = self.site
        return super().save(commit)
