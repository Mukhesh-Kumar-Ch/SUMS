from django import forms
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.shortcuts import render
from django.urls import path
from django.utils.translation import ngettext

from users.models import Faculty

from .models import (
    Program,
    Department,
    Course,
    CourseOffering,
    Prerequisite,
    Enrollment,
    Attendance,
    AssessmentComponent,
    StudentMarks,
    GradeScale,
    CourseGradeScale,
    Grade,
    SemesterResult,
    CourseCart,
    RegistrationWindow
)
from .views import process_semester_results_core


class AssignFacultyForm(forms.Form):
    faculty = forms.ModelChoiceField(queryset=Faculty.objects.select_related("user"), required=True)


class PrerequisiteAdminForm(forms.ModelForm):
    class Meta:
        model = Prerequisite
        fields = "__all__"
        help_texts = {
            "min_grade_req": "Select minimum required GRADE LETTER (not grade point)",
        }


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    search_fields = ("course_code", "course_name")


@admin.register(Prerequisite)
class PrerequisiteAdmin(admin.ModelAdmin):
    form = PrerequisiteAdminForm
    list_display = ("course", "prereq_course", "min_grade_req")
    fields = ("course", "prereq_course", "min_grade_req")
    autocomplete_fields = ("course", "prereq_course")


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "offering", "faculty")
    list_filter = ("offering", "faculty")
    actions = ("assign_faculty_to_selected_enrollments", "process_semester_results")

    def assign_faculty_to_selected_enrollments(self, request, queryset):
        if "apply" in request.POST:
            form = AssignFacultyForm(request.POST)
            if form.is_valid():
                faculty = form.cleaned_data["faculty"]
                updated = queryset.update(faculty=faculty)
                self.message_user(
                    request,
                    ngettext(
                        "%d enrollment was updated successfully.",
                        "%d enrollments were updated successfully.",
                        updated,
                    )
                    % updated,
                    messages.SUCCESS,
                )
                return None
        else:
            form = AssignFacultyForm()

        context = {
            **self.admin_site.each_context(request),
            "title": "Assign Faculty to Selected Enrollments",
            "form": form,
            "queryset": queryset,
            "action_checkbox_name": ACTION_CHECKBOX_NAME,
            "opts": self.model._meta,
            "selected_action": "assign_faculty_to_selected_enrollments",
        }
        return render(request, "admin/academics/enrollment/assign_faculty.html", context)

    assign_faculty_to_selected_enrollments.short_description = "Assign Faculty to Selected Enrollments"

    def process_semester_results(self, request, queryset):
        if not request.user.is_staff:
            self.message_user(request, "You do not have permission to process semester results.", level=messages.ERROR)
            return None

        summary = process_semester_results_core()
        self.message_user(
            request,
            (
                f"Processed {summary['processed_students_count']} students, "
                f"promoted {summary['promoted_students_count']}, "
                f"on probation {summary['probation_students_count']}, "
                f"discontinued {summary['discontinued_students_count']}, "
                f"skipped {summary['skipped_students_count']} due to incomplete grading"
            ),
            level=messages.SUCCESS,
        )
        return None

    process_semester_results.short_description = "Process Semester Results"

admin.site.register(Program)
admin.site.register(Department)
admin.site.register(CourseOffering)
admin.site.register(Attendance)
admin.site.register(AssessmentComponent)
admin.site.register(StudentMarks)
admin.site.register(GradeScale)
admin.site.register(CourseGradeScale)
admin.site.register(Grade)
admin.site.register(SemesterResult)
admin.site.register(CourseCart)
admin.site.register(RegistrationWindow)