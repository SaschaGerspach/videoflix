from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

User = get_user_model()

# Falls der User schon vom Default-Admin registriert wurde → erst deregistrieren
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # Spalten in der Liste
    list_display = (
        "id", "email", "username", "is_active", "is_staff",
        "is_superuser", "last_login", "date_joined",
    )
    list_display_links = ("id", "email")
    list_editable = ("is_active",)  # direkt in der Liste toggeln
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("email", "username")
    ordering = ("-date_joined",)

    @admin.action(description="Ausgewählte Benutzer aktivieren")
    def activate_users(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="Ausgewählte Benutzer deaktivieren")
    def deactivate_users(self, request, queryset):
        queryset.update(is_active=False)

    actions = ["activate_users", "deactivate_users"]
