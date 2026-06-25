from django.contrib import admin
from django.urls import path, include
from django.conf.urls.static import static
from django.conf import settings
from django.shortcuts import render
from .views import (import_folder_view,force_email_check_view,reindex_qdrant_view,test_imap_view,mes_traitements_view, traitement_terminer_view, traitement_annuler_view,mark_all_notifications_read,mark_notification_read,create_agent,delete_candidat_view,view_upload_cv,dashboard_view,settings_view, message_view, compte_view,view_agents,analyse_view,repport_view, document_view)

urlpatterns = [
    path("delior_sup_admin/", admin.site.urls,name="delior_sup_admin"),
    path("", dashboard_view, name="dashboard"),
    path("repport/", repport_view, name="repport"),
    path("", include(("candidat.urls", "candidat"), namespace="candidat")),
    path("login/", include(("utilisateur.urls", "utilisateur"), namespace="utilisateur")),
    path("settings/", settings_view, name="settings"),
    path("message/", message_view, name="message"),
    path('agents/',view_agents,  name='agents'),
    path('agents/create/', create_agent, name='create_agent'),
    path("analyse/", analyse_view, name="analyse"),
    path("compte/", compte_view, name="compte"),
    path('document/',document_view,name='document'),
    path('upload/',view_upload_cv,name='upload'),
    path('delete/<uuid:candidat_id>/', delete_candidat_view, name='delete_candidat'),
    path('notifications/mark-read/<uuid:notif_id>/', mark_notification_read, name='mark_notification_read'),
    path('notifications/mark-all-read/', mark_all_notifications_read, name='mark_all_notifications_read'),
    path('mes-traitements/', mes_traitements_view, name='mes_traitements'),
    path('mes-traitements/terminer/<uuid:candidat_id>/', traitement_terminer_view, name='traitement_terminer'),
    path('mes-traitements/annuler/<uuid:candidat_id>/', traitement_annuler_view, name='traitement_annuler'),
    path('parametres/test-imap/',  test_imap_view,         name='test_imap'),
    path('parametres/reindex/',    reindex_qdrant_view,    name='reindex_qdrant_view'),
    path('parametres/force-email/', force_email_check_view, name='force_email_check'),
    path('import-folder/', import_folder_view, name='import_folder'),
    path('mobile-not-supported/', lambda request: render(request, 'not_found.html'), name='mobile_not_supported'),
]

handler404 = 'utilisateur.views.notfound_view'

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
