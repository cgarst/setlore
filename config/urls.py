from django.contrib import admin
from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views
from apps.core import views as core_views
from apps.concerts import views as concerts_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', core_views.health_check, name='health_check'),

    # Authentication
    path('accounts/login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('accounts/logout/', auth_views.LogoutView.as_view(next_page='login'), name='logout'),
    path('accounts/register/', core_views.register_view, name='register'),
    path('accounts/password_change/', auth_views.PasswordChangeView.as_view(
        template_name='registration/password_change_form.html',
        success_url=reverse_lazy('password_change_done'),
    ), name='password_change'),
    path('accounts/password_change/done/', auth_views.PasswordChangeDoneView.as_view(
        template_name='registration/password_change_done.html',
    ), name='password_change_done'),

    # Concert Dashboard & API
    path('', concerts_views.dashboard_view, name='dashboard'),
    path('api/sync/', concerts_views.trigger_sync, name='api_sync'),
    path('api/sync/status/', concerts_views.sync_status, name='api_sync_status'),
    path('api/upload-csv/', concerts_views.upload_csv, name='api_upload_csv'),
    path('api/concerts/add/', concerts_views.add_concert, name='api_add_concert'),
]
