from django.urls import path
from . import views

urlpatterns = [
    path("student/dashboard/", views.student_dashboard, name="student_dashboard"),
    path("student/transcript/", views.student_transcript, name="student_transcript"),
    path("transcript/", views.student_transcript, name="academics_transcript"),
    path("faculty/dashboard/", views.faculty_dashboard, name="faculty_dashboard"),
    path("admin/faculty-teaching-load/", views.faculty_teaching_load, name="faculty_teaching_load"),
    path("admin/system-dashboard/", views.admin_system_dashboard, name="admin_system_dashboard"),
    path("admin/analytics/", views.admin_analytics_dashboard, name="admin_analytics_dashboard"),
    path("admin/process-semester-results/", views.process_semester_results, name="process_semester_results"),
    path("admin/allocate-faculty/<int:offering_id>/", views.admin_allocate_faculty, name="admin_allocate_faculty"),
    path("admin/assign-faculty/", views.assign_faculty_to_enrollment, name="assign_faculty_to_enrollment"),
    path("course-students/<int:offering_id>/", views.course_students, name="course_students"),
    path("mark-attendance/<int:offering_id>/", views.mark_attendance, name="mark_attendance"),
    path("add-to-cart/<int:offering_id>/", views.add_to_cart, name="add_to_cart"),
    path("submit-enrollment/", views.submit_enrollment, name="submit_enrollment"),
    path("remove-from-cart/<int:cart_id>/", views.remove_from_cart, name="remove_from_cart"),
    path("manage-registration-windows/", views.manage_registration_windows, name="manage_registration_windows"),
    path("manage-assessments/<int:offering_id>/", views.manage_assessments, name="manage_assessments"),
    path("manage-grading/<int:offering_id>/", views.manage_grading_scheme, name="manage_grading_scheme"),
    path("upload-marks/<int:enrollment_id>/", views.upload_marks, name="upload_marks"),
]