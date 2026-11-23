from django import forms

from apps.core.forms import DateInput, MoneyField
from domain.phone import is_valid_phone, normalize_phone

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
    unit_type = forms.ModelChoiceField(UnitType.unscoped.none(), label="Daire tipi", required=False)

    class Meta:
        model = Unit
        fields = ["block", "number", "floor", "kind", "unit_type", "gross_area", "share_numerator", "share_denominator"]
        labels = {
            "number": "Daire no",
            "kind": "Tür",
            "share_numerator": "Arsa payı (pay)",
            "share_denominator": "Arsa payı (payda)",
        }

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


class BlockCreateForm(BlockForm):
    """Blok eklerken daireleri de tek seferde açmak için (Blok adı + daire sayısı + kat sayısı)."""

    unit_count = forms.IntegerField(
        label="Daire sayısı",
        min_value=0,
        max_value=500,
        required=False,
        help_text="Boş bırakırsanız yalnızca blok eklenir.",
    )
    floors = forms.IntegerField(
        label="Kat sayısı", min_value=1, max_value=100, required=False, help_text="Daireler katlara sırayla dağıtılır."
    )
    first_number = forms.IntegerField(label="İlk daire no", min_value=1, max_value=9999, initial=1, required=False)
    unit_type = forms.ModelChoiceField(UnitType.unscoped.none(), label="Daire tipi", required=False, empty_label="—")

    class Meta(BlockForm.Meta):
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit_type"].queryset = UnitType.objects.filter(site=self.site)

    def unit_numbers(self) -> list[tuple[int, int | None]]:
        """[(daire no, kat)] — kat 0'dan başlar (zemin)."""
        d = self.cleaned_data
        count = d.get("unit_count") or 0
        first = d.get("first_number") or 1
        floors = d.get("floors")
        per_floor = -(-count // floors) if floors and count else None
        return [(first + i, (i // per_floor) if per_floor else None) for i in range(count)]


class UnitWithPeopleForm(UnitForm):
    """Yeni daire: daire bilgileri + isteğe bağlı malik ve kiracı (tek seferde kayıt)."""

    PEOPLE = (("owner", "Malik"), ("tenant", "Kiracı"))

    liable = forms.ChoiceField(
        label="Borç sorumlusu",
        choices=[("", "Otomatik (kiracı varsa kiracı)"), ("owner", "Malik"), ("tenant", "Kiracı")],
        required=False,
    )
    start_date = forms.DateField(
        label="Oturma / mülkiyet başlangıcı",
        required=False,
        widget=DateInput(),
        help_text="Boşsa bugün. Aidat, ayın 1'inde sorumlu olan kişiye yazılır.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key, label in self.PEOPLE:
            self.fields[f"{key}_first"] = forms.CharField(label=f"{label} adı", max_length=80, required=False)
            self.fields[f"{key}_last"] = forms.CharField(label=f"{label} soyadı", max_length=80, required=False)
            self.fields[f"{key}_phone"] = forms.CharField(
                label=f"{label} cep telefonu",
                max_length=20,
                required=False,
                widget=forms.TextInput(attrs={"inputmode": "tel", "placeholder": "05xx xxx xx xx"}),
            )
            self.fields[f"{key}_email"] = forms.EmailField(label=f"{label} e-postası", required=False)
        for name in self.fields:
            if name.startswith(("owner_", "tenant_")) or name in ("liable", "start_date"):
                self.fields[name].hide_optional = True

    def person(self, key: str) -> dict | None:
        d = self.cleaned_data
        first, last = (d.get(f"{key}_first") or "").strip(), (d.get(f"{key}_last") or "").strip()
        if not first:
            return None
        return {
            "first": first,
            "last": last,
            "phone": d.get(f"{key}_phone") or "",
            "email": d.get(f"{key}_email") or "",
        }

    def clean(self):
        data = super().clean()
        for key, label in self.PEOPLE:
            first, last = (data.get(f"{key}_first") or "").strip(), (data.get(f"{key}_last") or "").strip()
            phone = normalize_phone(data.get(f"{key}_phone"))
            if (first or last or phone or data.get(f"{key}_email")) and not (first and last):
                self.add_error(f"{key}_first", f"{label} için ad ve soyadı birlikte girin.")
            if phone and not is_valid_phone(phone):
                self.add_error(f"{key}_phone", "Telefonu 05xx xxx xx xx biçiminde girin.")
            data[f"{key}_phone"] = phone
        if data.get("liable") == "tenant" and not (data.get("tenant_first") or "").strip():
            self.add_error("liable", "Borç sorumlusu kiracı seçildi ama kiracı bilgisi yok.")
        return data
