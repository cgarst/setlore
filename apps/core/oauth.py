import logging
import re
import secrets
import urllib.parse
from django.conf import settings
from django.contrib.auth.models import User
from django.urls import reverse
import requests

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def is_google_oauth_configured():
    """Returns True if Google Client ID and Secret are configured."""
    return bool(getattr(settings, 'GOOGLE_CLIENT_ID', None) and getattr(settings, 'GOOGLE_CLIENT_SECRET', None))


def get_redirect_uri(request):
    """
    Returns the absolute Google OAuth redirect URI.
    Can be overridden via GOOGLE_REDIRECT_URI environment variable / setting.
    """
    configured_uri = getattr(settings, 'GOOGLE_REDIRECT_URI', '').strip()
    if configured_uri:
        return configured_uri
    return request.build_absolute_uri(reverse('google_callback'))


def build_authorization_url(request, state, prompt='select_account'):
    """Constructs the Google OAuth authorization redirect URL."""
    client_id = settings.GOOGLE_CLIENT_ID
    redirect_uri = get_redirect_uri(request)

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
    }
    if prompt:
        params['prompt'] = prompt

    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_token(request, code):
    """Exchanges an authorization code for Google access and ID tokens."""
    client_id = settings.GOOGLE_CLIENT_ID
    client_secret = settings.GOOGLE_CLIENT_SECRET
    redirect_uri = get_redirect_uri(request)

    data = {
        'code': code,
        'client_id': client_id,
        'client_secret': client_secret,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }
    response = requests.post(GOOGLE_TOKEN_URL, data=data, timeout=10)
    if not response.ok:
        logger.error(f"Google token exchange failed: {response.status_code} {response.text}")
        raise ValueError(f"Failed to exchange code with Google: {response.text}")

    return response.json()


def fetch_google_user_info(access_token):
    """Fetches user profile and email information from Google UserInfo endpoint."""
    headers = {'Authorization': f'Bearer {access_token}'}
    response = requests.get(GOOGLE_USERINFO_URL, headers=headers, timeout=10)
    if not response.ok:
        logger.error(f"Google userinfo request failed: {response.status_code} {response.text}")
        raise ValueError("Failed to fetch user info from Google")

    return response.json()


def generate_unique_username(email, name=None):
    """Generates a clean, unique, case-insensitive username for a new user."""
    base_candidate = ""
    if email and "@" in email:
        base_candidate = email.split("@")[0].strip().lower()
    elif name:
        base_candidate = name.strip().lower()

    # Clean characters (alphanumeric and underscore)
    base = re.sub(r'[^a-zA-Z0-9_]', '', base_candidate)[:25]
    if not base:
        base = "user"

    candidate = base
    counter = 1
    while User.objects.filter(username__iexact=candidate).exists():
        counter += 1
        candidate = f"{base}_{counter}"

    return candidate


def authenticate_or_register_google_user(sub, email, name=None):
    """
    Handles sign in and registration via Google OAuth:
    1. Looks up existing user mapped to this Google sub (google_id).
    2. If not found, looks up existing user by email address (case-insensitive) and maps them.
    3. If not found, creates and registers a new User account with Google credentials.

    Returns: (user, created, action_type)
    action_type can be 'existing_google_id', 'mapped_by_email', or 'new_registration'
    """
    if not sub or not email:
        raise ValueError("Google user info must contain 'sub' (ID) and 'email'.")

    email = email.strip().lower()

    # 1. Match by google_id
    existing_profile_user = User.objects.filter(profile__google_id=sub).first()
    if existing_profile_user:
        # Keep profile email in sync
        if existing_profile_user.profile.google_email != email:
            existing_profile_user.profile.google_email = email
            existing_profile_user.profile.save(update_fields=['google_email'])
        if not existing_profile_user.email:
            existing_profile_user.email = email
            existing_profile_user.save(update_fields=['email'])
        return existing_profile_user, False, 'existing_google_id'

    # 2. Match by email address (case-insensitive)
    existing_email_user = User.objects.filter(email__iexact=email).first()
    if not existing_email_user:
        # Also check profile google_email
        existing_email_user = User.objects.filter(profile__google_email__iexact=email).first()

    if existing_email_user:
        # Map Google account to this existing user
        profile = existing_email_user.profile
        profile.google_id = sub
        profile.google_email = email
        profile.save(update_fields=['google_id', 'google_email'])
        if not existing_email_user.email:
            existing_email_user.email = email
            existing_email_user.save(update_fields=['email'])
        return existing_email_user, False, 'mapped_by_email'

    # 3. Create brand new user
    username = generate_unique_username(email, name)
    user = User.objects.create_user(username=username, email=email)
    user.set_unusable_password()
    user.save()

    profile = user.profile
    profile.google_id = sub
    profile.google_email = email
    profile.save(update_fields=['google_id', 'google_email'])

    return user, True, 'new_registration'


def link_google_account_to_user(user, sub, email):
    """
    Links a Google account to an already-authenticated user.
    Returns: (success, message)
    """
    if not sub or not email:
        return False, "Invalid Google account data received."

    email = email.strip().lower()

    # Check if another user already has this google_id
    existing = User.objects.filter(profile__google_id=sub).exclude(pk=user.pk).first()
    if existing:
        return False, f"This Google account ({email}) is already connected to another user (@{existing.username})."

    profile = user.profile
    profile.google_id = sub
    profile.google_email = email
    profile.save(update_fields=['google_id', 'google_email'])

    if not user.email:
        user.email = email
        user.save(update_fields=['email'])

    return True, f"Google account ({email}) successfully connected!"


def unlink_google_account_from_user(user):
    """
    Unlinks/unmaps the Google account from the given user.
    Returns: (success, message)
    """
    profile = user.profile
    if not profile.google_id:
        return False, "No Google account is currently linked."

    profile.google_id = None
    profile.google_email = ''
    profile.save(update_fields=['google_id', 'google_email'])

    return True, "Google account successfully disconnected."
