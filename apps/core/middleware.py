from .context import reset_request_actor, set_request_actor


def client_ip(request) -> str | None:
    # Ters vekil (proxy) arkasında X-Forwarded-For yalnızca güvenilen vekil ayarlıysa kullanılmalı;
    # şimdilik doğrudan bağlantı adresi.
    return request.META.get("REMOTE_ADDR")


class RequestContextMiddleware:
    """İşlemi yapan kullanıcı ve IP'yi denetim izi için bağlama koyar."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        tokens = set_request_actor(user.pk if user and user.is_authenticated else None, client_ip(request))
        try:
            return self.get_response(request)
        finally:
            reset_request_actor(tokens)
