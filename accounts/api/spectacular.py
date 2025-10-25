# accounts/api/spectacular.py
from rest_framework import serializers


class RegistrationRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()
    confirmed_password = serializers.CharField()


class LoginRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordConfirmRequestSerializer(serializers.Serializer):
    new_password = serializers.CharField()
    confirm_password = serializers.CharField()


class ActivationRequestSerializer(serializers.Serializer):
    uidb64 = serializers.CharField()
    token = serializers.CharField()
