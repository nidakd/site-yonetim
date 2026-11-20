from django import forms
from django.utils import timezone

from apps.core.forms import DateInput, MoneyField
from apps.ledger.services import cash_accounts
from apps.people.models import Person, active_on
from apps.property.models import Unit

from .models import Payment


class PaymentForm(forms.Form):
    unit = forms.ModelChoiceField(Unit.unscoped.none(), label="Bölüm")
    date = forms.DateField(label="Tarih", widget=DateInput())
    amount = MoneyField(label="Tutar")
    channel = forms.ChoiceField(label="Ödeme şekli", choices=[c for c in Payment.Channel.choices if c[0] != "opening"])
    account = forms.ModelChoiceField(None, label="Kasa / banka", empty_label=None)
    payer = forms.ModelChoiceField(Person.unscoped.none(), label="Ödeyen", required=False, empty_label="—")
    reference = forms.CharField(label="Dekont / referans", max_length=120, required=False)
    note = forms.CharField(label="Not", max_length=255, required=False)
    preferred = forms.MultipleChoiceField(
        label="Önce kapatılacak borçlar", required=False, widget=forms.CheckboxSelectMultiple
    )

    def __init__(self, *args, site, unit=None, open_charges=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.fields["unit"].queryset = Unit.objects.filter(site=site, is_archived=False).select_related("block")
        self.fields["account"].queryset = cash_accounts(site)
        self.fields["date"].initial = timezone.localdate()
        if unit is not None:
            linked = unit.occupancies.filter(active_on(timezone.localdate())).values("person_id")
            self.fields["payer"].queryset = Person.objects.filter(pk__in=linked)
        self.fields["preferred"].choices = [(str(c.pk), str(c.pk)) for c in open_charges]

    def clean(self):
        data = super().clean()
        if data.get("channel") == Payment.Channel.CASH and data.get("account") and data["account"].cash_kind != "cash":
            self.add_error("account", "Nakit tahsilat kasaya alınır.")
        return data
