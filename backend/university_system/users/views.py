from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from .models import User as ProfileUser


def _is_safe_next_url(request, next_url):
	if not next_url:
		return False
	return url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()})


def _ensure_auth_user_for_profile(profile_user):
	auth_user = profile_user.auth_user
	auth_model = get_user_model()
	email = (profile_user.email or "").strip().lower()

	if auth_user is None:
		base_username = email.split("@")[0] if email and "@" in email else email
		if not base_username:
			base_username = f"user{profile_user.pk}"

		username = base_username
		counter = 1
		while auth_model.objects.filter(username=username).exists():
			username = f"{base_username}{counter}"
			counter += 1

		auth_user, _ = auth_model.objects.get_or_create(
			username=username,
			defaults={"email": email or ""},
		)
		profile_user.auth_user = auth_user
		profile_user.save(update_fields=["auth_user"])

	if email and auth_user.email != email:
		auth_user.email = email
		auth_user.save(update_fields=["email"])

	return auth_user


def login_view(request):
	next_url = request.GET.get("next") or request.POST.get("next")

	if request.user.is_authenticated:
		try:
			profile_user = ProfileUser.objects.get(auth_user=request.user)
		except ProfileUser.DoesNotExist:
			profile_user = None

		if _is_safe_next_url(request, next_url):
			return redirect(next_url)

		if profile_user:
			if profile_user.user_type == ProfileUser.UserType.STUDENT:
				return redirect("student_dashboard")
			if profile_user.user_type == ProfileUser.UserType.FACULTY:
				return redirect("faculty_dashboard")
			if profile_user.user_type == ProfileUser.UserType.ADMIN:
				return redirect("admin_system_dashboard")

	if request.method == "POST":
		email = request.POST.get("email", "").strip().lower()
		password = request.POST.get("password", "")

		try:
			profile_user = ProfileUser.objects.get(email__iexact=email)
		except ProfileUser.DoesNotExist:
			messages.error(request, "Invalid email or password.")
			return render(request, "registration/login.html", {"email": email, "next": next_url})

		auth_user = _ensure_auth_user_for_profile(profile_user)

		# First attempt normal authentication with Django auth credentials.
		user = authenticate(request, username=auth_user.username, password=password)

		if user is None:
			messages.error(request, "Invalid email or password.")
			return render(request, "registration/login.html", {"email": email, "next": next_url})

		login(request, user)
		if _is_safe_next_url(request, next_url):
			return redirect(next_url)

		if profile_user.user_type == ProfileUser.UserType.STUDENT:
			return redirect("student_dashboard")
		if profile_user.user_type == ProfileUser.UserType.FACULTY:
			return redirect("faculty_dashboard")
		if profile_user.user_type == ProfileUser.UserType.ADMIN:
			return redirect("admin_system_dashboard")

		messages.error(request, "Unknown user role.")
		logout(request)
		return redirect("login")

	return render(request, "registration/login.html", {"next": next_url})


def logout_view(request):
	logout(request)
	return redirect("login")


@login_required(login_url="login")
def change_password(request):
	if request.method == "POST":
		old_password = request.POST.get("old_password", "").strip()
		new_password = request.POST.get("new_password", "").strip()
		confirm_password = request.POST.get("confirm_password", "").strip()

		# Check old password
		if not request.user.check_password(old_password):
			messages.error(request, "Incorrect old password")
			return redirect("change_password")

		# Check new passwords match
		if new_password != confirm_password:
			messages.error(request, "Passwords do not match")
			return redirect("change_password")

		# Check new password is not empty
		if not new_password:
			messages.error(request, "New password cannot be empty")
			return redirect("change_password")

		# Set new password (hashed)
		request.user.set_password(new_password)
		request.user.save()

		messages.success(request, "Password changed successfully. Please login again.")
		return redirect("login")

	return render(request, "users/change_password.html")
