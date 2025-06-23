# core/urls.py
from django.urls import path
from . import views # Importer les vues de l'application core

app_name = 'core'

urlpatterns = [
    path('dashboard/', views.dashboard_view, name='dashboard'),
    # --- NOUVELLE URL AJOUTÉE ---
    path('trigger-fetch-indicators/', views.trigger_fetch_indicators_view, name='trigger_fetch_indicators'),
    # --- FIN NOUVELLE URL ---
]
