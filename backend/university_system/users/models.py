from datetime import datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class User(models.Model):
    class UserType(models.TextChoices):
        STUDENT = "STUDENT", "Student"
        FACULTY = "FACULTY", "Faculty"
        ADMIN = "ADMIN", "Admin"

    user_id = models.AutoField(primary_key=True)
    auth_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    name = models.CharField(max_length=150)
    email = models.EmailField(unique=True, blank=True, null=True)
    phone = models.CharField(max_length=50)
    dob = models.DateField()
    gender = models.CharField(
        max_length=20,
        choices=[
            ("Male", "Male"),
            ("Female", "Female"),
            ("Other", "Other"),
        ],
    )
    user_type = models.CharField(max_length=50, choices=UserType.choices)

    def __str__(self):
        return f"{self.name} ({self.user_type})"


class Student(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    roll_number = models.CharField(max_length=20, unique=True)
    admission_year = models.PositiveSmallIntegerField(validators=[MinValueValidator(2000)])
    program = models.ForeignKey(
        "academics.Program",
        on_delete=models.PROTECT
    )
    current_semester = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    academic_status = models.CharField(max_length=50)
    curr_cpi = models.DecimalField(max_digits=4, decimal_places=2, validators=[MinValueValidator(0), MaxValueValidator(10)])
    backlog_count = models.PositiveSmallIntegerField(default=0)

    def clean(self):
        if self.user.user_type != User.UserType.STUDENT:
            raise ValidationError("User must be of type STUDENT")

    def _generate_roll_number(self):
        current_year = datetime.now().year % 100
        branch_raw = (self.program.branch if self.program_id and self.program.branch else "CSE")
        branch_code = "".join(ch for ch in branch_raw.upper() if ch.isalnum())[:2] or "CS"
        sequence = Student.objects.exclude(pk=self.pk).count() + 1

        while True:
            roll_number = f"{current_year:02d}{branch_code}{sequence:03d}"
            if not Student.objects.filter(roll_number=roll_number).exists():
                return roll_number
            sequence += 1

    def save(self, *args, **kwargs):
        self.clean()
        if not self.roll_number and self.program_id:
            self.roll_number = self._generate_roll_number()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Student: {self.user.name}"


class Faculty(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    faculty_id = models.CharField(max_length=20, unique=True)
    designation = models.CharField(max_length=100)
    department = models.ForeignKey(
        "academics.Department",
        on_delete=models.PROTECT,
        db_column="department_id",
    )
    experience = models.DecimalField(max_digits=4, decimal_places=1)
    qualification = models.CharField(max_length=255)

    def clean(self):
        if self.user.user_type != User.UserType.FACULTY:
            raise ValidationError("User must be of type FACULTY")

    def _generate_faculty_id(self):
        sequence = Faculty.objects.exclude(pk=self.pk).count() + 1
        while True:
            faculty_id = f"FAC{sequence:03d}"
            if not Faculty.objects.filter(faculty_id=faculty_id).exists():
                return faculty_id
            sequence += 1

    def save(self, *args, **kwargs):
        self.clean()
        if not self.faculty_id:
            self.faculty_id = self._generate_faculty_id()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Faculty: {self.user.name}"


class Admin(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, primary_key=True)
    admin_id = models.CharField(max_length=20, unique=True)
    role = models.CharField(max_length=100)

    def clean(self):
        if self.user.user_type != User.UserType.ADMIN:
            raise ValidationError("User must be of type ADMIN")

    def _generate_admin_id(self):
        sequence = Admin.objects.exclude(pk=self.pk).count() + 1
        while True:
            admin_id = f"ADM{sequence:03d}"
            if not Admin.objects.filter(admin_id=admin_id).exists():
                return admin_id
            sequence += 1

    def save(self, *args, **kwargs):
        self.clean()
        if not self.admin_id:
            self.admin_id = self._generate_admin_id()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Admin: {self.user.name}"
