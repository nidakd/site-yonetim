import re

from django import forms

from apps.core.forms import DateInput

from .models import Occupancy, Person


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("90") and len(digits) == 12:
        digits = "0" + digits[2:]
    if digits and len(digits) == 10:
        digits = "0" + digits
    return digits


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ["first_name", "last_name", "phone", "email", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2}), "phone": forms.TextInput(attrs={"inputmode": "tel"})}

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if phone and not re.fullmatch(r"0\d{10}", phone):
            raise forms.ValidationError("Telefonu 05xx xxx xx xx biçiminde girin.")
        return phone


class OccupancyForm(forms.ModelForm):
    """Bölüme kişi bağlama: kayıtlı bir kişiyi seç ya da yeni kişi bilgilerini gir."""

    person = forms.ModelChoiceField(
        Person.unscoped.none(), label="Kayıtlı kişi", required=False, empty_label="— Yeni kişi ekle —"
    )
    first_name = forms.CharField(label="Ad", max_length=80, required=False)
    last_name = forms.CharField(label="Soyad", max_length=80, required=False)
    phone = forms.CharField(
        label="Telefon", max_length=20, required=False, widget=forms.TextInput(attrs={"inputmode": "tel"})
    )
    email = forms.EmailField(label="E-posta", required=False)

    class Meta:
        model = Occupancy
        fields = ["relation", "start_date", "share_percent", "is_liable", "receives_notifications"]
        widgets = {"start_date": DateInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["person"].queryset = Person.objects.filter(is_anonymized=False)
        self.fields["person"].widget.attrs["x-model"] = "existing"

    def clean(self):
        data = super().clean()
        if not data.get("person"):
            if not (data.get("first_name") or "").strip() or not (data.get("last_name") or "").strip():
                raise forms.ValidationError("Kayıtlı bir kişi seçin ya da yeni kişinin adını ve soyadını girin.")
            phone = normalize_phone(data.get("phone", ""))
            if phone and not re.fullmatch(r"0\d{10}", phone):
                self.add_error("phone", "Telefonu 05xx xxx xx xx biçiminde girin.")
            data["phone"] = phone
        return data
