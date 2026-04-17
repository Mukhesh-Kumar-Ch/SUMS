from django.core.exceptions import ValidationError
from django.db import models
from academics.models import Program
from users.models import Student

# Create your models here.

class Company(models.Model):
    company_id = models.AutoField(primary_key=True)
    company_name = models.CharField(max_length=255)
    contact = models.CharField(max_length=50)
    email = models.EmailField(unique=True)
    industry_type = models.CharField(max_length=100)

    def __str__(self):
        return self.company_name

class PlacementOffer(models.Model):
    class OfferType(models.TextChoices):
        JOB = "JOB", "Job"
        INTERNSHIP = "INTERNSHIP", "Internship"
        PPO = "PPO", "PPO"

    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    role_name = models.CharField(max_length=255)
    package_ctc = models.DecimalField(max_digits=10, decimal_places=2)
    offer_type = models.CharField(
        max_length=50,
        choices=OfferType.choices,
        blank=True,
        null=True,
    )
    min_cpi = models.DecimalField(max_digits=4, decimal_places=2, default=0)
    allowed_programs = models.ManyToManyField(Program, blank=True)
    max_backlogs = models.PositiveSmallIntegerField(default=0)
    min_semester = models.PositiveSmallIntegerField(default=1)
    max_semester = models.PositiveSmallIntegerField(default=8)
    application_deadline = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=["company"], name="placement_offer_company_idx"),
        ]

    def clean(self):
        if self.min_semester > self.max_semester:
            raise ValidationError("Min semester cannot be greater than max semester")

    def __str__(self):
        if self.offer_type:
            return f"{self.role_name} ({self.offer_type}) at {self.company.company_name}"
        return f"{self.role_name} at {self.company.company_name}"

class Application(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    placement_offer = models.ForeignKey(PlacementOffer, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=[('Applied', 'Applied'), ('Shortlisted', 'Shortlisted'), ('Offered', 'Offered'), ('Accepted', 'Accepted'), ('Rejected', 'Rejected')])
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (('student', 'placement_offer'),)
        indexes = [
            models.Index(fields=["student"], name="application_student_idx"),
            models.Index(fields=["placement_offer"], name="application_offer_idx"),
        ]

    def __str__(self):
        return f"{self.student.user.name} applied for {self.placement_offer}"

class FinalOutcome(models.Model):
    student = models.OneToOneField(Student, on_delete=models.CASCADE, primary_key=True)
    graduating_cpi = models.DecimalField(max_digits=4, decimal_places=2)
    total_credits_earned = models.PositiveIntegerField()
    class_awarded = models.CharField(max_length=50)
    degree_awarded = models.CharField(max_length=255)
    graduation_year = models.PositiveSmallIntegerField()

    class Meta:
        indexes = [
            models.Index(fields=["student"], name="final_outcome_student_idx"),
        ]

    def __str__(self):
        return f"Final Outcome for {self.student.user.name}"
