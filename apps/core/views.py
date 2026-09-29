import json
import secrets
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponseRedirect
from django.contrib import messages
from django.urls import reverse
from .forms import CaseInsensitiveUserCreationForm
from .models import UserProfile
from . import oauth
from apps.concerts.services.sync_worker import sync_worker

def health_check(request):
    return JsonResponse({"status": "ok"})

def privacy_view(request):
    return render(request, 'privacy.html')

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = CaseInsensitiveUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            setlist_user = request.POST.get('setlistfm_username', '').strip()
            if setlist_user.startswith('@'):
                setlist_user = setlist_user[1:].strip()
            if setlist_user:
                profile = user.profile
                profile.setlistfm_username = setlist_user
                profile.save()
            login(request, user, backend='apps.core.backends.CaseInsensitiveModelBackend')
            return redirect('dashboard')
    else:
        form = CaseInsensitiveUserCreationForm()
    return render(request, 'registration/register.html', {
        'form': form,
        'google_oauth_enabled': oauth.is_google_oauth_configured(),
    })

def google_login_view(request):
    """Initiates Google OAuth authorization flow for sign in, registration, or account mapping."""
    if not oauth.is_google_oauth_configured():
        messages.error(request, "Google Sign-In is not configured yet. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET environment variables.")
        if request.user.is_authenticated:
            return redirect('dashboard')
        return redirect('login')

    state = secrets.token_urlsafe(32)
    request.session['google_oauth_state'] = state
    next_url = request.GET.get('next', '')
    if next_url:
        request.session['google_oauth_next'] = next_url

    auth_url = oauth.build_authorization_url(request, state)
    return redirect(auth_url)

def google_callback_view(request):
    """Handles OAuth callback from Google."""
    if not oauth.is_google_oauth_configured():
        messages.error(request, "Google Sign-In is not configured.")
        return redirect('login')

    error = request.GET.get('error')
    if error:
        messages.error(request, f"Google authentication was cancelled or failed: {error}")
        return redirect('dashboard' if request.user.is_authenticated else 'login')

    session_state = request.session.pop('google_oauth_state', None)
    req_state = request.GET.get('state', '')
    if not session_state or session_state != req_state:
        messages.error(request, "Authentication session expired or state mismatch. Please try again.")
        return redirect('dashboard' if request.user.is_authenticated else 'login')

    code = request.GET.get('code')
    if not code:
        messages.error(request, "No authorization code provided by Google.")
        return redirect('dashboard' if request.user.is_authenticated else 'login')

    try:
        token_data = oauth.exchange_code_for_token(request, code)
        access_token = token_data.get('access_token')
        if not access_token:
            raise ValueError("No access token returned from Google.")

        user_info = oauth.fetch_google_user_info(access_token)
        google_sub = user_info.get('sub')
        google_email = user_info.get('email')
        name = user_info.get('name')

        if not google_sub or not google_email:
            raise ValueError("Google account did not return required profile or email data.")

        # Case 1: User is already logged in -> Link/Map Google Account
        if request.user.is_authenticated:
            success, msg = oauth.link_google_account_to_user(request.user, google_sub, google_email)
            if success:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            next_url = request.session.pop('google_oauth_next', None) or 'dashboard'
            return redirect(next_url)

        # Case 2: User is not logged in -> Authenticate, Map existing by email, or Register
        user, created, action = oauth.authenticate_or_register_google_user(google_sub, google_email, name)
        login(request, user, backend='apps.core.backends.CaseInsensitiveModelBackend')

        if created:
            messages.success(request, f"Welcome to Setlore, @{user.username}! Your account has been registered with Google ({google_email}).")
        elif action == 'mapped_by_email':
            messages.success(request, f"Welcome back, @{user.username}! Your Google account ({google_email}) has been linked to your existing account.")
        else:
            messages.success(request, f"Welcome back, @{user.username}!")

        next_url = request.session.pop('google_oauth_next', None) or 'dashboard'
        return redirect(next_url)

    except Exception as e:
        messages.error(request, f"Failed to complete Google Sign-In: {str(e)}")
        return redirect('dashboard' if request.user.is_authenticated else 'login')

@login_required
@require_POST
def google_disconnect_view(request):
    """Unlinks / unmaps Google account from the authenticated user."""
    success, msg = oauth.unlink_google_account_from_user(request.user)
    
    if request.content_type == 'application/json' or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            "status": "ok" if success else "error",
            "message": msg,
            "has_google_linked": bool(request.user.profile.google_id),
            "google_email": request.user.profile.google_email,
        })

    if success:
        messages.success(request, msg)
    else:
        messages.error(request, msg)
    return redirect('dashboard')

@login_required
@require_POST
def update_profile_view(request):
    try:
        if request.content_type == 'application/json':
            data = json.loads(request.body.decode('utf-8'))
        else:
            data = request.POST

        profile = request.user.profile
        update_fields = []

        if 'email' in data:
            new_email = str(data.get('email', '')).strip().lower()
            if new_email and new_email != request.user.email.lower():
                from django.contrib.auth.models import User
                if User.objects.filter(email__iexact=new_email).exclude(pk=request.user.pk).exists():
                    return JsonResponse({"status": "error", "error": "This email address is already associated with another account."}, status=400)
            if new_email != request.user.email:
                request.user.email = new_email
                request.user.save(update_fields=['email'])

        if 'setlistfm_username' in data:
            username = str(data.get('setlistfm_username', '')).strip()
            if username.startswith('@'):
                username = username[1:].strip()
            profile.setlistfm_username = username
            update_fields.append('setlistfm_username')

        if 'setlistfm_api_key' in data:
            api_key = str(data.get('setlistfm_api_key', '')).strip()
            profile.setlistfm_api_key = api_key
            update_fields.append('setlistfm_api_key')

        if 'prompt_setlistfm' in data:
            val = data.get('prompt_setlistfm')
            if isinstance(val, str):
                prompt_bool = val.lower() in ('true', '1', 'yes')
            else:
                prompt_bool = bool(val)
            profile.prompt_setlistfm = prompt_bool
            update_fields.append('prompt_setlistfm')

        if 'is_public' in data:
            val = data.get('is_public')
            if isinstance(val, str):
                is_pub_bool = val.lower() in ('true', '1', 'yes', 'on')
            else:
                is_pub_bool = bool(val)
            profile.is_public = is_pub_bool
            update_fields.append('is_public')

        if update_fields:
            update_fields.append('updated_at')
            profile.save(update_fields=update_fields)

        sync_queued = False
        should_sync = data.get('trigger_sync', False)
        if isinstance(should_sync, str):
            should_sync = should_sync.lower() in ('true', '1', 'yes')
        
        if should_sync:
            sync_worker.enqueue_sync(request.user.id)
            sync_queued = True

        return JsonResponse({
            "status": "ok",
            "message": "Profile updated successfully.",
            "profile": {
                "username": request.user.username,
                "email": request.user.email,
                "setlistfm_username": profile.setlistfm_username,
                "prompt_setlistfm": profile.prompt_setlistfm,
                "is_public": profile.is_public,
                "has_custom_api_key": bool(profile.setlistfm_api_key),
                "has_google_linked": bool(profile.google_id),
                "google_email": profile.google_email,
            },
            "sync_queued": sync_queued
        })
    except Exception as e:
        return JsonResponse({"status": "error", "error": str(e)}, status=400)


