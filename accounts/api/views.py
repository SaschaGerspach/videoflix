
from django.conf import settings
from django.core.exceptions import ValidationError
from rest_framework import status
from rest_framework.decorators import (api_view, authentication_classes,
                                       permission_classes, throttle_classes)
from rest_framework.exceptions import ParseError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from accounts.api.serializers import (ActivationSerializer, LoginSerializer,
                                      LogoutSerializer,
                                      PasswordConfirmSerializer,
                                      PasswordResetSerializer,
                                      RegistrationSerializer,
                                      TokenRefreshSerializer,
                                      format_validation_error)
from accounts.api.spectacular import (
    ActivationRequestSerializer,
    LoginRequestSerializer,
    PasswordConfirmRequestSerializer,
    PasswordResetRequestSerializer,
    RegistrationRequestSerializer,
)
from accounts.domain.services import (AuthenticationError, activate_user,
                                      confirm_password_reset,
                                      create_inactive_user, login_user,
                                      logout_user, refresh_access_token,
                                      send_activation_email,
                                      send_password_reset_email)
from drf_spectacular.utils import OpenApiExample, extend_schema


ERROR_RESPONSE_REF = {"$ref": "#/components/schemas/ErrorResponse"}


def _base_cookie_kwargs(request):
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
        kwargs["samesite"] = getattr(settings, "DEV_COOKIE_SAMESITE", kwargs["samesite"])

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
                    "properties": {"id": {"type": "integer"}, "email": {"type": "string"}},
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
                    "properties": {"id": {"type": "integer"}, "username": {"type": "string"}},
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
    serializer = LoginSerializer(request.data)

    if not serializer.is_valid():
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data

    try:
        user, tokens = login_user(
            email=data["email"], password=data["password"])
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

    access_cookie_kwargs = _base_cookie_kwargs(request)
    access_cookie_kwargs["max_age"] = tokens["access_max_age"]

    refresh_cookie_kwargs = _base_cookie_kwargs(request)
    refresh_cookie_kwargs["max_age"] = tokens["refresh_max_age"]

    response.set_cookie(
        "access_token", tokens["access"], **access_cookie_kwargs)
    response.set_cookie(
        "refresh_token", tokens["refresh"], **refresh_cookie_kwargs)

    return response


login.throttle_scope = "login"
if hasattr(login, "cls"):
    login.cls.throttle_scope = "login"


@extend_schema(
    tags=["Auth"],
    request=None,
    responses={200: {"type": "object", "properties": {
        "detail": {"type": "string"}}}},
    auth=[{"cookieJwtAuth": []}],
)
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
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
    deletion_kwargs = _base_cookie_kwargs(request)
    deletion_kwargs["max_age"] = 0

    response.set_cookie("access_token", "", **deletion_kwargs)
    response.set_cookie("refresh_token", "", **deletion_kwargs)
    return response


@extend_schema(
    tags=["Auth"],
    request=None,
    responses={
        200: {"type": "object",
              "properties": {"detail": {"type": "string"}, "access": {"type": "string"}}},
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
        return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

    email = serializer.validated_data["email"]
    send_password_reset_email(email=email)

    return Response(
        {"detail": "An email has been sent to reset your password."},
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
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def post(self, request):
        try:
            data = request.data
        except ParseError as exc:
            return Response(
                {"errors": {"non_field_errors": [str(exc)]}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = ActivationSerializer(data)
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)

        try:
            activate_user(**serializer.validated_data)
        except ValidationError as exc:
            return Response({"errors": format_validation_error(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"message": "Account activated."}, status=status.HTTP_200_OK)
