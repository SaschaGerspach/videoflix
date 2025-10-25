from django.conf import settings
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class CookieJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "accounts.domain.authentication.CookieJWTAuthentication"
    name = "cookieJwtAuth"
    priority = 1

    def get_security_definition(self, auto_schema):
        access_cookie = getattr(settings, "ACCESS_COOKIE_NAME", "access_token")
        refresh_cookie = getattr(settings, "REFRESH_COOKIE_NAME", "refresh_token")
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": access_cookie,
            "description": (
                f"Authentication via HttpOnly cookie `{access_cookie}`. "
                f"Optional refresh cookie `{refresh_cookie}` may be provided."
            ),
        }
