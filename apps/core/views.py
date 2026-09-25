from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.http import JsonResponse
from .models import UserProfile

def health_check(request):
    return JsonResponse({"status": "ok"})

def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            setlist_user = request.POST.get('setlistfm_username', '').strip()
            if setlist_user:
                profile = user.profile
                profile.setlistfm_username = setlist_user
                profile.save()
            login(request, user)
            return redirect('dashboard')
    else:
        form = UserCreationForm()
    return render(request, 'registration/register.html', {'form': form})
