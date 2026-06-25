from django.utils import timezone


class UpdateLastSeenMiddleware:
    """
    Middleware qui met à jour last_seen à chaque requête
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            request.user.last_seen = timezone.now()
            request.user.save(update_fields=['last_seen'])
        response = self.get_response(request)
        return response

# class Redirect404Middleware:

#     def __init__(self, get_response):
#         self.get_response = get_response

#     def __call__(self, request):
#         response = self.get_response(request)

#         if response.status_code == 404:
#             return redirect('utilisateur:notfound')

#         return response

