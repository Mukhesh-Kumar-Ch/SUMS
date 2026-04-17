from django.db import migrations, models, transaction


def backfill_role_ids(apps, schema_editor):
    User = apps.get_model("users", "User")
    Student = apps.get_model("users", "Student")
    Faculty = apps.get_model("users", "Faculty")
    Admin = apps.get_model("users", "Admin")

    with transaction.atomic():
        for student in Student.objects.select_related("user").all():
            if not student.roll_number:
                student.roll_number = f"STU{student.user_id:04d}"
                student.save(update_fields=["roll_number"])
            if student.roll_number and not student.user.email:
                student.user.email = f"{student.roll_number.lower()}@university.edu"
                student.user.save(update_fields=["email"])

        for faculty in Faculty.objects.select_related("user").all():
            if not faculty.faculty_id:
                faculty.save()
            if faculty.faculty_id and not faculty.user.email:
                faculty.user.email = f"{faculty.faculty_id.lower()}@university.edu"
                faculty.user.save(update_fields=["email"])

        for admin_profile in Admin.objects.select_related("user").all():
            if not admin_profile.admin_id:
                admin_profile.save()
            if admin_profile.admin_id and not admin_profile.user.email:
                admin_profile.user.email = f"{admin_profile.admin_id.lower()}@university.edu"
                admin_profile.user.save(update_fields=["email"])


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0002_admin_admin_id_faculty_faculty_id_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_role_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="student",
            name="roll_number",
            field=models.CharField(blank=True, max_length=20, unique=True),
        ),
        migrations.AlterField(
            model_name="faculty",
            name="faculty_id",
            field=models.CharField(blank=True, max_length=20, unique=True),
        ),
        migrations.AlterField(
            model_name="admin",
            name="admin_id",
            field=models.CharField(blank=True, max_length=20, unique=True),
        ),
    ]
