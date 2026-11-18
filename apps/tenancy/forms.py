from django import forms

from .models import Site


class SiteForm(forms.ModelForm):
    class Meta:
        model = Site
        fields = ["name", "city", "district", "address"]
        widgets = {"address": forms.Textarea(attrs={"rows": 3})}

    def clean_name(self) -> str:
        name = " ".join(self.cleaned_data["name"].split())
        qs = Site.objects.filter(name__iexact=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Bu adla bir siteniz zaten var.")
        return name
