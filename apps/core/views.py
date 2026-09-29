import json
from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from .forms import CaseInsensitiveUserCreationForm
from .models import UserProfile
from apps.concerts.services.sync_worker import sync_worker

def health_check(request):
    return JsonResponse({"status": "ok"})

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
            login(request, user)
            return redirect('dashboard')
    else:
        form = CaseInsensitiveUserCreationForm()
    return render(request, 'registration/register.html', {'form': form})

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
                "setlistfm_username": profile.setlistfm_username,
                "prompt_setlistfm": profile.prompt_setlistfm,
                "has_custom_api_key": bool(profile.setlistfm_api_key),
            },
            "sync_queued": sync_queued
        })
    except Exception as e:
        return JsonResponse({"status": "error", "error": str(e)}, status=400)

