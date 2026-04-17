from datetime import datetime

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from academics.models import Department, Program

from .models import Admin, Faculty, Student, User


@receiver(post_save, sender=User)
def create_profile_and_identifiers(sender, instance, created, **kwargs):
    if not created:
        return

    with transaction.atomic():
        if instance.auth_user_id is None:
            auth_model = get_user_model()
            base_username = (instance.email or instance.name or "user").strip().lower().replace(" ", "")
            if not base_username:
                base_username = f"user{instance.pk}"

            username = base_username
            counter = 1
            while auth_model.objects.filter(username=username).exists():
                username = f"{base_username}{counter}"
                counter += 1

            auth_user = auth_model(username=username, email=(instance.email or ""))
            auth_user.set_password("test123")
            auth_user.save()
            instance.auth_user = auth_user
            instance.save(update_fields=["auth_user"])

        if instance.user_type == User.UserType.STUDENT:
            program = Program.objects.order_by("degree", "branch").first()
            if program is None:
                return

            student, _ = Student.objects.get_or_create(
                user=instance,
                defaults={
                    "admission_year": datetime.now().year,
                    "program": program,
                    "current_semester": 1,
                    "academic_status": "ONGOING",
                    "curr_cpi": 0,
                    "backlog_count": 0,
                },
            )
            if not student.roll_number:
                student.save()

            generated_email = f"{student.roll_number.lower()}@student.university.edu"
            if not instance.email:
                User.objects.filter(pk=instance.pk).update(email=generated_email)
                instance.email = generated_email

        elif instance.user_type == User.UserType.FACULTY:
            department = Department.objects.order_by("department_name").first()
            if department is None:
                return

            faculty, _ = Faculty.objects.get_or_create(
                user=instance,
                defaults={
                    "designation": "Faculty",
                    "department": department,
                    "experience": 0,
                    "qualification": "Not Specified",
                },
            )
            if not faculty.faculty_id:
                faculty.save()

            generated_email = f"{faculty.faculty_id.lower()}@faculty.university.edu"
            if not instance.email:
                User.objects.filter(pk=instance.pk).update(email=generated_email)
                instance.email = generated_email

        elif instance.user_type == User.UserType.ADMIN:
            admin_profile, _ = Admin.objects.get_or_create(
                user=instance,
                defaults={
                    "role": "ADMIN",
                },
            )
            if not admin_profile.admin_id:
                admin_profile.save()

            generated_email = f"{admin_profile.admin_id.lower()}@admin.university.edu"
            if not instance.email:
                User.objects.filter(pk=instance.pk).update(email=generated_email)
                instance.email = generated_email
