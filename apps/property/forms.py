from django import forms

from apps.core.forms import MoneyField

from .models import Block, Unit, UnitType


class SiteBoundForm(forms.ModelForm):
    """Seçim alanları yalnızca bu sitenin kayıtlarını gösterir (başka sitenin bloğu seçilemez)."""

    def __init__(self, *args, site, **kwargs):
        self.site = site
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field, forms.ModelChoiceField) and hasattr(field.queryset.model, "site"):
                field.queryset = field.queryset.model.objects.filter(site=site)
                if name in ("block", "unit_type"):
                    field.empty_label = "—"

    def save(self, commit=True):
        self.instance.site = self.site
        return super().save(commit)


class BlockForm(SiteBoundForm):
    class Meta:
        model = Block
        fields = ["name", "order"]

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        qs = Block.objects.filter(site=self.site, name__iexact=name).exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Bu adla bir blok zaten var.")
        return name


class UnitTypeForm(SiteBoundForm):
    default_amount = MoneyField(label="Aylık aidat", required=False, allow_zero=True)

    class Meta:
        model = UnitType
        fields = ["name", "default_amount"]

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        if UnitType.objects.filter(site=self.site, name__iexact=name).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Bu adla bir bölüm tipi zaten var.")
        return name


class UnitForm(SiteBoundForm):
    block = forms.ModelChoiceField(Block.unscoped.none(), label="Blok", required=False)
    unit_type = forms.ModelChoiceField(UnitType.unscoped.none(), label="Bölüm tipi", required=False)

    class Meta:
        model = Unit
        fields = ["block", "number", "floor", "kind", "unit_type", "gross_area", "share_numerator", "share_denominator"]
        labels = {"share_numerator": "Arsa payı (pay)", "share_denominator": "Arsa payı (payda)"}

    def clean_number(self):
        return self.cleaned_data["number"].strip()

    def clean(self):
        data = super().clean()
        num, den = data.get("share_numerator"), data.get("share_denominator")
        if (num is None) != (den is None):
            raise forms.ValidationError("Arsa payının hem payını hem paydasını girin ya da ikisini de boş bırakın.")
        if num is not None and den is not None and num > den:
            self.add_error("share_numerator", "Pay, paydadan büyük olamaz.")
        number, block = data.get("number"), data.get("block")
        if number:
            qs = Unit.objects.filter(site=self.site, block=block, number__iexact=number).exclude(pk=self.instance.pk)
            if qs.exists():
                self.add_error("number", "Bu blokta aynı numaralı bir bölüm zaten var.")
        return data


class BulkUnitForm(forms.Form):
    """Bir blokta ardışık numaralı bölümleri tek seferde açmak için (kurulum kolaylığı)."""

    block = forms.ModelChoiceField(Block.unscoped.none(), label="Blok", required=False, empty_label="—")
    first = forms.IntegerField(label="İlk no", min_value=1, max_value=9999)
    last = forms.IntegerField(label="Son no", min_value=1, max_value=9999)
    kind = forms.ChoiceField(label="Tür", choices=Unit.Kind.choices)
    unit_type = forms.ModelChoiceField(UnitType.unscoped.none(), label="Bölüm tipi", required=False, empty_label="—")

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.site = site
        self.fields["block"].queryset = Block.objects.filter(site=site)
        self.fields["unit_type"].queryset = UnitType.objects.filter(site=site)

    def clean(self):
        data = super().clean()
        first, last = data.get("first"), data.get("last")
        if first and last:
            if last < first:
                raise forms.ValidationError("Son no, ilk nodan küçük olamaz.")
            if last - first >= 500:
                raise forms.ValidationError("Tek seferde en fazla 500 bölüm açılabilir.")
            existing = set(
                Unit.objects.filter(site=self.site, block=data.get("block")).values_list("number", flat=True)
            )
            clash = [str(n) for n in range(first, last + 1) if str(n) in existing]
            if clash:
                raise forms.ValidationError(f"Bu numaralar zaten var: {', '.join(clash[:10])}")
        return data
