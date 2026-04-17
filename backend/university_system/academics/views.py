from datetime import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction, DatabaseError
from django.db.models import Avg, Count, F, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from placements.models import Application, FinalOutcome
from users.models import Admin, Faculty, Student, User as ProfileUser

from .models import (
    Attendance,
    AssessmentComponent,
    Course,
    CourseCart,
    CourseGradeScale,
    CourseOffering,
    Enrollment,
    Grade,
    GradeScale,
    Prerequisite,
    RegistrationWindow,
    SemesterResult,
    StudentMarks,
)


def _to_hundredths(value):
    return int((Decimal(value).quantize(Decimal("0.01")) * 100))


def _validate_complete_scale_rows(rows):
    # No validation required - faculty has full control over grade scheme
    return True, ""


def _match_grade_for_offering(offering, total_weighted):
    return CourseGradeScale.objects.filter(
        offering=offering,
        min_score__lte=total_weighted,
    ).select_related("grade_letter").order_by("-min_score", "-grade_letter__grade_point").first()


def calculate_grade(enrollment):
    total_weighted = StudentMarks.objects.filter(enrollment=enrollment).aggregate(
        total=Sum("weighted_marks")
    )["total"] or Decimal("0.00")
    total_weighted = Decimal(total_weighted).quantize(Decimal("0.01"))
    course_scale = _match_grade_for_offering(enrollment.offering, total_weighted)
    if not course_scale:
        return None
    return course_scale.grade_letter


def calculate_attendance_percentage(enrollment):
    total_classes = Attendance.objects.filter(enrollment=enrollment).count()
    present_classes = Attendance.objects.filter(enrollment=enrollment, status="Present").count()

    if total_classes == 0:
        return 0

    return round((present_classes / total_classes) * 100, 2)


def _get_logged_in_profile_user(request):
    if not request.user.is_authenticated:
        return None

    try:
        return ProfileUser.objects.get(auth_user=request.user)
    except ProfileUser.DoesNotExist:
        pass

    if getattr(request.user, "email", None):
        try:
            return ProfileUser.objects.get(email__iexact=request.user.email)
        except ProfileUser.DoesNotExist:
            return None

    return None


def _get_student_for_request(request):
    profile_user = _get_logged_in_profile_user(request)
    if not profile_user or profile_user.user_type != ProfileUser.UserType.STUDENT:
        return None
    try:
        return Student.objects.select_related("user", "program").get(user=profile_user)
    except Student.DoesNotExist:
        return None


def _get_faculty_for_request(request):
    profile_user = _get_logged_in_profile_user(request)
    if not profile_user or profile_user.user_type != ProfileUser.UserType.FACULTY:
        return None
    try:
        return Faculty.objects.select_related("user", "department").get(user=profile_user)
    except Faculty.DoesNotExist:
        return None


def _get_admin_for_request(request):
    profile_user = _get_logged_in_profile_user(request)
    if not profile_user or profile_user.user_type != ProfileUser.UserType.ADMIN:
        return None
    try:
        return Admin.objects.select_related("user").get(user=profile_user)
    except Admin.DoesNotExist:
        return None


def _role_label(user_type):
    mapping = {
        ProfileUser.UserType.STUDENT: "Student",
        ProfileUser.UserType.FACULTY: "Faculty",
        ProfileUser.UserType.ADMIN: "Admin",
    }
    return mapping.get(user_type, "User")


def _forbidden_page(request, required_role=None, detail_message=None):
    profile_user = _get_logged_in_profile_user(request)
    current_role_label = _role_label(profile_user.user_type) if profile_user else "User"
    required_role_label = _role_label(required_role) if required_role else None

    if detail_message:
        message = detail_message
    elif required_role_label and profile_user:
        message = f"You are logged in as {current_role_label}. This page is for {required_role_label}."
    else:
        message = "You are not authorized to access this page"

    return render(
        request,
        "403.html",
        {
            "forbidden_message": message,
            "required_role_label": required_role_label,
        },
        status=403,
    )


def _prerequisites_satisfied(student, course):
    prerequisites = Prerequisite.objects.filter(course=course).select_related(
        "prereq_course", "min_grade_req"
    )
    for prereq in prerequisites:
        print("Checking prereq:", prereq.prereq_course.course_code)
        enrollment = Enrollment.objects.filter(
            student=student,
            offering__course=prereq.prereq_course,
        ).order_by("-attempt_no").first()
        print("Enrollment found:", enrollment)
        print("Status:", enrollment.status if enrollment else None)
        print("Attempt:", enrollment.attempt_no if enrollment else None)

        if enrollment is None:
            return False

        if enrollment.status == Enrollment.EnrollmentStatus.BACKLOG:
            return False

        grade = Grade.objects.filter(enrollment=enrollment).select_related("grade_letter").first()
        if grade is None:
            return False

        if grade.grade_letter.grade_point < prereq.min_grade_req.grade_point:
            return False

    return True


def _get_registration_window(semester_no, academic_year):
    try:
        return RegistrationWindow.objects.get(
            semester_no=semester_no,
            academic_year=academic_year,
        )
    except RegistrationWindow.DoesNotExist:
        return None


def _get_student_academic_year_labels(student):
    """Return acceptable academic_year labels for the student's active term."""
    year_offset = max(student.current_semester - 1, 0) // 2
    start_year = student.admission_year + year_offset
    end_year = start_year + 1
    end_year_short = str(end_year)[-2:]

    return {
        str(start_year),
        f"{start_year}-{end_year}",
        f"{start_year}-{end_year_short}",
        f"{start_year}/{end_year}",
        f"{start_year}/{end_year_short}",
    }


def _get_primary_student_academic_year(student):
    year_offset = max(student.current_semester - 1, 0) // 2
    return str(student.admission_year + year_offset)


@login_required
def student_dashboard(request):
    student = _get_student_for_request(request)
    if not student:
        return _forbidden_page(request, required_role=ProfileUser.UserType.STUDENT)

    student_academic_year_labels = _get_student_academic_year_labels(student)
    current_academic_year = _get_primary_student_academic_year(student)

    offerings = CourseOffering.objects.select_related("course").filter(
        semester_no=student.current_semester,
        academic_year__in=student_academic_year_labels,
    )
    cart_items = CourseCart.objects.filter(student=student).select_related("offering__course")
    enrollments = Enrollment.objects.filter(student=student).select_related("offering__course", "faculty__user")
    semester_enrollments = enrollments.filter(
        offering__semester_no=student.current_semester,
        offering__academic_year__in=student_academic_year_labels,
    )
    cart_offering_ids = set(cart_items.values_list("offering_id", flat=True))
    enrolled_offering_ids = set(semester_enrollments.values_list("offering_id", flat=True))
    registration_locked = semester_enrollments.exists()

    offering_rows = []
    for offering in offerings:
        status = "available"
        status_message = "Available"

        if offering.offering_id in enrolled_offering_ids:
            status = "already_enrolled"
            status_message = "Already enrolled"
        elif offering.offering_id in cart_offering_ids:
            status = "already_in_cart"
            status_message = "Already added"
        elif registration_locked:
            status = "registration_locked"
            status_message = "Enrollment already submitted"
        elif not _prerequisites_satisfied(student, offering.course):
            status = "prerequisite_not_satisfied"
            status_message = "Prerequisite not satisfied"

        offering_rows.append(
            {
                "offering": offering,
                "status": status,
                "status_message": status_message,
            }
        )

    backlog_enrollments = (
        enrollments.filter(status=Enrollment.EnrollmentStatus.BACKLOG)
        .select_related("offering__course")
        .order_by("offering__course__course_code", "-offering__semester_no")
    )
    latest_backlog_by_course = {}
    for enrollment in backlog_enrollments:
        course_id = enrollment.offering.course_id
        existing = latest_backlog_by_course.get(course_id)
        if not existing or enrollment.offering.semester_no > existing.offering.semester_no:
            latest_backlog_by_course[course_id] = enrollment

    backlog_rows = []
    for enrollment in latest_backlog_by_course.values():
        last_attempt_semester = enrollment.offering.semester_no
        is_even_semester = (last_attempt_semester % 2 == 0)
        backlog_rows.append(
            {
                "course_name": enrollment.offering.course.course_name,
                "course_code": enrollment.offering.course.course_code,
                "last_attempt_semester": last_attempt_semester,
                "eligible_parity_message": "Eligible in next even semester" if is_even_semester else "Eligible in next odd semester",
            }
        )

    backlog_rows.sort(key=lambda row: row["course_code"])

    return render(
        request,
        "academics/student_dashboard.html",
        {
            "student": student,
            "offering_rows": offering_rows,
            "cart_items": cart_items,
            "enrollments": enrollments,
            "backlog_rows": backlog_rows,
            "registration_locked": registration_locked,
            "current_semester": student.current_semester,
            "current_academic_year": current_academic_year,
        },
    )


@login_required
def faculty_dashboard(request):
    faculty = _get_faculty_for_request(request)
    if not faculty:
        return _forbidden_page(request, required_role=ProfileUser.UserType.FACULTY)

    offerings = (
        CourseOffering.objects.filter(enrollment__faculty=faculty)
        .select_related("course")
        .distinct()
        .order_by("academic_year", "semester_no", "course__course_code")
    )

    active_offerings = []
    completed_offerings = []

    for offering in offerings:
        offering_enrollments = Enrollment.objects.filter(offering=offering, faculty=faculty)
        attendance_percentages = [
            calculate_attendance_percentage(enrollment)
            for enrollment in offering_enrollments
        ]
        if attendance_percentages:
            offering.average_attendance_percentage = round(
                sum(attendance_percentages) / len(attendance_percentages),
                2,
            )
        else:
            offering.average_attendance_percentage = 0

        total_enrollments = Enrollment.objects.filter(offering=offering).count()
        graded = Grade.objects.filter(enrollment__offering=offering).count()

        if total_enrollments > 0 and graded == total_enrollments:
            completed_offerings.append(offering)
        else:
            active_offerings.append(offering)

    enrollments = Enrollment.objects.filter(
        faculty=faculty,
        offering__in=active_offerings,
    ).select_related("offering__course", "student__user")

    courses_taught = (
        enrollments.values(
            "offering__offering_id",
            "offering__course__course_code",
            "offering__course__course_name",
            "offering__academic_year",
            "offering__semester_no",
        )
        .annotate(student_count=Count("student"))
        .order_by("offering__academic_year", "offering__semester_no", "offering__course__course_code")
    )
    return render(
        request,
        "academics/faculty_dashboard.html",
        {
            "faculty": faculty,
            "enrollments": enrollments,
            "courses_taught": courses_taught,
            "active_offerings": active_offerings,
            "completed_offerings": completed_offerings,
        },
    )


@login_required
def student_transcript(request):
    student = _get_student_for_request(request)
    if not student:
        return _forbidden_page(request, required_role=ProfileUser.UserType.STUDENT)

    enrollments = Enrollment.objects.filter(student=student).select_related(
        "offering__course",
        "faculty__user",
        "grade__grade_letter",
    ).order_by(
        "offering__academic_year", "offering__semester_no", "offering__course__course_code"
    )

    semester_results_qs = SemesterResult.objects.filter(student=student)
    semester_results_map = {
        (result.academic_year, result.semester_no): result
        for result in semester_results_qs
    }

    transcript_by_semester = {}
    for enrollment in enrollments:
        key = (enrollment.offering.academic_year, enrollment.offering.semester_no)
        if key not in transcript_by_semester:
            semester_result = semester_results_map.get(key)
            transcript_by_semester[key] = {
                "academic_year": enrollment.offering.academic_year,
                "semester_no": enrollment.offering.semester_no,
                "rows": [],
                "spi": semester_result.spi if semester_result else None,
                "spi_available": semester_result is not None and semester_result.spi is not None,
                "backlog_count": 0,
            }

        try:
            grade_obj = enrollment.grade
            grade_letter = grade_obj.grade_letter_id
        except ObjectDoesNotExist:
            grade_letter = "-"

        is_backlog = enrollment.status == Enrollment.EnrollmentStatus.BACKLOG
        if is_backlog:
            transcript_by_semester[key]["backlog_count"] += 1

        if is_backlog:
            if enrollment.failure_reason == Enrollment.FailureReason.ATTENDANCE:
                display_grade = "F (Attendance)"
            elif enrollment.failure_reason == Enrollment.FailureReason.GRADE:
                display_grade = "F (Grade)"
            else:
                display_grade = "F"
        else:
            display_grade = grade_letter

        transcript_by_semester[key]["rows"].append(
            {
                "course_code": enrollment.offering.course.course_code,
                "course_name": enrollment.offering.course.course_name,
                "credits": enrollment.offering.course.credits,
                "faculty_name": enrollment.faculty.user.name if enrollment.faculty else "Faculty not assigned",
                "grade_letter": display_grade,
                "is_backlog": is_backlog,
            }
        )

    grouped_semesters = [
        transcript_by_semester[key]
        for key in sorted(transcript_by_semester.keys(), key=lambda value: (value[0], value[1]))
    ]

    cpi_weighted_sum = Decimal("0")
    cpi_credit_total = 0
    for result in semester_results_qs:
        if result.spi is None:
            continue
        semester_credits = result.credits_earned_sem or 0
        cpi_weighted_sum += Decimal(result.spi) * Decimal(semester_credits)
        cpi_credit_total += semester_credits

    if cpi_credit_total == 0:
        calculated_cpi = None
    else:
        calculated_cpi = (cpi_weighted_sum / Decimal(cpi_credit_total)).quantize(Decimal("0.01"))

    return render(
        request,
        "academics/student_transcript.html",
        {
            "student": student,
            "grouped_semesters": grouped_semesters,
            "calculated_cpi": calculated_cpi,
            "cpi_available": calculated_cpi is not None,
            "cpi_message": "CPI will be calculated after semester completion",
        },
    )


@login_required
def faculty_teaching_load(request):
    admin_user = _get_admin_for_request(request)
    if not admin_user:
        return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

    faculty_load = (
        Faculty.objects.select_related("user", "department")
        .annotate(
            total_students=Count("enrollment"),
            courses_taught=Count("enrollment__offering", distinct=True),
        )
        .order_by("-total_students", "user__name")
    )

    return render(
        request,
        "academics/faculty_teaching_load.html",
        {"faculty_load": faculty_load},
    )


@login_required
def admin_allocate_faculty(request, offering_id):
    admin_user = _get_admin_for_request(request)
    if not admin_user:
        return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

    offering = get_object_or_404(CourseOffering.objects.select_related("course"), pk=offering_id)
    enrollments = Enrollment.objects.filter(offering=offering).select_related(
        "student__user", "faculty__user"
    )
    faculty_list = Faculty.objects.select_related("user").all()

    return render(
        request,
        "academics/admin_allocate_faculty.html",
        {
            "offering": offering,
            "enrollments": enrollments,
            "faculty_list": faculty_list,
        },
    )


@login_required
def assign_faculty_to_enrollment(request):
    admin_user = _get_admin_for_request(request)
    if not admin_user:
        return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

    if request.method != "POST":
        return _forbidden_page(request, detail_message="Invalid request method.")

    enrollment_id = request.POST.get("enrollment_id")
    faculty_id = request.POST.get("faculty_id")

    enrollment = get_object_or_404(Enrollment, pk=enrollment_id)
    faculty = get_object_or_404(Faculty, pk=faculty_id)

    enrollment.faculty = faculty
    enrollment.save(update_fields=["faculty"])

    return redirect("admin_allocate_faculty", offering_id=enrollment.offering_id)


@login_required
def course_students(request, offering_id):
    faculty = _get_faculty_for_request(request)
    if not faculty:
        return _forbidden_page(request, required_role=ProfileUser.UserType.FACULTY)

    offering = get_object_or_404(CourseOffering, pk=offering_id)
    enrollments = Enrollment.objects.filter(offering=offering, faculty=faculty).select_related(
        "student__user", "offering__course", "faculty__user"
    )

    enrollment_rows = [
        {
            "enrollment": enrollment,
            "attendance_percentage": calculate_attendance_percentage(enrollment),
        }
        for enrollment in enrollments
    ]

    return render(
        request,
        "academics/course_students.html",
        {
            "offering": offering,
            "enrollments": enrollments,
            "enrollment_rows": enrollment_rows,
        },
    )


@login_required
def mark_attendance(request, offering_id):
    faculty = _get_faculty_for_request(request)
    if not faculty:
        return _forbidden_page(request, required_role=ProfileUser.UserType.FACULTY)

    offering = get_object_or_404(CourseOffering.objects.select_related("course"), pk=offering_id)
    enrollments = Enrollment.objects.filter(offering=offering, faculty=faculty).select_related(
        "student__user"
    )

    if request.method == "POST":
        date_input = request.POST.get("date")
        if not date_input:
            messages.error(request, "Please select a date.")
            return redirect("mark_attendance", offering_id=offering_id)

        try:
            attendance_date = datetime.strptime(date_input, "%Y-%m-%d").date()
        except ValueError:
            messages.error(request, "Invalid date selected.")
            return redirect("mark_attendance", offering_id=offering_id)

        if Attendance.objects.filter(offering=offering, date=attendance_date).exists():
            messages.error(request, "Attendance already marked for this date.")
            return redirect("mark_attendance", offering_id=offering_id)

        present_student_ids = set(request.POST.getlist("present_students"))

        try:
            with transaction.atomic():
                for enrollment in enrollments:
                    status = "Present" if str(enrollment.enrollment_id) in present_student_ids else "Absent"
                    Attendance.objects.create(
                        offering=offering,
                        enrollment=enrollment,
                        date=attendance_date,
                        status=status,
                    )
        except DatabaseError:
            messages.error(request, "Failed to submit attendance. Please try again.")
            return redirect("mark_attendance", offering_id=offering_id)

        messages.success(request, "Attendance submitted successfully.")
        return redirect("course_students", offering_id=offering_id)

    marked_dates = Attendance.objects.filter(offering=offering).values_list("date", flat=True).distinct().order_by("-date")

    return render(
        request,
        "academics/mark_attendance.html",
        {
            "offering": offering,
            "enrollments": enrollments,
            "marked_dates": marked_dates,
        },
    )


@login_required
def add_to_cart(request, offering_id):
    student = _get_student_for_request(request)
    if not student:
        print("ADD TO CART START")
        print("FAILED: student not found")
        return _forbidden_page(request, required_role=ProfileUser.UserType.STUDENT)

    offering = get_object_or_404(CourseOffering.objects.select_related("course"), pk=offering_id)
    student_academic_year_labels = _get_student_academic_year_labels(student)
    print("ADD TO CART START")
    print("Student:", student.user.name)
    print("Student current semester:", student.current_semester)
    print("Offering semester no:", offering.semester_no)

    window = _get_registration_window(student.current_semester, offering.academic_year)
    window_status = "open" if (window and window.is_open()) else "closed"
    print("Registration window status:", window_status)
    if not window or not window.is_open():
        print("FAILED: registration closed")
        messages.error(request, "Course registration is currently closed.")
        return redirect("student_dashboard")

    # Check prerequisites
    prerequisites_satisfied = _prerequisites_satisfied(student, offering.course)
    print("Prerequisites satisfied:", prerequisites_satisfied)
    if not prerequisites_satisfied:
        print("FAILED: prerequisite not satisfied")
        messages.error(
            request,
            "You cannot register for this course because prerequisites are not satisfied.",
        )
        return redirect("student_dashboard")

    # Prevent adding to cart if enrollment already submitted
    enrollment_exists = Enrollment.objects.filter(
        student=student,
        offering__semester_no=student.current_semester,
        offering__academic_year__in=student_academic_year_labels,
    ).exists()
    print("Enrollment already exists:", enrollment_exists)
    if enrollment_exists:
        print("FAILED: already enrolled")
        return redirect("student_dashboard")

    # Only allow adding if the offering matches the student's current semester
    if offering.semester_no != student.current_semester:
        print("FAILED: semester mismatch")
        return redirect("student_dashboard")

    if offering.academic_year not in student_academic_year_labels:
        print("FAILED: academic year mismatch")
        return redirect("student_dashboard")

    previous_backlog_enrollment = (
        Enrollment.objects.filter(
            student=student,
            offering__course=offering.course,
            status=Enrollment.EnrollmentStatus.BACKLOG,
        )
        .select_related("offering")
        .order_by("-offering__semester_no")
        .first()
    )
    if previous_backlog_enrollment:
        original_semester = previous_backlog_enrollment.offering.semester_no
        current_semester = student.current_semester
        if (original_semester % 2) != (current_semester % 2):
            parity_message = "Eligible in next even semester" if (original_semester % 2 == 0) else "Eligible in next odd semester"
            messages.error(request, parity_message)
            return redirect("student_dashboard")

    # Avoid duplicates in the cart
    if CourseCart.objects.filter(student=student, offering=offering).exists():
        print("FAILED: already in cart")
        return redirect("student_dashboard")

    if Enrollment.objects.filter(student=student, offering=offering).exists():
        print("FAILED: already enrolled")
        messages.error(request, "You are already enrolled in this course offering.")
        return redirect("student_dashboard")

    CourseCart.objects.get_or_create(student=student, offering=offering)
    print("SUCCESS: added to cart")
    return redirect("student_dashboard")


@login_required
def submit_enrollment(request):
    student = _get_student_for_request(request)
    if not student:
        return _forbidden_page(request, required_role=ProfileUser.UserType.STUDENT)

    student_academic_year_labels = _get_student_academic_year_labels(student)

    # Prevent multiple submissions for the active semester
    if Enrollment.objects.filter(
        student=student,
        offering__semester_no=student.current_semester,
        offering__academic_year__in=student_academic_year_labels,
    ).exists():
        return redirect("student_dashboard")

    cart_items = CourseCart.objects.filter(student=student).select_related("offering__course")

    total_credits = sum(item.offering.course.credits for item in cart_items)
    print("Total credits:", total_credits)

    # if total_credits < 12:
    #     messages.error(request, "Minimum 12 credits required")
    #     return redirect("student_dashboard")

    # if total_credits > 26:
    #     messages.error(request, "Maximum 26 credits allowed")
    #     return redirect("student_dashboard")

    # Check prerequisites for all cart items
    for item in cart_items:
        if not _prerequisites_satisfied(student, item.offering.course):
            messages.error(request, "You cannot register for this course because prerequisites are not satisfied.")
            return redirect("student_dashboard")

    if not cart_items.exists():
        return redirect("student_dashboard")

    for item in cart_items:
        offering = item.offering
        window = _get_registration_window(student.current_semester, offering.academic_year)
        if not window or not window.is_open():
            messages.error(request, "Registration period has ended.")
            return redirect("student_dashboard")

        previous_backlog_enrollment = (
            Enrollment.objects.filter(
                student=student,
                offering__course=offering.course,
                status=Enrollment.EnrollmentStatus.BACKLOG,
            )
            .select_related("offering")
            .order_by("-offering__semester_no")
            .first()
        )
        if previous_backlog_enrollment:
            original_semester = previous_backlog_enrollment.offering.semester_no
            if (original_semester % 2) != (student.current_semester % 2):
                parity_message = "Eligible in next even semester" if (original_semester % 2 == 0) else "Eligible in next odd semester"
                messages.error(request, parity_message)
                return redirect("student_dashboard")

        if Enrollment.objects.filter(student=student, offering=offering).exists():
            messages.error(request, "You are already enrolled in this course offering.")
            return redirect("student_dashboard")

    try:
        with transaction.atomic():
            for item in cart_items:
                offering = item.offering
                previous_attempt_no = (
                    Enrollment.objects.filter(
                        student=student,
                        offering__course=offering.course,
                    ).aggregate(max_attempt=Max("attempt_no"))["max_attempt"]
                    or 0
                )

                Enrollment.objects.create(
                    student=student,
                    offering=offering,
                    faculty=None,
                    attempt_no=previous_attempt_no + 1,
                    enrollment_type="REGULAR",
                    failure_reason=None,
                )
            cart_items.delete()
    except DatabaseError:
        messages.error(request, "Enrollment submission failed. No courses were enrolled.")
        return redirect("student_dashboard")

    return redirect("student_dashboard")


@login_required
def remove_from_cart(request, cart_id):
    student = _get_student_for_request(request)
    if not student:
        return _forbidden_page(request, required_role=ProfileUser.UserType.STUDENT)

    cart_item = get_object_or_404(CourseCart, cart_id=cart_id, student=student)
    cart_item.delete()
    return redirect("student_dashboard")


@login_required
def manage_assessments(request, offering_id):
    faculty = _get_faculty_for_request(request)
    if not faculty:
        return _forbidden_page(request, required_role=ProfileUser.UserType.FACULTY)

    offering = get_object_or_404(CourseOffering, pk=offering_id)
    components = AssessmentComponent.objects.filter(offering=offering)

    if request.method == "POST":
        component_type = request.POST.get("type")
        weightage_raw = request.POST.get("weightage", "0")

        try:
            weightage = Decimal(weightage_raw)
        except (InvalidOperation, TypeError):
            messages.error(request, "Please enter a valid weightage.")
            return redirect("manage_assessments", offering_id=offering_id)

        # Check for duplicate component
        if AssessmentComponent.objects.filter(offering=offering, type=component_type).exists():
            messages.error(request, "Assessment component already exists for this offering.")
            return redirect("manage_assessments", offering_id=offering_id)

        # Check total weightage
        total_weightage = components.aggregate(Sum("weightage"))["weightage__sum"] or Decimal("0")
        if total_weightage + weightage > 100:
            messages.error(request, "Total weightage cannot exceed 100%.")
            return redirect("manage_assessments", offering_id=offering_id)

        AssessmentComponent.objects.create(offering=offering, type=component_type, weightage=weightage)
        messages.success(request, "Assessment component added successfully.")
        return redirect("manage_assessments", offering_id=offering_id)

    total_weightage = components.aggregate(Sum("weightage"))["weightage__sum"] or 0
    return render(
        request,
        "academics/manage_assessments.html",
        {
            "offering": offering,
            "components": components,
            "total_weightage": total_weightage,
            "can_upload_marks": Decimal(total_weightage) == Decimal("100"),
        },
    )


@login_required
def upload_marks(request, enrollment_id):
    faculty = _get_faculty_for_request(request)
    if not faculty:
        return _forbidden_page(request, required_role=ProfileUser.UserType.FACULTY)

    enrollment = get_object_or_404(Enrollment, pk=enrollment_id)
    if enrollment.faculty != faculty:
        return _forbidden_page(request, detail_message="You are not authorized to access this student record.")

    if enrollment.offering.is_grading_finalized:
        messages.error(request, "Grades finalized. Marks upload is locked for this offering.")
        return redirect("course_students", offering_id=enrollment.offering.offering_id)

    component_type = request.POST.get("type") or request.GET.get("type")
    components = AssessmentComponent.objects.filter(offering=enrollment.offering)
    total_weightage = components.aggregate(Sum("weightage"))["weightage__sum"] or Decimal("0")
    attendance_component = components.filter(type=AssessmentComponent.ComponentType.ATTENDANCE).first()

    if attendance_component:
        all_offering_enrollments = Enrollment.objects.filter(offering=enrollment.offering)
        distinct_attendance_dates = Attendance.objects.filter(
            offering=enrollment.offering
        ).values_list("date", flat=True).distinct()
        total_attendance_dates = distinct_attendance_dates.count()

        all_attendance_marked = total_attendance_dates > 0
        if all_attendance_marked:
            for offering_enrollment in all_offering_enrollments:
                enrollment_date_count = Attendance.objects.filter(
                    offering=enrollment.offering,
                    enrollment=offering_enrollment,
                ).values_list("date", flat=True).distinct().count()
                if enrollment_date_count < total_attendance_dates:
                    all_attendance_marked = False
                    break

        if all_attendance_marked:
            with transaction.atomic():
                for offering_enrollment in all_offering_enrollments:
                    if StudentMarks.objects.filter(
                        enrollment=offering_enrollment,
                        component=attendance_component,
                    ).exists():
                        continue

                    attendance_percentage = Decimal(
                        str(calculate_attendance_percentage(offering_enrollment))
                    ).quantize(Decimal("0.01"))
                    weighted_marks = (
                        attendance_percentage * attendance_component.weightage
                    ) / Decimal("100")

                    StudentMarks.objects.create(
                        enrollment=offering_enrollment,
                        component=attendance_component,
                        marks_obtained=attendance_percentage,
                        weighted_marks=weighted_marks,
                    )

    if Decimal(total_weightage) != Decimal("100"):
        messages.error(
            request,
            "Marks can only be uploaded when total assessment weightage is exactly 100%.",
        )
        return redirect("manage_assessments", offering_id=enrollment.offering.offering_id)

    if request.method == "POST":
        if not component_type:
            messages.error(request, "Please select an assessment component.")
            return redirect("upload_marks", enrollment_id=enrollment_id)

        component = get_object_or_404(AssessmentComponent, offering=enrollment.offering, type=component_type)
        if component.type == AssessmentComponent.ComponentType.ATTENDANCE:
            marks_obtained = Decimal(str(calculate_attendance_percentage(enrollment))).quantize(
                Decimal("0.01")
            )
        else:
            marks_obtained_raw = request.POST.get("marks_obtained", "0")
            try:
                marks_obtained = Decimal(marks_obtained_raw)
            except (InvalidOperation, TypeError):
                messages.error(request, "Please enter valid marks.")
                return redirect("upload_marks", enrollment_id=enrollment_id)

        weighted_marks = (marks_obtained * component.weightage) / 100

        # Prevent duplicate marks
        if StudentMarks.objects.filter(enrollment=enrollment, component=component).exists():
            messages.error(request, "Marks already uploaded for this component.")
            return redirect("upload_marks", enrollment_id=enrollment_id)

        StudentMarks.objects.create(
            enrollment=enrollment,
            component=component,
            marks_obtained=marks_obtained,
            weighted_marks=weighted_marks,
        )

        uploaded_count = StudentMarks.objects.filter(enrollment=enrollment).count()
        component_count = components.count()
        if component_count > 0 and uploaded_count == component_count:
            total_weighted = StudentMarks.objects.filter(enrollment=enrollment).aggregate(
                Sum("weighted_marks")
            )["weighted_marks__sum"] or Decimal("0")

            messages.success(
                request,
                f"All components uploaded. Total weighted marks: {total_weighted}. Use Finalize Grades to assign grades.",
            )

        messages.success(request, "Marks uploaded successfully.")
        return redirect("upload_marks", enrollment_id=enrollment_id)

    existing_marks_qs = StudentMarks.objects.filter(enrollment=enrollment).select_related("component")
    existing_marks = existing_marks_qs.values_list("component__type", flat=True)
    available_components = components.exclude(type__in=existing_marks)
    return render(
        request,
        "academics/upload_marks.html",
        {
            "enrollment": enrollment,
            "components": components,
            "available_components": available_components,
            "existing_marks": existing_marks,
            "existing_marks_records": existing_marks_qs,
            "selected_type": component_type,
            "attendance_component_value": AssessmentComponent.ComponentType.ATTENDANCE,
        },
    )


@login_required
def manage_grading_scheme(request, offering_id):
    faculty = _get_faculty_for_request(request)
    if not faculty:
        return _forbidden_page(request, required_role=ProfileUser.UserType.FACULTY)

    offering = get_object_or_404(CourseOffering.objects.select_related("course"), pk=offering_id)
    if not Enrollment.objects.filter(offering=offering, faculty=faculty).exists():
        return _forbidden_page(request, detail_message="Only assigned faculty can manage grading for this offering.")

    enrollments = Enrollment.objects.filter(offering=offering).select_related("student__user")
    student_totals = []
    marks_distribution = []
    for enrollment in enrollments:
        total = StudentMarks.objects.filter(enrollment=enrollment).aggregate(
            total=Sum("weighted_marks")
        )["total"] or Decimal("0.00")
        total = Decimal(total).quantize(Decimal("0.01"))

        try:
            assigned_grade = enrollment.grade.grade_letter_id
        except ObjectDoesNotExist:
            assigned_grade = "-"

        student_totals.append(
            {
                "enrollment": enrollment,
                "total_weighted": total,
                "assigned_grade": assigned_grade,
            }
        )
        marks_distribution.append(total)

    marks_distribution.sort(reverse=True)

    reference_grades = list(GradeScale.objects.all().order_by("-grade_point", "grade_letter"))
    existing_scheme = {
        scale.grade_letter_id: scale
        for scale in CourseGradeScale.objects.filter(offering=offering).select_related("grade_letter")
    }
    scheme_rows = []
    for grade in reference_grades:
        existing = existing_scheme.get(grade.grade_letter)
        scheme_rows.append(
            {
                "grade_letter": grade.grade_letter,
                "grade_point": grade.grade_point,
                "min_score": existing.min_score if existing else None,
            }
        )
    # Sort by min_score descending
    scheme_rows.sort(key=lambda x: x["min_score"] if x["min_score"] is not None else -1, reverse=True)

    if request.method == "POST":
        if offering.is_grading_finalized:
            messages.error(request, "Grades finalized")
            return redirect("manage_grading_scheme", offering_id=offering_id)

        action = request.POST.get("action", "save_scheme")

        if action == "finalize_grades":
            # No validation needed - faculty defines any grade scheme they want
            enrollments = Enrollment.objects.filter(offering=offering).select_related("student")
            updated_count = 0
            student_ids = set()

            with transaction.atomic():
                for enrollment in enrollments:
                    matched_grade = calculate_grade(enrollment)
                    if not matched_grade:
                        messages.error(request, "Grade scheme does not cover all computed scores.")
                        transaction.set_rollback(True)
                        return redirect("manage_grading_scheme", offering_id=offering_id)

                    Grade.objects.update_or_create(
                        enrollment=enrollment,
                        defaults={
                            "grade_letter": matched_grade,
                            "is_counted_for_cpi": True,
                        },
                    )
                    updated_count += 1
                    student_ids.add(enrollment.student_id)

                for student_id in student_ids:
                    student = Student.objects.get(pk=student_id)
                    semester_enrollments = Enrollment.objects.filter(
                        student=student,
                        offering__semester_no=offering.semester_no,
                    )
                    total_enrollments = semester_enrollments.count()
                    graded_enrollments = Grade.objects.filter(enrollment__in=semester_enrollments).count()
                    if total_enrollments > 0 and graded_enrollments == total_enrollments:
                        calculate_spi(student, offering.semester_no)

                offering.is_grading_finalized = True
                offering.save(update_fields=["is_grading_finalized"])

            messages.success(request, f"Grades finalized for {updated_count} enrollments.")
            return redirect("manage_grading_scheme", offering_id=offering_id)

        raw_rows = []
        for grade in reference_grades:
            min_key = f"min_score_{grade.grade_letter}"
            min_raw = request.POST.get(min_key, "").strip()

            if not min_raw:
                continue

            try:
                min_score = Decimal(min_raw).quantize(Decimal("0.01"))
            except (InvalidOperation, TypeError):
                messages.error(request, f"Invalid numeric value for grade {grade.grade_letter}.")
                return redirect("manage_grading_scheme", offering_id=offering_id)

            raw_rows.append(
                {
                    "grade": grade,
                    "min": min_score,
                }
            )

        with transaction.atomic():
            CourseGradeScale.objects.filter(offering=offering).exclude(
                grade_letter__in=[row["grade"] for row in raw_rows]
            ).delete()

            for row in raw_rows:
                CourseGradeScale.objects.update_or_create(
                    offering=offering,
                    grade_letter=row["grade"],
                    defaults={
                        "min_score": row["min"],
                    },
                )

        messages.success(request, "Grading scheme saved successfully.")
        return redirect("manage_grading_scheme", offering_id=offering_id)

    return render(
        request,
        "academics/manage_grading_scheme.html",
        {
            "offering": offering,
            "student_totals": student_totals,
            "marks_distribution": marks_distribution,
            "scheme_rows": scheme_rows,
            "is_grading_finalized": offering.is_grading_finalized,
        },
    )


def calculate_spi(student, semester):
    enrollments = Enrollment.objects.filter(student=student, offering__semester_no=semester).select_related(
        "offering__course"
    )
    total_enrollments = enrollments.count()
    if total_enrollments == 0:
        return None

    non_backlog_enrollments = enrollments.exclude(status=Enrollment.EnrollmentStatus.BACKLOG)
    non_backlog_count = non_backlog_enrollments.count()

    graded_enrollments = Grade.objects.filter(enrollment__in=non_backlog_enrollments).count()
    if graded_enrollments != non_backlog_count:
        return None

    total_points = Decimal("0")
    total_credits = 0

    grades = Grade.objects.filter(enrollment__in=non_backlog_enrollments).select_related(
        "enrollment",
        "grade_letter",
        "enrollment__offering__course",
        "enrollment__offering",
    )
    grades_by_enrollment = {grade.enrollment_id: grade for grade in grades}

    for enrollment in enrollments:
        credits = enrollment.offering.course.credits
        total_credits += credits

        if enrollment.status == Enrollment.EnrollmentStatus.BACKLOG:
            continue

        grade = grades_by_enrollment.get(enrollment.enrollment_id)
        if not grade:
            return None

        total_points += grade.grade_letter.grade_point * Decimal(credits)

    if total_credits == 0:
        return None

    spi = (total_points / Decimal(total_credits)).quantize(Decimal("0.01"))
    first_enrollment = enrollments.first()
    academic_year = first_enrollment.offering.academic_year if first_enrollment else ""
    SemesterResult.objects.update_or_create(
        student=student,
        academic_year=academic_year,
        semester_no=semester,
        defaults={
            "spi": spi,
            "credits_earned_sem": total_credits,
        },
    )
    completed_semesters = SemesterResult.objects.filter(student=student, spi__isnull=False)
    cpi_weighted_sum = Decimal("0")
    cpi_credit_total = 0
    for result in completed_semesters:
        semester_credits = result.credits_earned_sem or 0
        cpi_weighted_sum += Decimal(result.spi) * Decimal(semester_credits)
        cpi_credit_total += semester_credits

    if cpi_credit_total == 0:
        return spi

    student.curr_cpi = (cpi_weighted_sum / Decimal(cpi_credit_total)).quantize(Decimal("0.01"))
    student.save(update_fields=["curr_cpi"])
    return spi


def process_semester_results_core():
    promoted_students_count = 0
    skipped_students_count = 0
    processed_students_count = 0
    probation_students_count = 0
    discontinued_students_count = 0

    students = Student.objects.select_related("user")

    with transaction.atomic():
        for student in students:
            semester = student.current_semester

            enrollments = Enrollment.objects.filter(
                student=student,
                offering__semester_no=semester,
            ).select_related("offering__course")

            if enrollments.count() == 0:
                skipped_students_count += 1
                continue

            backlog_due_attendance_ids = []
            for enrollment in enrollments:
                attendance_percentage = calculate_attendance_percentage(enrollment)
                min_attendance = enrollment.offering.course.min_attendance_req
                if attendance_percentage < min_attendance:
                    backlog_due_attendance_ids.append(enrollment.enrollment_id)

            enrollments.update(
                status=Enrollment.EnrollmentStatus.ONGOING,
                failure_reason=None,
            )

            if backlog_due_attendance_ids:
                Enrollment.objects.filter(
                    enrollment_id__in=backlog_due_attendance_ids
                ).update(
                    status=Enrollment.EnrollmentStatus.BACKLOG,
                    failure_reason=Enrollment.FailureReason.ATTENDANCE,
                )

            remaining_enrollments = enrollments.exclude(
                enrollment_id__in=backlog_due_attendance_ids
            )

            graded_remaining_ids = list(
                Grade.objects.filter(enrollment__in=remaining_enrollments).values_list(
                    "enrollment_id", flat=True
                )
            )
            backlog_due_grade_ids = list(
                Grade.objects.filter(
                    enrollment__in=remaining_enrollments,
                    grade_letter_id="F",
                ).values_list("enrollment_id", flat=True)
            )
            backlog_due_grade_set = set(backlog_due_grade_ids)

            pass_ids = [
                enrollment_id
                for enrollment_id in graded_remaining_ids
                if enrollment_id not in backlog_due_grade_set
            ]

            if backlog_due_grade_ids:
                Enrollment.objects.filter(enrollment_id__in=backlog_due_grade_ids).update(
                    status=Enrollment.EnrollmentStatus.BACKLOG,
                    failure_reason=Enrollment.FailureReason.GRADE,
                )

            if pass_ids:
                Enrollment.objects.filter(enrollment_id__in=pass_ids).update(
                    status=Enrollment.EnrollmentStatus.PASS,
                    failure_reason=None,
                )

            spi = calculate_spi(student, semester)

            print("Student:", student.user.name)
            print("Enrollments:", enrollments.count())
            print("SPI:", spi)
            print("CPI:", student.curr_cpi)

            if spi is None:
                skipped_students_count += 1
                continue

            cpi = student.curr_cpi
            if cpi < Decimal("4.0") and semester in [2, 4]:
                student.academic_status = "DISCONTINUED"
                discontinued_students_count += 1
            elif spi < Decimal("4.5") or cpi < Decimal("5.0"):
                student.academic_status = "PROBATION"
                probation_students_count += 1
            else:
                student.academic_status = "NORMAL"

            update_fields = ["academic_status"]
            if student.academic_status != "DISCONTINUED":
                student.current_semester = semester + 1
                update_fields.append("current_semester")
                promoted_students_count += 1

            student.save(update_fields=update_fields)
            processed_students_count += 1

    return {
        "processed_students_count": processed_students_count,
        "promoted_students_count": promoted_students_count,
        "probation_students_count": probation_students_count,
        "discontinued_students_count": discontinued_students_count,
        "skipped_students_count": skipped_students_count,
    }


@login_required
def process_semester_results(request):
    admin_user = _get_admin_for_request(request)
    if not admin_user:
        return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

    if request.method != "POST":
        return _forbidden_page(request, detail_message="Invalid request method.")

    summary = process_semester_results_core()

    messages.success(
        request,
        (
            f"Processed {summary['processed_students_count']} students, "
            f"promoted {summary['promoted_students_count']}, "
            f"on probation {summary['probation_students_count']}, "
            f"discontinued {summary['discontinued_students_count']}, "
            f"skipped {summary['skipped_students_count']} due to incomplete grading"
        ),
    )
    return redirect("admin_system_dashboard")


@login_required
def manage_registration_windows(request):
    admin_user = _get_admin_for_request(request)
    if not admin_user:
        return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

    editing_window = None
    edit_window_id = request.GET.get("edit")
    if edit_window_id:
        editing_window = get_object_or_404(RegistrationWindow, pk=edit_window_id)

    if request.method == "POST":
        semester_no = request.POST.get("semester_no")
        academic_year = request.POST.get("academic_year", "").strip()
        start_datetime_raw = request.POST.get("start_datetime")
        end_datetime_raw = request.POST.get("end_datetime")
        window_id = request.POST.get("window_id")

        try:
            start_datetime = datetime.fromisoformat(start_datetime_raw)
            end_datetime = datetime.fromisoformat(end_datetime_raw)
        except (TypeError, ValueError):
            messages.error(request, "Please provide valid start and end datetimes.")
            return redirect("manage_registration_windows")

        if timezone.is_naive(start_datetime):
            start_datetime = timezone.make_aware(start_datetime, timezone.get_current_timezone())
        if timezone.is_naive(end_datetime):
            end_datetime = timezone.make_aware(end_datetime, timezone.get_current_timezone())

        if start_datetime >= end_datetime:
            messages.error(request, "Start datetime must be before end datetime.")
            return redirect("manage_registration_windows")

        if window_id:
            window = get_object_or_404(RegistrationWindow, pk=window_id)
            if RegistrationWindow.objects.filter(
                semester_no=semester_no,
                academic_year=academic_year,
            ).exclude(pk=window.pk).exists():
                messages.error(request, "A registration window already exists for this semester and academic year.")
                return redirect("manage_registration_windows")

            window.semester_no = semester_no
            window.academic_year = academic_year
            window.start_datetime = start_datetime
            window.end_datetime = end_datetime
            window.save()
            messages.success(request, "Registration window updated successfully.")
        else:
            if RegistrationWindow.objects.filter(
                semester_no=semester_no,
                academic_year=academic_year,
            ).exists():
                messages.error(request, "A registration window already exists for this semester and academic year.")
                return redirect("manage_registration_windows")

            RegistrationWindow.objects.create(
                semester_no=semester_no,
                academic_year=academic_year,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
            messages.success(request, "Registration window created successfully.")

        return redirect("manage_registration_windows")

    windows = RegistrationWindow.objects.all().order_by("academic_year", "semester_no")
    return render(
        request,
        "academics/manage_registration_windows.html",
        {
            "windows": windows,
            "editing_window": editing_window,
        },
    )


@login_required
def admin_analytics_dashboard(request):
    admin_user = _get_admin_for_request(request)
    if not admin_user:
        return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

    program_stats = list(
        Student.objects.select_related("program")
        .values("program__degree", "program__branch")
        .annotate(
            avg_cpi=Avg("curr_cpi"),
            total_students=Count("pk"),
            placed_students=Count("finaloutcome", distinct=True),
        )
        .order_by("program__degree", "program__branch")
    )
    for stat in program_stats:
        total_students = stat["total_students"] or 0
        placed_students = stat["placed_students"] or 0
        stat["placement_rate"] = (placed_students * 100.0 / total_students) if total_students else 0

    placed_students_count = FinalOutcome.objects.count()

    top_recruiting_companies = (
        Application.objects.filter(status="Accepted")
        .values("placement_offer__company__company_name")
        .annotate(total_hires=Count("pk"))
        .order_by("-total_hires", "placement_offer__company__company_name")
    )

    faculty_teaching_load = (
        Enrollment.objects.filter(faculty__isnull=False)
        .values("faculty__user__name")
        .annotate(
            student_count=Count("student"),
            offering_count=Count("offering", distinct=True),
        )
        .order_by("-student_count", "faculty__user__name")
    )

    return render(
        request,
        "academics/admin_analytics_dashboard.html",
        {
            "program_stats": program_stats,
            "placed_students_count": placed_students_count,
            "top_recruiting_companies": top_recruiting_companies,
            "faculty_teaching_load": faculty_teaching_load,
        },
    )


@login_required
def admin_system_dashboard(request):
    admin_user = _get_admin_for_request(request)
    if not admin_user:
        return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

    from placements.models import PlacementOffer

    metrics = {
        "total_students": Student.objects.count(),
        "total_faculty": Faculty.objects.count(),
        "total_courses": Course.objects.count(),
        "total_course_offerings": CourseOffering.objects.count(),
        "total_enrollments": Enrollment.objects.count(),
        "total_placement_offers": PlacementOffer.objects.count(),
        "total_applications": Application.objects.count(),
    }

    return render(
        request,
        "academics/admin_system_dashboard.html",
        {
            "metrics": metrics,
            "admin_user": admin_user,
        },
    )