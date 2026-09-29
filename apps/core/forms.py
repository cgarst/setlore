from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

User = get_user_model()


class CaseInsensitiveUserCreationForm(UserCreationForm):
    """
    User registration form that captures email and ensures username & email uniqueness is case-insensitive.
    """
    email = forms.EmailField(
        required=False,
        label="Email address",
        help_text="Used for account recovery and automatic Google Sign-In mapping"
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")

    def clean_username(self):
        username = self.cleaned_data.get("username")
        if username:
            query = User.objects.filter(username__iexact=username)
            if self.instance.pk:
                query = query.exclude(pk=self.instance.pk)
            if query.exists():
                raise forms.ValidationError(
                    "A user with that username already exists.",
                    code="duplicate_username",
                )
        return username

    def clean_email(self):
        email = self.cleaned_data.get("email")
        if email:
            email = email.strip().lower()
            query = User.objects.filter(email__iexact=email)
            if self.instance.pk:
                query = query.exclude(pk=self.instance.pk)
            if query.exists():
                raise forms.ValidationError(
                    "An account with this email address already exists.",
                    code="duplicate_email",
                )
        return email
