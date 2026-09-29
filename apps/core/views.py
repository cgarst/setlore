import json
import secrets
from django.conf import settings
from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpResponseRedirect
from django.contrib import messages
from django.urls import reverse
from django.db.models import Count
from .forms import CaseInsensitiveUserCreationForm
from .models import UserProfile, SiteSetting, Friendship
from . import oauth
from apps.catalog.models import Artist, Venue, Song
from apps.concerts.models import Concert
from apps.concerts.services.sync_worker import sync_worker

def health_check(request):
    return JsonResponse({"status": "ok"})

def privacy_view(request):
    return render(request, 'privacy.html')

def home_view(request):
    """
    Public landing page showcasing Setlore features, self-hosted open-source ethos,
    top users by concert count with public profiles, exportability, and tech stack.
    """
    top_users_qs = (
        User.objects.filter(profile__is_public=True, is_active=True)
        .annotate(concert_count=Count('concerts', distinct=True))
        .filter(concert_count__gt=0)
        .select_related('profile')
        .order_by('-concert_count', 'username')[:12]
    )

    top_users = []
    for rank, u in enumerate(top_users_qs, start=1):
        top_artist_record = (
            Concert.objects.filter(user=u)
            .values('primary_artist')
            .annotate(shows=Count('id'))
            .order_by('-shows', 'primary_artist')
            .first()
        )
        top_users.append({
            'rank': rank,
            'user': u,
            'username': u.username,
            'setlistfm_username': u.profile.setlistfm_username,
            'location': u.profile.default_location,
            'concert_count': u.concert_count,
            'top_artist': top_artist_record['primary_artist'] if top_artist_record else None,
            'top_artist_shows': top_artist_record['shows'] if top_artist_record else 0,
        })

    total_public_concerts = Concert.objects.filter(user__profile__is_public=True).count()
    total_artists = Artist.objects.count()
    total_venues = Venue.objects.count()
    total_songs = Song.objects.count()

    context = {
        'top_users': top_users,
        'total_public_concerts': total_public_concerts,
        'total_artists': total_artists,
        'total_venues': total_venues,
        'total_songs': total_songs,
        'google_oauth_enabled': oauth.is_google_oauth_configured(),
    }
    return render(request, 'home.html', context)

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    registration_enabled = SiteSetting.is_registration_enabled()
    if not registration_enabled:
        if request.method == 'POST':
            messages.error(request, "New user registration is currently disabled by an administrator.")
        return render(request, 'registration/register.html', {
            'registration_enabled': False,
            'form': None,
            'google_oauth_enabled': oauth.is_google_oauth_configured(),
        })

    if request.method == 'POST':
        form = CaseInsensitiveUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user, backend='apps.core.backends.CaseInsensitiveModelBackend')
            return redirect('dashboard')
    else:
        form = CaseInsensitiveUserCreationForm()

    return render(request, 'registration/register.html', {
        'registration_enabled': True,
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

    except PermissionError as pe:
        messages.error(request, str(pe))
        return redirect('login')
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


# ---------------------------------------------------------
# Admin Impersonation Views
# ---------------------------------------------------------

@login_required
@require_POST
def impersonate_user_view(request):
    """Allows staff/admins to impersonate another user account."""
    is_admin = request.user.is_staff or request.user.is_superuser or bool(request.session.get('impersonator_id'))
    if not is_admin:
        messages.error(request, "Permission denied. Only administrators can impersonate users.")
        return redirect('dashboard')

    user_id = request.POST.get('user_id')
    username = request.POST.get('username')

    target_user = None
    if user_id:
        target_user = User.objects.filter(id=user_id).first()
    elif username:
        target_user = User.objects.filter(username__iexact=username.strip()).first()

    if not target_user:
        messages.error(request, "Target user not found.")
        return redirect('dashboard')

    if target_user.id == request.user.id:
        messages.info(request, "You are already signed in as this user.")
        return redirect('dashboard')

    # Save initial admin identity in session
    impersonator_id = request.session.get('impersonator_id') or request.user.id

    login(request, target_user, backend='apps.core.backends.CaseInsensitiveModelBackend')
    request.session['impersonator_id'] = impersonator_id

    messages.warning(request, f"Now impersonating @{target_user.username}. You can exit impersonation anytime.")
    return redirect('dashboard')


@login_required
@require_POST
def stop_impersonating_view(request):
    """Exits impersonation and restores the original administrator account."""
    impersonator_id = request.session.pop('impersonator_id', None)
    if not impersonator_id:
        messages.info(request, "You are not currently impersonating any user.")
        return redirect('dashboard')

    admin_user = User.objects.filter(id=impersonator_id).first()
    if not admin_user:
        messages.error(request, "Original admin user could not be found.")
        return redirect('login')

    login(request, admin_user, backend='apps.core.backends.CaseInsensitiveModelBackend')
    messages.success(request, f"Exited impersonation. Logged back in as admin @{admin_user.username}.")
    return redirect('dashboard')


@login_required
@require_POST
def toggle_registration_view(request):
    """Allows staff to toggle registration enabled/disabled."""
    if not request.user.is_staff and not request.user.is_superuser:
        return JsonResponse({"status": "error", "error": "Permission denied."}, status=403)

    setting = SiteSetting.get_settings()
    if request.content_type == 'application/json':
        try:
            data = json.loads(request.body.decode('utf-8'))
            if 'enabled' in data:
                setting.registration_enabled = bool(data['enabled'])
            else:
                setting.registration_enabled = not setting.registration_enabled
        except Exception:
            setting.registration_enabled = not setting.registration_enabled
    else:
        setting.registration_enabled = not setting.registration_enabled

    setting.save()
    return JsonResponse({
        "status": "ok",
        "registration_enabled": setting.registration_enabled,
        "message": f"Registration is now {'enabled' if setting.registration_enabled else 'disabled'}."
    })


# ---------------------------------------------------------
# Friends / Community Views
# ---------------------------------------------------------

@login_required
@require_POST
def toggle_friend_view(request):
    """Adds or removes a user from friends list."""
    try:
        if request.content_type == 'application/json':
            data = json.loads(request.body.decode('utf-8'))
        else:
            data = request.POST

        friend_id = data.get('friend_id')
        friend_username = data.get('username')

        target_user = None
        if friend_id:
            target_user = User.objects.filter(id=friend_id).first()
        elif friend_username:
            target_user = User.objects.filter(username__iexact=str(friend_username).lstrip('@').strip()).first()

        if not target_user:
            return JsonResponse({"status": "error", "error": "User not found."}, status=404)

        if target_user.id == request.user.id:
            return JsonResponse({"status": "error", "error": "You cannot add yourself as a friend."}, status=400)

        existing = Friendship.objects.filter(user=request.user, friend=target_user).first()
        if existing:
            existing.delete()
            is_friend = False
            msg = f"Removed @{target_user.username} from friends."
        else:
            Friendship.objects.create(user=request.user, friend=target_user)
            is_friend = True
            msg = f"Added @{target_user.username} as friend!"

        friends_count = Friendship.objects.filter(user=request.user).count()
        return JsonResponse({
            "status": "ok",
            "is_friend": is_friend,
            "friend_id": target_user.id,
            "friend_username": target_user.username,
            "friends_count": friends_count,
            "message": msg
        })
    except Exception as e:
        return JsonResponse({"status": "error", "error": str(e)}, status=400)


@login_required
def list_friends_view(request):
    """Returns the authenticated user's friends and community suggestions."""
    friend_ids = list(Friendship.objects.filter(user=request.user).values_list('friend_id', flat=True))
    friends = list(User.objects.filter(id__in=friend_ids).order_by('username').values('id', 'username', 'email'))
    for f in friends:
        f['concert_count'] = Concert.objects.filter(user_id=f['id']).count()
        f['is_friend'] = True

    # User suggestions (public users or existing users)
    other_users = list(
        User.objects.exclude(id=request.user.id)
        .exclude(id__in=friend_ids)
        .order_by('username')
        .values('id', 'username')
    )
    for u in other_users:
        u['concert_count'] = Concert.objects.filter(user_id=u['id']).count()
        u['is_friend'] = False

    return JsonResponse({
        "status": "ok",
        "friends": friends,
        "suggestions": other_users[:20],
        "total_friends": len(friends),
    })
