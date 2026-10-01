from datetime import date
from django.db.models import Q
from django.contrib.auth.models import User
from .models import SiteSetting
from apps.concerts.models import Concert

def core_context(request):
    registration_enabled = SiteSetting.is_registration_enabled()
    impersonator_id = request.session.get('impersonator_id') if hasattr(request, 'session') else None
    is_impersonating = bool(impersonator_id)
    impersonator = None
    if is_impersonating:
        impersonator = User.objects.filter(id=impersonator_id).first()

    all_users_for_impersonate = []
    user_concerts_count = 0
    should_show_onboarding = False

    if hasattr(request, 'user') and request.user.is_authenticated:
        today = date.today()
        user_concerts_count = Concert.objects.filter(user=request.user).filter(Q(date__isnull=True) | Q(date__lte=today)).count()
        sync_status = getattr(getattr(request.user, 'profile', None), 'sync_status', 'idle')
        should_show_onboarding = (user_concerts_count == 0 and sync_status != 'syncing')

        if request.user.is_staff or is_impersonating:
            all_users_for_impersonate = list(
                User.objects.exclude(id=request.user.id)
                .order_by('username')
                .values('id', 'username', 'email', 'is_staff')
            )

    return {
        'registration_enabled': registration_enabled,
        'is_impersonating': is_impersonating,
        'impersonator': impersonator,
        'all_users_for_impersonate': all_users_for_impersonate,
        'user_concerts_count': user_concerts_count,
        'should_show_onboarding': should_show_onboarding,
    }
