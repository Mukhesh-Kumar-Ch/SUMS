from django.db import migrations, models
import django.db.models.deletion


def backfill_auth_user(apps, schema_editor):
    User = apps.get_model("users", "User")
    AuthUser = apps.get_model("auth", "User")

    for profile in User.objects.all():
        if profile.auth_user_id:
            continue

        email_value = (profile.email or "").strip().lower()
        if not email_value:
            email_value = f"user{profile.user_id}@university.edu"
            profile.email = email_value

        base_username = email_value.split("@")[0] if "@" in email_value else email_value
        if not base_username:
            base_username = f"user{profile.user_id}"

        username = base_username
        counter = 1
        while AuthUser.objects.filter(username=username).exists():
            username = f"{base_username}{counter}"
            counter += 1

        auth_user = AuthUser(username=username, email=email_value)
        auth_user.set_unusable_password()
        auth_user.save()

        profile.auth_user_id = auth_user.id
        profile.save(update_fields=["email", "auth_user"])


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0003_backfill_role_ids"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.RunPython(backfill_auth_user, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="user",
            name="password",
        ),
        migrations.AlterField(
            model_name="user",
            name="auth_user",
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="profile", to="auth.user"),
        ),
    ]
