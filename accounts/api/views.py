import logging

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.utils.html import escape
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
    throttle_classes,
)
from rest_framework.exceptions import ParseError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.api.serializers import (
    ActivationSerializer,
    LoginSerializer,
    LogoutSerializer,
    PasswordConfirmSerializer,
    PasswordResetSerializer,
    RegistrationSerializer,
    TokenRefreshSerializer,
    format_validation_error,
)
from accounts.api.spectacular import (
    ActivationRequestSerializer,
    LoginRequestSerializer,
    PasswordConfirmRequestSerializer,
    PasswordResetRequestSerializer,
    RegistrationRequestSerializer,
)
from accounts.domain.services import (
    AuthenticationError,
    activate_user,
    confirm_password_reset,
    create_inactive_user,
    login_user,
    logout_user,
    refresh_access_token,
    send_activation_email,
    send_password_reset_email,
)
from accounts.domain.utils import resolve_auth_frontend_base
from drf_spectacular.utils import OpenApiExample, extend_schema

logger = logging.getLogger("videoflix")


ERROR_RESPONSE_REF = {"$ref": "#/components/schemas/ErrorResponse"}


def _base_cookie_kwargs(request):
    """Build base cookie settings honoring session and dev overrides."""
    path = getattr(settings, "SESSION_COOKIE_PATH", "/")
    domain = getattr(settings, "SESSION_COOKIE_DOMAIN", None)
    kwargs = {
        "httponly": True,
        "secure": bool(getattr(settings, "SESSION_COOKIE_SECURE", False)),
        "samesite": getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax"),
        "path": path,
    }
    if domain:
        kwargs["domain"] = domain

    if request.is_secure():
        kwargs["secure"] = getattr(settings, "DEV_COOKIE_SECURE", kwargs["secure"])
        kwargs["samesite"] = getattr(
            settings, "DEV_COOKIE_SAMESITE", kwargs["samesite"]
        )

    return kwargs


@extend_schema(
    tags=["Auth"],
    request=RegistrationRequestSerializer,
    responses={
        201: {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "email": {"type": "string"},
                    },
                    "required": ["id", "email"],
                },
                "token": {"type": "string"},
            },
            "required": ["user", "token"],
        },
        400: ERROR_RESPONSE_REF,
    },
    auth=[],
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def register(request):
    # request.data already contains the parsed JSON payload.
    serializer = RegistrationSerializer(request.data)

    if not serializer.is_valid():
        return Response(
            {"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
        )

    data = serializer.validated_data
    user = create_inactive_user(email=data["email"], password=data["password"])
    token, email_sent = send_activation_email(user, fail_silently=True)
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))

    response_data = {
        "user": {"id": user.pk, "email": user.email},
        "uidb64": uidb64,
        "token": token,
    }
    if not email_sent:
        logger.warning("Activation email suppressed after creating user_id=%s", user.pk)

    return Response(response_data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["Auth"],
    request=LoginRequestSerializer,
    responses={
        200: {
            "type": "object",
            "properties": {
                "detail": {"type": "string"},
                "user": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "username": {"type": "string"},
                    },
                },
            },
        },
        400: ERROR_RESPONSE_REF,
        401: ERROR_RESPONSE_REF,
        403: ERROR_RESPONSE_REF,
    },
    auth=[],
    examples=[
        OpenApiExample(
            "LoginSuccess",
            value={
                "detail": "Login successful",
                "user": {"id": 1, "username": "user"},
            },
            response_only=True,
        ),
        OpenApiExample(
            "LoginError",
            value={"errors": {"non_field_errors": ["Invalid credentials."]}},
            response_only=True,
            status_codes=["400"],
        ),
    ],
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def login(request):
    """Authenticate a user and set access/refresh cookies on success."""
    serializer = LoginSerializer(request.data)

    if not serializer.is_valid():
        return Response(
            {"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
        )

    data = serializer.validated_data

    try:
        user, tokens = login_user(email=data["email"], password=data["password"])
    except AuthenticationError as exc:
        status_code = (
            status.HTTP_403_FORBIDDEN
            if exc.reason == "inactive"
            else status.HTTP_400_BAD_REQUEST
        )
        return Response({"errors": format_validation_error(exc)}, status=status_code)

    return _login_success_response(request, user, tokens)


def _login_success_response(request, user, tokens):
    """Return the successful login response with access and refresh cookies set."""
    response = Response(
        {
            "detail": "Login successful",
            "user": {"id": user.pk, "username": user.username},
        },
        status=status.HTTP_200_OK,
    )
    access_cookie_kwargs = _base_cookie_kwargs(request)
    access_cookie_kwargs["max_age"] = tokens["access_max_age"]

    refresh_cookie_kwargs = _base_cookie_kwargs(request)
    refresh_cookie_kwargs["max_age"] = tokens["refresh_max_age"]

    response.set_cookie("access_token", tokens["access"], **access_cookie_kwargs)
    response.set_cookie("refresh_token", tokens["refresh"], **refresh_cookie_kwargs)

    return response


login.throttle_scope = "login"
if hasattr(login, "cls"):
    login.cls.throttle_scope = "login"


@extend_schema(
    tags=["Auth"],
    request=None,
    responses={200: {"type": "object", "properties": {"detail": {"type": "string"}}}},
    auth=[{"cookieJwtAuth": []}],
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def logout_view(request):
    """Invalidate the refresh token and clear auth cookies."""
    try:
        data = request.data
    except ParseError as exc:
        return Response(
            {"errors": {"non_field_errors": [str(exc)]}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = LogoutSerializer(data)
    if not serializer.is_valid():
        return Response(
            {"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
        )

    refresh_token = request.COOKIES.get("refresh_token")
    try:
        logout_user(refresh_token)
    except ValidationError as exc:
        return Response(
            {"errors": format_validation_error(exc)}, status=status.HTTP_400_BAD_REQUEST
        )

    return _logout_success_response(request)


def _logout_success_response(request):
    """Build logout response clearing both access and refresh cookies."""
    response = Response(
        {
            "detail": "Logout successful! All tokens will be deleted. Refresh token is now invalid."
        },
        status=status.HTTP_200_OK,
    )
    deletion_kwargs = _base_cookie_kwargs(request)
    deletion_kwargs["max_age"] = 0

    response.set_cookie("access_token", "", **deletion_kwargs)
    response.set_cookie("refresh_token", "", **deletion_kwargs)
    return response


@extend_schema(
    tags=["Auth"],
    request=None,
    responses={
        200: {
            "type": "object",
            "properties": {"detail": {"type": "string"}, "access": {"type": "string"}},
        },
        400: ERROR_RESPONSE_REF,
        401: ERROR_RESPONSE_REF,
    },
    auth=[{"cookieJwtAuth": []}],
    examples=[
        OpenApiExample(
            "TokenRefreshSuccess",
            value={"detail": "Token refreshed", "access": "jwt-token"},
            response_only=True,
        ),
        OpenApiExample(
            "TokenRefreshError",
            value={"errors": {"refresh_token": ["Refresh token cookie missing."]}},
            response_only=True,
            status_codes=["400"],
        ),
    ],
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def token_refresh(request):
    """Issue a new access token based on a valid refresh token cookie."""
    parse_response, data = _parse_token_refresh_request(request)
    if parse_response is not None:
        return parse_response

    validation_response = _validate_token_refresh_serializer(data)
    if validation_response is not None:
        return validation_response

    refresh_token = _get_refresh_cookie(request)
    if refresh_token_response := refresh_token.get("response"):
        return refresh_token_response

    try:
        token_data = refresh_access_token(refresh_token["value"])
    except ValidationError as exc:
        return Response(
            {"errors": format_validation_error(exc)},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    return _refresh_success_response(request, token_data)


def _parse_token_refresh_request(request):
    """Parse request data for token refresh, returning (response, data) tuple."""
    try:
        return None, request.data
    except ParseError as exc:
        return (
            Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=status.HTTP_400_BAD_REQUEST,
            ),
            None,
        )


def _validate_token_refresh_serializer(data):
    """Validate the token refresh serializer, returning a Response on failure."""
    serializer = TokenRefreshSerializer(data)
    if serializer.is_valid():
        return None
    return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


def _get_refresh_cookie(request) -> dict[str, object]:
    """Return refresh cookie value or a response if missing."""
    refresh_token = request.COOKIES.get("refresh_token")
    if refresh_token:
        return {"value": refresh_token}
    return {
        "response": Response(
            {"errors": {"refresh_token": ["Refresh token cookie missing."]}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    }


def _refresh_success_response(request, token_data: dict):
    """Return refresh response and update the access cookie."""
    response = Response(
        {"detail": "Token refreshed", "access": token_data["access"]},
        status=status.HTTP_200_OK,
    )

    cookie_kwargs = _base_cookie_kwargs(request)
    cookie_kwargs["max_age"] = token_data["access_max_age"]

    response.set_cookie("access_token", token_data["access"], **cookie_kwargs)
    return response


@extend_schema(
    tags=["Auth"],
    request=PasswordResetRequestSerializer,
    responses={
        200: {"type": "object", "properties": {"detail": {"type": "string"}}},
        400: ERROR_RESPONSE_REF,
    },
    auth=[],
    examples=[
        OpenApiExample(
            "PasswordResetError",
            value={"errors": {"email": ["Email is required."]}},
            response_only=True,
            status_codes=["400"],
        ),
    ],
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def password_reset(request):
    try:
        data = request.data
    except ParseError as exc:
        return Response(
            {"errors": {"non_field_errors": [str(exc)]}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = PasswordResetSerializer(data)
    if not serializer.is_valid():
        return Response(
            {"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
        )

    email = serializer.validated_data["email"]
    token, email_sent = send_password_reset_email(email=email, fail_silently=True)
    if not email_sent:
        logger.warning(
            "Password reset email suppressed after creating reset token for email=%s",
            email,
        )

    return Response(
        {"detail": "If this email exists, a password reset link has been sent."},
        status=status.HTTP_200_OK,
    )


@extend_schema(
    tags=["Auth"],
    request=PasswordConfirmRequestSerializer,
    responses={
        200: {"type": "object", "properties": {"detail": {"type": "string"}}},
        400: ERROR_RESPONSE_REF,
    },
    auth=[],
    examples=[
        OpenApiExample(
            "PasswordConfirmError",
            value={"errors": {"confirm_password": ["Passwords do not match."]}},
            response_only=True,
            status_codes=["400"],
        ),
    ],
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def password_confirm(request, uidb64: str, token: str):
    try:
        data = request.data
    except ParseError as exc:
        return Response(
            {"errors": {"non_field_errors": [str(exc)]}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = PasswordConfirmSerializer(data)
    if not serializer.is_valid():
        return Response(
            {"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST
        )

    try:
        confirm_password_reset(
            uidb64=uidb64,
            token=token,
            new_password=serializer.validated_data["new_password"],
        )
    except ValidationError as exc:
        return Response(
            {"errors": format_validation_error(exc)}, status=status.HTTP_400_BAD_REQUEST
        )

    return Response(
        {"detail": "Your Password has been successfully reset."},
        status=status.HTTP_200_OK,
    )


@extend_schema(
    tags=["Auth"],
    request=ActivationRequestSerializer,
    responses={
        200: {"type": "object", "properties": {"detail": {"type": "string"}}},
        400: ERROR_RESPONSE_REF,
    },
    auth=[],
    examples=[
        OpenApiExample(
            "ActivationError",
            value={"errors": {"non_field_errors": ["token is required."]}},
            response_only=True,
            status_codes=["400"],
        ),
    ],
)
class ActivateAccountView(APIView):
    """Handle activation links for newly registered accounts."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(
        self, request, uidb64: str | None = None, token: str | None = None, **kwargs
    ):
        try:
            data = request.data
        except ParseError as exc:
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = dict(data or {})
        if uidb64 and "uidb64" not in payload:
            payload["uidb64"] = uidb64
        if token and "token" not in payload:
            payload["token"] = token

        success, errors = self._activate_user(payload)
        if not success:
            return Response({"errors": errors}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"message": "Account activated."}, status=status.HTTP_200_OK)

    def get(
        self, request, uidb64: str | None = None, token: str | None = None, **kwargs
    ):
        """Validate activation tokens from query params and render HTML or JSON."""
        data = self._resolve_activation_data(request, uidb64, token)
        success, errors = self._activate_user(data)

        if self._wants_html_response(request):
            return self._build_html_response(success)

        return self._build_json_response(success, errors)

    def _activate_user(self, data: dict):
        """Validate and perform activation using the given payload."""
        serializer = ActivationSerializer(data)
        if not serializer.is_valid():
            return False, serializer.errors
        try:
            activate_user(**serializer.validated_data)
        except ValidationError as exc:
            return False, format_validation_error(exc)
        return True, None

    def _get_login_url(self) -> str:
        """Return the login URL for the frontend auth page."""
        base = resolve_auth_frontend_base().rstrip("/")
        return f"{base}/pages/auth/login.html"

    def _resolve_activation_data(
        self, request, uidb64: str | None, token: str | None
    ) -> dict:
        """Gather activation params from URL path or query parameters."""
        resolved_uid = (
            uidb64
            or request.query_params.get("uid")
            or request.query_params.get("uidb64")
        )
        resolved_token = token or request.query_params.get("token")
        return {"uidb64": resolved_uid, "token": resolved_token}

    def _wants_html_response(self, request) -> bool:
        """Determine whether the client expects an HTML response."""
        accepted_format = getattr(
            getattr(request, "accepted_renderer", None), "format", None
        )
        accepts_html = "text/html" in (request.META.get("HTTP_ACCEPT") or "")
        return accepted_format == "html" or accepts_html

    def _build_html_response(self, success: bool) -> HttpResponse:
        """Render a translated HTML activation outcome."""
        login_url = self._get_login_url()
        if success:
            content = self._render_activation_result(
                title="Account erfolgreich aktiviert",
                message="Du kannst dich jetzt einloggen.",
                login_url=login_url,
            )
            return HttpResponse(
                content, status=status.HTTP_200_OK, content_type="text/html"
            )
        content = self._render_activation_result(
            title="Aktivierung fehlgeschlagen",
            message="Der Link ist ungueltig oder abgelaufen.",
            login_url=login_url,
        )
        return HttpResponse(
            content, status=status.HTTP_400_BAD_REQUEST, content_type="text/html"
        )

    def _build_json_response(self, success: bool, errors):
        """Return a JSON activation response matching prior behavior."""
        if success:
            return Response(
                {"message": "Account successfully activated."},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"errors": ["Invalid or expired activation link."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _render_activation_result(
        self, title: str, message: str, login_url: str
    ) -> str:
        """Render a minimal HTML page describing the activation outcome."""
        safe_title = escape(title)
        safe_message = escape(message)
        safe_login = escape(login_url)
        return (
            "<!DOCTYPE html>"
            "<html lang='de'>"
            "<head>"
            "<meta charset='utf-8'/>"
            "<title>Videoflix</title>"
            "<style>"
            "body{font-family:Arial,Helvetica,sans-serif;margin:40px;line-height:1.6;}"
            "h1{color:#1f2933;}"
            "p{margin:16px 0;}"
            "a.button{display:inline-block;padding:10px 16px;background:#1d4ed8;"
            "color:#fff;text-decoration:none;border-radius:4px;}"
            "</style>"
            "</head>"
            "<body>"
            f"<h1>{safe_title}</h1>"
            f"<p>{safe_message}</p>"
            f"<a class='button' href='{safe_login}'>Zum Login</a>"
            "</body>"
            "</html>"
        )
