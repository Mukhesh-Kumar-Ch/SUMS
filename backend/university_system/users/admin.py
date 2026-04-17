from django import forms
from django.contrib import admin
from django.contrib.auth import get_user_model

from .models import User, Student, Faculty, Admin


class UserAdminForm(forms.ModelForm):
	class Meta:
		model = User
		fields = ("name", "phone", "dob", "gender", "user_type")


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
	form = UserAdminForm
	list_display = ("name", "user_type", "email")
	fields = ("name", "phone", "dob", "gender", "user_type", "email")
	readonly_fields = ("email",)

	def save_model(self, request, obj, form, change):
		if not obj.auth_user_id:
			auth_model = get_user_model()
			base_username = (obj.email or obj.name or "user").strip().lower().replace(" ", "")
			if not base_username:
				base_username = "user"

			username = base_username
			counter = 1
			while auth_model.objects.filter(username=username).exists():
				username = f"{base_username}{counter}"
				counter += 1

			auth_user = auth_model(username=username, email=(obj.email or ""))
			auth_user.set_password("test123")
			auth_user.save()
			obj.auth_user = auth_user

		super().save_model(request, obj, form, change)


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
	list_display = ("user", "roll_number", "email", "program", "current_semester", "backlog_count")
	fields = ("user", "roll_number", "email", "admission_year", "program", "current_semester", "academic_status", "curr_cpi", "backlog_count")
	readonly_fields = ("roll_number", "email", "user")

	def email(self, obj):
		return obj.user.email
	email.short_description = "Email"


@admin.register(Faculty)
class FacultyAdmin(admin.ModelAdmin):
	list_display = ("user", "faculty_id", "email", "department", "designation")
	fields = ("user", "faculty_id", "email", "designation", "department", "experience", "qualification")
	readonly_fields = ("faculty_id", "email", "user")

	def email(self, obj):
		return obj.user.email
	email.short_description = "Email"


@admin.register(Admin)
class AdminAdmin(admin.ModelAdmin):
	list_display = ("user", "admin_id", "email", "role")
	fields = ("user", "admin_id", "email", "role")
	readonly_fields = ("admin_id", "email", "user")

	def email(self, obj):
		return obj.user.email
	email.short_description = "Email"