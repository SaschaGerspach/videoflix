
from django.conf import settings
from django.core.exceptions import ValidationError

from accounts.api.serializers import (
    ActivationSerializer,
    LoginSerializer,
    LogoutSerializer,
    RegistrationSerializer,
    PasswordResetSerializer,
    PasswordConfirmSerializer,
    TokenRefreshSerializer,
    format_validation_error,
)
from accounts.domain.services import (
    AuthenticationError,
    activate_user,
    create_inactive_user,
    confirm_password_reset,
    login_user,
    logout_user,
    send_password_reset_email,
    refresh_access_token,
    send_activation_email,
)
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.exceptions import ParseError


@api_view(["POST"])
def register(request):
    # request.data enthält bereits das geparste JSON
    serializer = RegistrationSerializer(request.data)

    if not serializer.is_valid():
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    user = create_inactive_user(email=data["email"], password=data["password"])
    token = send_activation_email(user)

    return Response(
        {"user": {"id": user.pk, "email": user.email}, "token": token},
        status=status.HTTP_201_CREATED,
    )


@api_view(["POST"])
@throttle_classes([ScopedRateThrottle])
def login(request):
    serializer = LoginSerializer(request.data)

    if not serializer.is_valid():
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data

    try:
        user, tokens = login_user(email=data["email"], password=data["password"])
    except AuthenticationError as exc:
        status_code = status.HTTP_403_FORBIDDEN if exc.reason == "inactive" else status.HTTP_400_BAD_REQUEST
        return Response({"errors": format_validation_error(exc)}, status=status_code)

    response = Response(
        {
            "detail": "Login successful",
            "user": {"id": user.pk, "username": user.username},
        },
        status=status.HTTP_200_OK,
    )

    secure_cookie = bool(getattr(settings, "SESSION_COOKIE_SECURE", False))
    path = getattr(settings, "SESSION_COOKIE_PATH", "/")
    domain = getattr(settings, "SESSION_COOKIE_DOMAIN", None)
    samesite = getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax")

    access_cookie_kwargs = {
        "httponly": True,
        "secure": secure_cookie,
        "samesite": samesite,
        "path": path,
        "max_age": tokens["access_max_age"],
    }
    refresh_cookie_kwargs = {
        "httponly": True,
        "secure": secure_cookie,
        "samesite": samesite,
        "path": path,
        "max_age": tokens["refresh_max_age"],
    }
    if domain:
        access_cookie_kwargs["domain"] = domain
        refresh_cookie_kwargs["domain"] = domain

    response.set_cookie("access_token", tokens["access"], **access_cookie_kwargs)
    response.set_cookie("refresh_token", tokens["refresh"], **refresh_cookie_kwargs)

    return response


login.throttle_scope = "login"
if hasattr(login, "cls"):
    login.cls.throttle_scope = "login"


@api_view(["POST"])
def logout_view(request):
    try:
        data = request.data
    except ParseError as exc:
        return Response(
            {"errors": {"non_field_errors": [str(exc)]}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = LogoutSerializer(data)
    if not serializer.is_valid():
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    refresh_token = request.COOKIES.get("refresh_token")
    try:
        logout_user(refresh_token)
    except ValidationError as exc:
        return Response({"errors": format_validation_error(exc)}, status=status.HTTP_400_BAD_REQUEST)

    response = Response(
        {
            "detail": "Logout successful! All tokens will be deleted. Refresh token is now invalid."
        },
        status=status.HTTP_200_OK,
    )
    secure_cookie = bool(getattr(settings, "SESSION_COOKIE_SECURE", False))
    path = getattr(settings, "SESSION_COOKIE_PATH", "/")
    domain = getattr(settings, "SESSION_COOKIE_DOMAIN", None)
    samesite = getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax")
    deletion_kwargs = {
        "httponly": True,
        "secure": secure_cookie,
        "samesite": samesite,
        "path": path,
        "max_age": 0,
    }
    if domain:
        deletion_kwargs["domain"] = domain

    response.set_cookie("access_token", "", **deletion_kwargs)
    response.set_cookie("refresh_token", "", **deletion_kwargs)
    return response


@api_view(["POST"])
def token_refresh(request):
    try:
        data = request.data
    except ParseError as exc:
        return Response(
            {"errors": {"non_field_errors": [str(exc)]}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = TokenRefreshSerializer(data)
    if not serializer.is_valid():
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    refresh_token = request.COOKIES.get("refresh_token")
    if not refresh_token:
        return Response(
            {"errors": {"refresh_token": ["Refresh token cookie missing."]}},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        token_data = refresh_access_token(refresh_token)
    except ValidationError as exc:
        return Response(
            {"errors": format_validation_error(exc)},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    response = Response(
        {"detail": "Token refreshed", "access": token_data["access"]},
        status=status.HTTP_200_OK,
    )

    secure_cookie = bool(getattr(settings, "SESSION_COOKIE_SECURE", False))
    path = getattr(settings, "SESSION_COOKIE_PATH", "/")
    domain = getattr(settings, "SESSION_COOKIE_DOMAIN", None)
    samesite = getattr(settings, "SESSION_COOKIE_SAMESITE", "Lax")
    cookie_kwargs = {
        "httponly": True,
        "secure": secure_cookie,
        "samesite": samesite,
        "path": path,
        "max_age": token_data["access_max_age"],
    }
    if domain:
        cookie_kwargs["domain"] = domain

    response.set_cookie("access_token", token_data["access"], **cookie_kwargs)
    return response


@api_view(["POST"])
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
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    email = serializer.validated_data["email"]
    send_password_reset_email(email=email)

    return Response(
        {"detail": "An email has been sent to reset your password."},
        status=status.HTTP_200_OK,
    )


@api_view(["POST"])
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
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    try:
        confirm_password_reset(
            uidb64=uidb64,
            token=token,
            new_password=serializer.validated_data["new_password"],
        )
    except ValidationError as exc:
        return Response({"errors": format_validation_error(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        {"detail": "Your Password has been successfully reset."},
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
def activate(request, uidb64: str, token: str):
    serializer = ActivationSerializer({"uidb64": uidb64, "token": token})

    if not serializer.is_valid():
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data

    try:
        activate_user(uidb64=data["uidb64"], token=data["token"])
    except ValidationError as exc:
        return Response({"errors": format_validation_error(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response({"message": "Account successfully activated."}, status=status.HTTP_200_OK)
