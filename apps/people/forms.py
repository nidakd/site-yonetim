from django import forms

from apps.core.forms import DateInput
from apps.property.models import Unit
from domain.phone import is_valid_phone, normalize_phone

from .models import Occupancy, Person


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ["first_name", "last_name", "phone", "email", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2}), "phone": forms.TextInput(attrs={"inputmode": "tel"})}

    def clean_phone(self):
        phone = normalize_phone(self.cleaned_data["phone"])
        if phone and not is_valid_phone(phone):
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
            if phone and not is_valid_phone(phone):
                self.add_error("phone", "Telefonu 05xx xxx xx xx biçiminde girin.")
            data["phone"] = phone
        return data


class PersonCreateForm(PersonForm):
    """Yeni kişi; isteğe bağlı olarak bir daireye malik/kiracı olarak bağlanır."""

    unit = forms.ModelChoiceField(
        Unit.unscoped.none(), label="Daire", required=False, empty_label="— Şimdilik bağlama —"
    )
    relation = forms.ChoiceField(label="İlişki", choices=Occupancy.Relation.choices, initial="owner")
    start_date = forms.DateField(label="Başlangıç", required=False, widget=DateInput())
    is_liable = forms.BooleanField(label="Borç sorumlusu", required=False, initial=True)

    class Meta(PersonForm.Meta):
        pass

    def __init__(self, *args, site, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].queryset = Unit.objects.filter(site=site, is_archived=False).select_related("block")
        self.fields["start_date"].hide_optional = True
