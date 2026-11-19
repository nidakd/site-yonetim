from django import forms
from django.contrib.auth.forms import SetPasswordForm as DjangoSetPasswordForm


class LoginForm(forms.Form):
    email = forms.EmailField(
        label="E-posta", widget=forms.EmailInput(attrs={"autocomplete": "username", "autofocus": True})
    )
    password = forms.CharField(
        label="Şifre", strip=False, widget=forms.PasswordInput(attrs={"autocomplete": "current-password"})
    )


class TokenForm(forms.Form):
    token = forms.CharField(
        label="Doğrulama kodu",
        max_length=16,
        widget=forms.TextInput(
            attrs={"autocomplete": "one-time-code", "inputmode": "numeric", "autofocus": True, "placeholder": "123456"}
        ),
    )

    remember = forms.BooleanField(
        label="Bu cihazı 30 gün hatırla",
        required=False,
        help_text="Bu cihazdan girişte yalnızca şifre sorulur. Ortak kullanılan bilgisayarlarda işaretlemeyin.",
    )

    def clean_token(self) -> str:
        return "".join(self.cleaned_data["token"].split()).replace("-", "")


class SetPasswordForm(DjangoSetPasswordForm):
    """Django'nun formu; etiketler uygulamanın diliyle ("şifre") tutarlı."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = "Yeni şifre"
        self.fields["new_password2"].label = "Yeni şifre (tekrar)"
        self.fields["new_password2"].help_text = ""
