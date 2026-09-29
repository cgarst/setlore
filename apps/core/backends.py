from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

UserModel = get_user_model()


class CaseInsensitiveModelBackend(ModelBackend):
    """
    Custom authentication backend that allows case-insensitive username lookups.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if username is None or password is None:
            return None

        try:
            user = UserModel._default_manager.get(**{f"{UserModel.USERNAME_FIELD}__iexact": username})
        except UserModel.DoesNotExist:
            # Run the default password hasher once to reduce timing attacks
            UserModel().set_password(password)
            return None
        except UserModel.MultipleObjectsReturned:
            # If multiple matching accounts exist, authenticate with any matching password
            users = UserModel._default_manager.filter(**{f"{UserModel.USERNAME_FIELD}__iexact": username})
            for u in users:
                if u.check_password(password) and self.user_can_authenticate(u):
                    return u
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
