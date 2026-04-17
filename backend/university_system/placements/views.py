from decimal import Decimal
import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import OperationalError, transaction
from django.db.models import Avg, Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from academics.models import Enrollment, Grade
from users.models import Admin, Student, User as ProfileUser

from .models import Application, Company, FinalOutcome, PlacementOffer


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
		return Student.objects.select_related("program", "user").get(user=profile_user)
	except Student.DoesNotExist:
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


def _get_class_awarded(cpi):
	cpi_value = Decimal(cpi)
	if cpi_value >= Decimal("7.00"):
		return "First Class"
	if cpi_value >= Decimal("6.00"):
		return "Second Class"
	return "Pass"


def _create_final_outcome(student):
	total_credits = sum(
		Grade.objects.filter(
			enrollment__student=student,
			is_counted_for_cpi=True,
		)
		.select_related("enrollment__offering__course")
		.values_list("enrollment__offering__course__credits", flat=True)
	)
	degree_awarded = student.program.degree
	graduation_year = timezone.now().year

	FinalOutcome.objects.update_or_create(
		student=student,
		defaults={
			"graduating_cpi": student.curr_cpi,
			"total_credits_earned": total_credits,
			"class_awarded": _get_class_awarded(student.curr_cpi),
			"degree_awarded": degree_awarded,
			"graduation_year": graduation_year,
		},
	)


@login_required
def student_placement_dashboard(request):
	student = _get_student_for_request(request)
	if not student:
		return _forbidden_page(request, required_role=ProfileUser.UserType.STUDENT)

	accepted_application = (
		Application.objects.filter(student=student, status="Accepted")
		.select_related("placement_offer__company")
		.order_by("-applied_at")
		.first()
	)
	has_accepted_offer = accepted_application is not None

	try:
		final_outcome = FinalOutcome.objects.get(student=student)
	except FinalOutcome.DoesNotExist:
		final_outcome = None
	if final_outcome:
		applications = Application.objects.filter(student=student).select_related("placement_offer__company")
		return render(
			request,
			"placements/student_dashboard.html",
			{
				"eligible_offers": [],
				"applications": applications,
				"final_outcome": final_outcome,
				"accepted_application": accepted_application,
				"has_accepted_offer": has_accepted_offer,
			},
		)

	student_sem = student.current_semester
	now = timezone.now()
	offers = (
		PlacementOffer.objects.select_related("company")
		.prefetch_related("allowed_programs")
		.filter(
			min_cpi__lte=student.curr_cpi,
			max_backlogs__gte=student.backlog_count,
			application_deadline__gt=now,
			min_semester__lte=student_sem,
			max_semester__gte=student_sem,
		)
		.filter(Q(allowed_programs__isnull=True) | Q(allowed_programs=student.program))
		.distinct()
	)
	applications = Application.objects.filter(student=student).select_related("placement_offer__company")
	application_by_offer = {application.placement_offer_id: application for application in applications}
	eligible_offers = []
	for offer in offers:
		offer.application = application_by_offer.get(offer.pk)
		eligible_offers.append(offer)

	return render(
		request,
		"placements/student_dashboard.html",
		{
			"eligible_offers": eligible_offers,
			"applications": applications,
			"final_outcome": None,
			"accepted_application": accepted_application,
			"has_accepted_offer": has_accepted_offer,
		},
	)


@login_required
def student_dashboard(request):
	return student_placement_dashboard(request)


@login_required
def apply_for_offer(request, offer_id):
	student = _get_student_for_request(request)
	if not student:
		return _forbidden_page(request, required_role=ProfileUser.UserType.STUDENT)

	offer = get_object_or_404(
		PlacementOffer.objects.select_related("company").prefetch_related("allowed_programs"),
		pk=offer_id,
	)
	if timezone.now() > offer.application_deadline:
		messages.error(request, "Application deadline has passed.")
		return redirect("student_placement_dashboard")

	if Application.objects.filter(student=student, placement_offer=offer).exists():
		messages.error(request, "You have already applied for this offer.")
		return redirect("student_placement_dashboard")

	if Application.objects.filter(student=student, status="Accepted").exists():
		messages.error(request, "You have already accepted an offer. No further actions allowed.")
		return redirect("student_placement_dashboard")

	if FinalOutcome.objects.filter(student=student).exists():
		messages.error(request, "You have already accepted a placement offer.")
		return redirect("student_placement_dashboard")

	if student.curr_cpi < offer.min_cpi:
		messages.error(request, "You do not satisfy the minimum CPI requirement for this offer.")
		return redirect("student_placement_dashboard")

	student_sem = student.current_semester
	if student_sem < offer.min_semester or student_sem > offer.max_semester:
		messages.error(request, "You are not eligible based on your current semester.")
		return redirect("student_placement_dashboard")

	allowed_programs = offer.allowed_programs.all()
	if allowed_programs.exists() and not allowed_programs.filter(pk=student.program_id).exists():
		messages.error(request, "Your program is not eligible for this placement offer.")
		return redirect("student_placement_dashboard")

	Application.objects.create(student=student, placement_offer=offer, status="Applied")
	messages.success(request, "Application submitted successfully.")
	return redirect("student_placement_dashboard")


@login_required
def admin_applications_dashboard(request):
	admin_user = _get_admin_for_request(request)
	if not admin_user:
		return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

	applications = Application.objects.select_related(
		"student__user",
		"placement_offer__company",
	).order_by("-applied_at")

	return render(
		request,
		"placements/admin_applications.html",
		{"applications": applications},
	)


@login_required
def placement_statistics_dashboard(request):
	admin_user = _get_admin_for_request(request)
	if not admin_user:
		return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

	students = Student.objects.select_related("program")
	active_offers = PlacementOffer.objects.select_related("company").prefetch_related("allowed_programs").filter(
		application_deadline__gte=timezone.now()
	)

	total_students_eligible = 0
	for student in students:
		is_eligible = False
		for offer in active_offers:
			if student.curr_cpi < offer.min_cpi:
				continue
			if student.backlog_count > offer.max_backlogs:
				continue
			student_sem = student.current_semester
			if student_sem < offer.min_semester or student_sem > offer.max_semester:
				continue
			allowed_programs = offer.allowed_programs.all()
			if allowed_programs.exists() and not allowed_programs.filter(pk=student.program_id).exists():
				continue
			is_eligible = True
			break
		if is_eligible:
			total_students_eligible += 1

	total_students_placed = FinalOutcome.objects.count()
	placement_percentage = (
		(total_students_placed * 100.0 / total_students_eligible) if total_students_eligible else 0
	)

	top_recruiting_companies = (
		Application.objects.filter(status="Accepted")
		.values("placement_offer__company__company_name")
		.annotate(total_hires=Count("pk"))
		.order_by("-total_hires", "placement_offer__company__company_name")
	)

	average_package_offered = (
		Application.objects.filter(status__in=["Offered", "Accepted"])
		.aggregate(avg_package=Avg("placement_offer__package_ctc"))
		.get("avg_package")
	)

	return render(
		request,
		"placements/placement_statistics_dashboard.html",
		{
			"total_students_eligible": total_students_eligible,
			"total_students_placed": total_students_placed,
			"placement_percentage": placement_percentage,
			"top_recruiting_companies": top_recruiting_companies,
			"average_package_offered": average_package_offered,
		},
	)


@login_required
def admin_dashboard(request):
	admin_user = _get_admin_for_request(request)
	if not admin_user:
		return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

	if request.method == "POST":
		action = request.POST.get("action")
		if action == "create_company":
			Company.objects.create(
				company_name=request.POST.get("company_name", "").strip(),
				contact=request.POST.get("contact", "").strip(),
				email=request.POST.get("email", "").strip(),
				industry_type=request.POST.get("industry_type", "").strip(),
			)
			messages.success(request, "Company created successfully.")
			return redirect("placements_admin_dashboard")

		if action == "create_offer":
			company = get_object_or_404(Company, pk=request.POST.get("company_id"))
			deadline_value = parse_datetime(request.POST.get("application_deadline", ""))
			if deadline_value is None:
				messages.error(request, "Please enter a valid application deadline.")
				return redirect("placements_admin_dashboard")
			if timezone.is_naive(deadline_value):
				deadline_value = timezone.make_aware(deadline_value, timezone.get_current_timezone())
			offer = PlacementOffer(
				company=company,
				role_name=request.POST.get("role_name", "").strip(),
				package_ctc=request.POST.get("package_ctc", "0").strip(),
				offer_type=(request.POST.get("offer_type") or "").strip() or None,
				min_cpi=request.POST.get("min_cpi", "0").strip(),
				max_backlogs=request.POST.get("max_backlogs", "0").strip(),
				min_semester=request.POST.get("min_semester", "1").strip(),
				max_semester=request.POST.get("max_semester", "8").strip(),
				application_deadline=deadline_value,
			)
			try:
				offer.full_clean()
			except ValidationError as exc:
				messages.error(request, str(exc))
				return redirect("placements_admin_dashboard")
			offer.save()
			program_ids = request.POST.getlist("allowed_programs")
			if program_ids:
				offer.allowed_programs.set(program_ids)
			messages.success(request, "Placement offer created successfully.")
			return redirect("placements_admin_dashboard")

	companies = Company.objects.all().order_by("company_name")
	offers = PlacementOffer.objects.select_related("company").prefetch_related("allowed_programs").all().order_by("company__company_name", "role_name")
	from academics.models import Program
	return render(
		request,
		"placements/admin_dashboard.html",
		{"companies": companies, "offers": offers, "programs": Program.objects.all().order_by("degree", "branch")},
	)


@login_required
def view_applicants(request, offer_id):
	admin_user = _get_admin_for_request(request)
	if not admin_user:
		return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

	offer = get_object_or_404(PlacementOffer.objects.select_related("company"), pk=offer_id)

	applications = Application.objects.filter(placement_offer=offer).select_related(
		"student__user", "student__program"
	).order_by("applied_at")
	return render(
		request,
		"placements/applicants.html",
		{"offer": offer, "applications": applications},
	)


@login_required
def update_application_status(request):
	admin_user = _get_admin_for_request(request)
	if not admin_user:
		return _forbidden_page(request, required_role=ProfileUser.UserType.ADMIN)

	if request.method != "POST":
		return _forbidden_page(request, detail_message="Invalid request method.")

	application_id = request.POST.get("application_id")

	application = get_object_or_404(
		Application.objects.select_related("student__program", "placement_offer"),
		pk=application_id,
	)
	new_status = request.POST.get("status")
	if new_status not in {"Shortlisted", "Offered", "Rejected"}:
		messages.error(request, "Invalid application status.")
		return redirect("admin_applications_dashboard")

	application.status = new_status
	application.save(update_fields=["status"])

	messages.success(request, "Application status updated successfully.")
	return redirect("admin_applications_dashboard")


@login_required
def accept_offer(request, offer_id):
	student = _get_student_for_request(request)
	if not student:
		return _forbidden_page(request, required_role=ProfileUser.UserType.STUDENT)

	try:
		max_attempts = 3
		for attempt in range(max_attempts):
			try:
				with transaction.atomic():
					# Lock the student row to serialize concurrent accept attempts.
					locked_student = Student.objects.select_for_update().get(user=request.user.profile)

					if Application.objects.filter(student=locked_student, status="Accepted").exists():
						messages.error(request, "You have already accepted an offer. No further actions allowed.")
						return redirect("student_placement_dashboard")

					application = get_object_or_404(
						Application.objects.select_related("placement_offer__company"),
						student=locked_student,
						placement_offer_id=offer_id,
					)

					if application.status not in {"Applied", "Shortlisted", "Offered"}:
						messages.error(request, "This application cannot be accepted in its current state.")
						return redirect("student_placement_dashboard")

					# Optional delay for concurrency test simulation.
					delay_seconds = float(getattr(settings, "ACCEPT_OFFER_LOCK_HOLD_SECONDS", 0) or 0)
					if delay_seconds > 0:
						time.sleep(delay_seconds)

					application.status = "Accepted"
					application.save(update_fields=["status"])

					Application.objects.filter(student=locked_student).exclude(id=application.id).update(status="Rejected")

					_create_final_outcome(locked_student)
					break
			except OperationalError as exc:
				if "locked" in str(exc).lower() and attempt < max_attempts - 1:
					time.sleep(0.05)
					continue
				raise

	except Student.DoesNotExist:
		messages.error(request, "Student profile not found.")
		return redirect("student_placement_dashboard")
	except Exception:
		messages.error(request, "Unable to accept offer right now. Please try again.")
		return redirect("student_placement_dashboard")

	messages.success(request, "Placement offer accepted successfully.")
	return redirect("student_placement_dashboard")


@login_required
def reject_offer(request, offer_id):
	student = _get_student_for_request(request)
	if not student:
		return _forbidden_page(request, required_role=ProfileUser.UserType.STUDENT)

	application = get_object_or_404(
		Application.objects.select_related("placement_offer__company"),
		student=student,
		placement_offer_id=offer_id,
	)

	accepted_application = Application.objects.filter(student=student, status="Accepted").first()
	if accepted_application and accepted_application.pk != application.pk:
		messages.error(request, "You have already accepted an offer. No further actions allowed.")
		return redirect("student_placement_dashboard")

	if application.status == "Accepted":
		messages.error(request, "Accepted offer cannot be rejected.")
		return redirect("student_placement_dashboard")

	if application.status == "Rejected":
		messages.info(request, "Application is already rejected.")
		return redirect("student_placement_dashboard")

	application.status = "Rejected"
	application.save(update_fields=["status"])
	messages.success(request, "Application rejected successfully.")
	return redirect("student_placement_dashboard")
