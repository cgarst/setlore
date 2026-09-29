from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm

User = get_user_model()


class CaseInsensitiveUserCreationForm(UserCreationForm):
    """
    User registration form that ensures username uniqueness is case-insensitive.
    """

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
