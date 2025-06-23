# odoo_dash_project/urls.py
from django.contrib import admin
# Assurez-vous que 'include' est bien importé
from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    # URL pour l'interface d'administration Django
    path('admin/', admin.site.urls),

    # Inclut les URLs de votre application 'core' (ex: dashboard)
    # Accessibles via /app/dashboard/, etc.
    path('app/', include('core.urls', namespace='core')),

    # Inclut les URLs d'authentification standard de Django
    # Fournit /accounts/login/, /accounts/logout/, etc.
    path('accounts/', include('django.contrib.auth.urls')),

    # Redirige la racine du site vers le tableau de bord
    path('', RedirectView.as_view(pattern_name='core:dashboard', permanent=False), name='home'),
    path('accounts/', include('django.contrib.auth.urls')),

]
