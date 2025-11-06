from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_https_login_sets_secure_cookies_and_cors_headers():
    user_model = get_user_model()
    user = user_model.objects.create_user(
        username="https-user",
        email="https-user@example.com",
        password="strong-password",
    )

    client = APIClient()
    response = client.post(
        "/api/login/",
        {"email": user.email, "password": "strong-password"},
        format="json",
        secure=True,
        HTTP_ORIGIN=settings.DEV_FRONTEND_ORIGIN,
    )

    assert response.status_code == 200
    access_cookie = response.cookies["access_token"]
    refresh_cookie = response.cookies["refresh_token"]

    assert bool(access_cookie["secure"])
    assert bool(refresh_cookie["secure"])
    assert access_cookie["samesite"].lower() == "none"
    assert refresh_cookie["samesite"].lower() == "none"

    assert response["Access-Control-Allow-Origin"] == settings.DEV_FRONTEND_ORIGIN
    assert response["Access-Control-Allow-Credentials"] == "true"
