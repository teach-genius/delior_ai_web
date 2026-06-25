from django.urls import path
from django.conf.urls.static import static
from django.conf import settings
from .views import (view_cv_view,view_rapport,cv_math_view,chat_bot_view,gen_repport_view)

app_name = "candidat"

urlpatterns = [
    path("cv_math/",cv_math_view,name="cv_math"),
    path("chat_bot/",chat_bot_view,name="chat_bot"),
    path("gen_repport/",gen_repport_view,name="gen_repport"),
    path("view_rapport/",view_rapport,name="view_rapport"),
    path("view_cv/",view_cv_view,name="view_cv"),
]
urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
