from django.urls import path
from django.conf.urls.static import static
from django.conf import settings
from .views import (verify_otp_view,forgot_password_view,approb_user,get_info_user,view_login_user, view_logout, view_register_user,notfound_view,setpassword,updateinfosuser,setprofile)

app_name = "utilisateur"

urlpatterns = [
    path("", view_login_user, name="login"),
    path("logout/", view_logout, name="logout"),
    path('verify-otp/', verify_otp_view, name='verify_otp'),
    path("register/", view_register_user, name="register"),
    path("notfound/",notfound_view,name="notfound"),
    path("setpassword/",setpassword,name="setpassword"),
    path("updateinfosuser/",updateinfosuser,name="updateinfosuser"),
    path("setprofile/",setprofile,name="setprofile"),
    path("get_info_user/",get_info_user,name="get_info_user"),
    path("approb_user/",approb_user,name="approb_user"),
    path('forgot-password/', forgot_password_view, name='forgot_password')
] 

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
