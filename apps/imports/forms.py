from django import forms
from django.utils import timezone

from apps.core.forms import DateInput, MoneyField, MonthField

from .models import ImportJob
from .xlsx import MAX_BYTES


class UploadForm(forms.Form):
    kind = forms.ChoiceField(label="Dosya türü", choices=ImportJob.Kind.choices, widget=forms.RadioSelect)
    file = forms.FileField(label="Excel dosyası (.xlsx)")
    opening_date = forms.DateField(
        label="Açılış tarihi",
        required=False,
        widget=DateInput(),
        help_text="Devreden borç ve avanslar bu tarihe yazılır; genellikle sisteme geçmeden önceki son gün.",
    )
    start_date = forms.DateField(
        label="Malik/kiracı başlangıç tarihi",
        widget=DateInput(),
        help_text="Dosyadaki kişilerin bölümle ilişkisi bu tarihten başlar. Sonradan bölüm sayfasından değişebilir.",
    )
    lock = forms.BooleanField(label="Açılış tarihine kadar olan dönemi kilitle", required=False, initial=True)
    unit_column = forms.CharField(
        label="Bölüm numarası sütununun başlığı", max_length=60, required=False, initial="Daire No"
    )
    first_month = MonthField(
        label="İlk ay sütunu", required=False, help_text="Ay başlıklarında yıl yoksa (yalnızca 'Ocak') gerekli."
    )
    sheet_name = forms.CharField(
        label="Sayfa adı", max_length=60, required=False, help_text="Dosyada birden çok sayfa varsa (ör. aidat 2026)."
    )
    charge_type = forms.ChoiceField(
        label="Borç türü",
        choices=[("Aidat", "Aidat"), ("Yakıt", "Yakıt"), ("Özel toplama", "Özel toplama"), ("Diğer", "Diğer")],
        initial="Aidat",
        required=False,
    )
    create_people = forms.BooleanField(
        label="Yeni dairelere AD/SOYAD sütunundaki kişiyi sorumlu olarak ekle", required=False, initial=True
    )
    monthly_fee = MoneyField(
        label="Aylık aidat",
        required=False,
        help_text="Bölüm tipinin aidatı girilmemiş bölümler için kullanılır.",
    )

    def __init__(self, *args, **kwargs):
        today = timezone.localdate()
        kwargs.setdefault("initial", {}).update(
            {
                "kind": ImportJob.Kind.SETUP,
                "start_date": today.replace(day=1),
                "lock": True,
                "unit_column": "Daire No",
                "charge_type": "Aidat",
                "create_people": True,
            }
        )
        super().__init__(*args, **kwargs)
        self.fields["kind"].widget.attrs["x-model"] = "kind"
        self.fields["file"].widget.attrs["accept"] = ".xlsx"

    def clean_file(self):
        f = self.cleaned_data["file"]
        if not f.name.lower().endswith(".xlsx"):
            raise forms.ValidationError(
                "Dosya .xlsx biçiminde olmalı (Excel'de 'Farklı kaydet → Excel Çalışma Kitabı')."
            )
        if f.size > MAX_BYTES:
            raise forms.ValidationError("Dosya 5 MB'tan büyük olamaz.")
        return f

    def options(self) -> dict:
        d = self.cleaned_data
        return {
            "opening_date": d["opening_date"].isoformat() if d.get("opening_date") else None,
            "start_date": d["start_date"].isoformat(),
            "lock": bool(d.get("lock")),
            "unit_column": (d.get("unit_column") or "").strip() or "Daire No",
            "first_month": d["first_month"].isoformat() if d.get("first_month") else None,
            "monthly_fee": d.get("monthly_fee"),
            "sheet_name": (d.get("sheet_name") or "").strip(),
            "charge_type": d.get("charge_type") or "Aidat",
            "create_people": bool(d.get("create_people")),
        }
