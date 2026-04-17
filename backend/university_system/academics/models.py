from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from users.models import Student, Faculty

class Program(models.Model):
    id = models.AutoField(primary_key=True)
    degree = models.CharField(max_length=50)
    branch = models.CharField(max_length=50)
    duration_years = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "program"
        unique_together = ("degree", "branch")

    def __str__(self):
        return f"{self.degree} - {self.branch}"


class Department(models.Model):
    department_id = models.AutoField(primary_key=True)
    department_name = models.CharField(max_length=150, unique=True)

    class Meta:
        db_table = "department"

    def __str__(self):
        return self.department_name


class CourseCategory(models.TextChoices):
    HS = "HS", "Humanities"
    MS = "MS", "Maths"
    SC = "SC", "Science"
    PC = "PC", "Program Core"
    PE = "PE", "Program Elective"
    OE = "OE", "Open Elective"


class Course(models.Model):
    course_code = models.CharField(max_length=20, primary_key=True)
    course_name = models.CharField(max_length=200)
    credits = models.PositiveSmallIntegerField()
    category = models.CharField(
        max_length=10,
        choices=CourseCategory.choices
    )
    min_attendance_req = models.PositiveSmallIntegerField()
    program = models.ForeignKey(Program, on_delete=models.PROTECT)

    class Meta:
        db_table = "course"

    def __str__(self):
        return f"{self.course_code} - {self.course_name}"


class CourseOffering(models.Model):
    offering_id = models.AutoField(primary_key=True)
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    academic_year = models.CharField(max_length=20)
    semester_no = models.PositiveSmallIntegerField()
    max_capacity = models.PositiveIntegerField(default=60)
    is_grading_finalized = models.BooleanField(default=False)

    class Meta:
        db_table = "course_offering"

    def __str__(self):
        return f"{self.course.course_name} ({self.academic_year}) - Sem {self.semester_no}"


class Prerequisite(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="prerequisites")
    prereq_course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="required_for")
    min_grade_req = models.ForeignKey("GradeScale", on_delete=models.PROTECT)

    class Meta:
        db_table = "prerequisite"
        unique_together = ("course", "prereq_course")

    def __str__(self):
        return (
            f"{self.course.course_code} -> {self.prereq_course.course_code} "
            f"(min grade: {self.min_grade_req_id})"
        )


class Enrollment(models.Model):
    class EnrollmentStatus(models.TextChoices):
        PASS = "PASS", "Pass"
        BACKLOG = "BACKLOG", "Backlog"
        ONGOING = "ONGOING", "Ongoing"

    class FailureReason(models.TextChoices):
        ATTENDANCE = "ATTENDANCE", "Attendance Shortage"
        GRADE = "GRADE", "Low Grade"

    enrollment_id = models.AutoField(primary_key=True)
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE)
    faculty = models.ForeignKey(
        Faculty,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    attempt_no = models.PositiveSmallIntegerField()
    enrollment_type = models.CharField(max_length=50)
    status = models.CharField(
        max_length=20,
        choices=EnrollmentStatus.choices,
        default=EnrollmentStatus.ONGOING,
    )
    failure_reason = models.CharField(
        max_length=50,
        choices=FailureReason.choices,
        null=True,
        blank=True,
    )

    class Meta:
        db_table = "enrollment"
        constraints = [
            models.UniqueConstraint(
                fields=["student", "offering", "attempt_no"],
                name="unique_student_course_enrollment_attempt"
            )
        ]
        indexes = [
            models.Index(fields=["student"], name="enrollment_student_idx"),
            models.Index(fields=["offering"], name="enrollment_offering_idx"),
        ]


class Attendance(models.Model):
    offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE)
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE)
    date = models.DateField()
    status = models.CharField(max_length=20)

    class Meta:
        db_table = "attendance"
        unique_together = ("enrollment", "date")
        indexes = [
            models.Index(fields=["offering", "date"], name="attendance_offering_date_idx"),
        ]


class AssessmentComponent(models.Model):
    class ComponentType(models.TextChoices):
        MIDSEM = "MIDSEM", "Midsem"
        ENDSEM = "ENDSEM", "Endsem"
        QUIZ = "QUIZ", "Quiz"
        PROJECT = "PROJECT", "Project"
        LAB = "LAB", "Lab"
        ASSIGNMENT = "ASSIGNMENT", "Assignment"
        ATTENDANCE = "ATTENDANCE", "Attendance"

    offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE)
    type = models.CharField(max_length=50, choices=ComponentType.choices)
    weightage = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        db_table = "assessment_component"
        unique_together = ("offering", "type")


class StudentMarks(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE)
    component = models.ForeignKey(AssessmentComponent, on_delete=models.CASCADE)
    marks_obtained = models.DecimalField(max_digits=6, decimal_places=2)
    weighted_marks = models.DecimalField(max_digits=7, decimal_places=2)

    class Meta:
        db_table = "student_marks"
        unique_together = ("enrollment", "component")
        indexes = [
            models.Index(fields=["enrollment"], name="studentmarks_enrollment_idx"),
        ]


class GradeScale(models.Model):
    grade_letter = models.CharField(max_length=5, primary_key=True)
    grade_point = models.DecimalField(max_digits=4, decimal_places=2)

    class Meta:
        db_table = "grade_scale"

    def __str__(self):
        return self.grade_letter


class Grade(models.Model):
    enrollment = models.OneToOneField(Enrollment, on_delete=models.CASCADE, primary_key=True)
    grade_letter = models.ForeignKey(GradeScale, on_delete=models.PROTECT)
    is_counted_for_cpi = models.BooleanField()

    class Meta:
        db_table = "grade"


class CourseGradeScale(models.Model):
    offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE)
    grade_letter = models.ForeignKey(GradeScale, on_delete=models.PROTECT)
    min_score = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        db_table = "course_grade_scale"
        constraints = [
            models.UniqueConstraint(
                fields=["offering", "grade_letter"],
                name="unique_offering_grade_letter",
            ),
            models.UniqueConstraint(
                fields=["offering", "min_score"],
                name="unique_offering_min_score",
            ),
        ]

    def __str__(self):
        return f"{self.grade_letter.grade_letter} (≥{self.min_score})"


class SemesterResult(models.Model):
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    academic_year = models.CharField(max_length=20)
    semester_no = models.PositiveSmallIntegerField()
    spi = models.DecimalField(max_digits=4, decimal_places=2)
    credits_earned_sem = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "semester_result"
        unique_together = ("student", "academic_year", "semester_no")


class CourseCart(models.Model):
    cart_id = models.AutoField(primary_key=True)
    student = models.ForeignKey(Student, on_delete=models.CASCADE)
    offering = models.ForeignKey(CourseOffering, on_delete=models.CASCADE)
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "course_cart"
        constraints = [
            models.UniqueConstraint(fields=["student", "offering"], name="unique_course_cart_item")
        ]

    def __str__(self):
        return f"{self.student.user.name} -> {self.offering.course.course_name}"


class RegistrationWindow(models.Model):
    semester_no = models.PositiveSmallIntegerField()
    academic_year = models.CharField(max_length=20)
    start_datetime = models.DateTimeField()
    end_datetime = models.DateTimeField()

    class Meta:
        db_table = "registration_window"
        constraints = [
            models.UniqueConstraint(
                fields=["semester_no", "academic_year"],
                name="unique_registration_window",
            )
        ]

    def is_open(self):
        now = timezone.now()
        return self.start_datetime <= now <= self.end_datetime
