import binascii
import logging
import secrets
from datetime import datetime, timedelta, UTC
from typing import Any
import jwt
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.auth.tokens import default_token_generator
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.html import strip_tags
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode
from jwt import InvalidTokenError

from accounts.domain.utils import build_logo_url, normalize_email

logger = logging.getLogger("videoflix")


def create_inactive_user(email: str, password: str) -> AbstractBaseUser:
    """Create an inactive user account with provided credentials."""
    user_model = get_user_model()
    return user_model.objects.create_user(
        username=email,
        email=email,
        password=password,
        is_active=False,
    )


def send_activation_email(
    user, *, fail_silently: bool = False
) -> str | tuple[str, bool]:
    """Send an activation email and return the generated token.

    When ``fail_silently`` is True the token is returned together with a boolean
    flag indicating if the email was delivered instead of raising on failures.
    """
    token = default_token_generator.make_token(user)
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    base_url = (
        getattr(settings, "PUBLIC_API_BASE", None)
        or getattr(settings, "FRONTEND_BASE_URL", None)
        or "http://127.0.0.1:8000/api"
    )
    action_url = f"{base_url.rstrip('/')}/activate/{uidb64}/{token}/"
    context = _email_context(user=user, action_url=action_url)

    try:
        _send_multipart_email(
            subject="Activate your Videoflix account",
            template_base="email/activation_email",
            context=context,
            recipient=normalize_email(user.email),
        )
        delivered = True
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning(
            "Activation email delivery failed: user_id=%s, email=%s, error=%s",
            getattr(user, "pk", None),
            getattr(user, "email", None),
            exc,
        )
        delivered = False
        if not fail_silently:
            raise

    if fail_silently:
        return token, delivered
    return token


def send_password_reset_email(
    email: str, *, fail_silently: bool = False
) -> str | tuple[str, bool]:
    """Send a password reset email to the user and return generated token."""
    user_model = get_user_model()
    normalized_email = normalize_email(email)
    user = user_model.objects.get(email__iexact=normalized_email)

    token = default_token_generator.make_token(user)
    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    frontend_base = getattr(settings, "DEV_FRONTEND_ORIGIN", "http://localhost:5500")
    reset_link = (
        f"{frontend_base.rstrip('/')}/pages/auth/confirm_password.html"
        f"?uid={uidb64}&token={token}"
    )
    # Example: http://localhost:5500/pages/auth/confirm_password.html?uid=ABC123&token=XYZ

    context = _email_context(user=user, action_url=reset_link)
    try:
        _send_multipart_email(
            subject="Reset your Videoflix password",
            template_base="email/password_reset_email",
            context=context,
            recipient=normalized_email,
        )
        delivered = True
    except Exception as exc:  # pragma: no cover - defensive guard
        logger.warning(
            "Password reset email delivery failed: user_id=%s, email=%s, error=%s",
            getattr(user, "pk", None),
            getattr(user, "email", None),
            exc,
        )
        delivered = False
        if not fail_silently:
            raise

    if fail_silently:
        return token, delivered
    return token


def confirm_password_reset(
    uidb64: str, token: str, new_password: str
) -> AbstractBaseUser:
    """Validate reset token and update the user's password."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
    except (TypeError, ValueError, OverflowError, binascii.Error):
        raise ValidationError({"uidb64": ["Invalid password reset link."]})

    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=uid)
    except user_model.DoesNotExist:
        raise ValidationError({"uidb64": ["Invalid password reset link."]})

    if not default_token_generator.check_token(user, token):
        raise ValidationError({"token": ["Invalid or expired password reset token."]})

    user.set_password(new_password)
    user.save(update_fields=["password"])
    revoke_all_refresh_tokens_for_user(user)
    return user


ACCESS_LIFETIME_SECONDS = getattr(settings, "JWT_ACCESS_LIFETIME_SECONDS", 900)
REFRESH_LIFETIME_SECONDS = getattr(settings, "JWT_REFRESH_LIFETIME_SECONDS", 1209600)

ACCESS_TOKEN_LIFETIME = timedelta(seconds=ACCESS_LIFETIME_SECONDS)
REFRESH_TOKEN_LIFETIME = timedelta(seconds=REFRESH_LIFETIME_SECONDS)

REFRESH_BLACKLIST_KEY_PREFIX = "jwt:refresh:blacklist:"
_USER_REFRESH_REVOKE_KEY = "jwt:refresh:revoke_before:{user_id}"


class AuthenticationError(ValidationError):
    """Raised when authentication fails with additional context."""

    def __init__(self, message_dict: dict[str, list[str]], *, reason: str):
        super().__init__(message_dict)
        self.reason = reason


def revoke_all_refresh_tokens_for_user(
    user: AbstractBaseUser, *, ttl_seconds: int | None = None
) -> None:
    """Store revoke timestamp for user refresh tokens."""
    revoke_before = int(datetime.now(UTC).timestamp())
    timeout = ttl_seconds if ttl_seconds is not None else REFRESH_LIFETIME_SECONDS
    cache.set(
        _USER_REFRESH_REVOKE_KEY.format(user_id=user.pk),
        revoke_before,
        timeout=timeout,
    )


def _generate_token(
    user: AbstractBaseUser, lifetime: timedelta, token_type: str
) -> tuple[str, datetime]:
    issued_at = datetime.now(UTC)
    expires_at = issued_at + lifetime
    issued_at_epoch = int(issued_at.timestamp())
    expires_at_epoch = int(expires_at.timestamp())
    payload = {
        "user_id": user.pk,
        "username": user.username,
        "type": token_type,
        "jti": secrets.token_urlsafe(12),
        "iat": issued_at_epoch,
        "exp": expires_at_epoch,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    return token, expires_at


def login_user(email: str, password: str) -> tuple[AbstractBaseUser, dict[str, object]]:
    """Authenticate the user and return the user with generated JWT tokens."""
    user_model = get_user_model()
    try:
        user = user_model.objects.get(email__iexact=email)
    except user_model.DoesNotExist:
        raise AuthenticationError(
            {"non_field_errors": ["Invalid credentials."]}, reason="invalid_credentials"
        )

    if not user.is_active:
        raise AuthenticationError(
            {"non_field_errors": ["Account is inactive."]}, reason="inactive"
        )

    authenticated_user = authenticate(username=user.username, password=password)
    if authenticated_user is None:
        raise AuthenticationError(
            {"non_field_errors": ["Invalid credentials."]}, reason="invalid_credentials"
        )

    access_token, access_expires = _generate_token(
        authenticated_user, ACCESS_TOKEN_LIFETIME, "access"
    )
    refresh_token, refresh_expires = _generate_token(
        authenticated_user, REFRESH_TOKEN_LIFETIME, "refresh"
    )

    token_payload = {
        "access": access_token,
        "access_expires": access_expires,
        "refresh": refresh_token,
        "refresh_expires": refresh_expires,
        "access_max_age": ACCESS_LIFETIME_SECONDS,
        "refresh_max_age": REFRESH_LIFETIME_SECONDS,
    }

    return authenticated_user, token_payload


def refresh_access_token(refresh_token: str) -> dict[str, object]:
    """Validate refresh token and issue a new access token."""
    payload = _decode_refresh_token(refresh_token)
    _ensure_refresh_token_is_valid(payload)

    jti = payload["jti"]
    if _is_refresh_jti_blacklisted(jti):
        raise ValidationError({"refresh_token": ["Invalid or expired refresh token."]})

    user_id = payload.get("user_id")
    if user_id is None:
        raise ValidationError({"refresh_token": ["Invalid refresh token."]})

    user_model = get_user_model()
    try:
        user = user_model.objects.get(pk=user_id)
    except user_model.DoesNotExist:
        raise ValidationError({"refresh_token": ["Invalid refresh token."]})

    if not user.is_active:
        raise ValidationError({"refresh_token": ["Invalid refresh token."]})

    revoke_before_key = _USER_REFRESH_REVOKE_KEY.format(user_id=user.pk)
    revoke_before = cache.get(revoke_before_key)
    if revoke_before is not None:
        try:
            token_iat_epoch = int(payload.get("iat", 0))
        except (TypeError, ValueError):
            token_iat_epoch = None
        try:
            revoke_before_value = int(revoke_before)
        except (TypeError, ValueError):
            revoke_before_value = None
        if (
            token_iat_epoch is not None
            and revoke_before_value is not None
            and token_iat_epoch <= revoke_before_value
        ):
            raise ValidationError(
                {"refresh_token": ["Invalid or expired refresh token."]}
            )

    access_token, access_expires = _generate_token(
        user, ACCESS_TOKEN_LIFETIME, "access"
    )
    return {
        "access": access_token,
        "access_expires": access_expires,
        "access_max_age": ACCESS_LIFETIME_SECONDS,
    }


def _email_context(*, user: AbstractBaseUser, action_url: str) -> dict[str, Any]:
    project_name = getattr(settings, "PROJECT_NAME", "Videoflix")
    user_name = getattr(user, "username", None) or getattr(user, "email", "")
    return {
        "user_name": user_name,
        "action_url": action_url,
        "project_name": project_name,
        "logo_url": build_logo_url(),
    }


def _send_multipart_email(
    *,
    subject: str,
    template_base: str,
    context: dict[str, Any],
    recipient: str,
) -> None:
    text_body, html_body = _render_email_bodies(template_base, context)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
    kwargs: dict[str, Any] = {}
    if from_email:
        kwargs["reply_to"] = [from_email]

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=[recipient],
        **kwargs,
    )
    email.attach_alternative(html_body, "text/html")
    email.send()


def _render_email_bodies(
    template_base: str, context: dict[str, Any]
) -> tuple[str, str]:
    html_template = (
        f"{template_base}.html"
        if not template_base.endswith(".html")
        else template_base
    )
    html_body = render_to_string(html_template, context)
    text_template = (
        f"{template_base}.txt"
        if not template_base.endswith(".html")
        else html_template[:-5] + ".txt"
    )
    try:
        text_body = render_to_string(text_template, context)
    except TemplateDoesNotExist:
        text_body = strip_tags(html_body)
        action_url = context.get("action_url")
        if action_url and action_url not in text_body:
            text_body = f"{text_body.strip()}\n\n{action_url}"
    return text_body, html_body


def logout_user(refresh_token: str | None) -> None:
    """Blacklist the provided refresh token."""
    if not refresh_token:
        raise ValidationError({"refresh_token": ["Refresh token cookie missing."]})

    payload = _decode_refresh_token(refresh_token)
    _ensure_refresh_token_is_valid(payload)

    jti = payload["jti"]
    if _is_refresh_jti_blacklisted(jti):
        raise ValidationError({"refresh_token": ["Token already invalidated."]})

    user_id = payload.get("user_id")
    if user_id is None:
        raise ValidationError({"refresh_token": ["Invalid refresh token."]})

    user_model = get_user_model()
    if not user_model.objects.filter(pk=user_id).exists():
        raise ValidationError({"refresh_token": ["Invalid refresh token."]})

    expires_at = datetime.fromtimestamp(payload["exp"], tz=UTC)
    ttl = max(int((expires_at - datetime.now(UTC)).total_seconds()), 0)
    cache.set(_refresh_blacklist_key(jti), True, timeout=ttl or None)


def is_refresh_token_blacklisted(refresh_token: str) -> bool:
    """Check if the given refresh token has already been blacklisted."""
    try:
        payload = _decode_refresh_token(refresh_token, verify_exp=False)
    except ValidationError:
        return False

    jti = payload.get("jti")
    if not jti:
        return False

    return _is_refresh_jti_blacklisted(jti)


def _decode_refresh_token(
    refresh_token: str, *, verify_exp: bool = True
) -> dict[str, object]:
    try:
        return jwt.decode(
            refresh_token,
            settings.SECRET_KEY,
            algorithms=["HS256"],
            options={"verify_exp": verify_exp},
            leeway=getattr(settings, "JWT_LEEWAY", 0),
        )
    except InvalidTokenError as exc:
        raise ValidationError({"refresh_token": ["Invalid refresh token."]}) from exc


def _ensure_refresh_token_is_valid(payload: dict[str, object]) -> None:
    if payload.get("type") != "refresh":
        raise ValidationError({"refresh_token": ["Invalid refresh token."]})
    if not payload.get("jti"):
        raise ValidationError({"refresh_token": ["Invalid refresh token."]})


def _refresh_blacklist_key(jti: str) -> str:
    return f"{REFRESH_BLACKLIST_KEY_PREFIX}{jti}"


def _is_refresh_jti_blacklisted(jti: str) -> bool:
    return bool(cache.get(_refresh_blacklist_key(jti)))


def activate_user(*, uidb64: str, token: str) -> None:
    """Activate a user using uidb64 and token."""
    user = _get_user_from_uidb64(uidb64)

    if user.is_active:
        raise ValidationError({"non_field_errors": ["Account already active."]})

    if not default_token_generator.check_token(user, token):
        raise ValidationError(
            {"non_field_errors": ["Invalid or expired activation token."]}
        )

    user.is_active = True
    user.save(update_fields=["is_active"])


def _get_user_from_uidb64(uidb64: str):
    """Decode uidb64 and return the corresponding user or raise ValidationError with non-field errors."""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
    except Exception:
        raise ValidationError({"non_field_errors": ["Invalid activation link."]})

    if not uid or not uid.isdigit():
        raise ValidationError({"non_field_errors": ["Invalid activation link."]})

    User = get_user_model()
    try:
        return User.objects.get(pk=int(uid))
    except User.DoesNotExist:
        raise ValidationError({"non_field_errors": ["Invalid activation link."]})
