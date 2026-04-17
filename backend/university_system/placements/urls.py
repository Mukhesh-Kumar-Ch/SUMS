from django.urls import path

from . import views

urlpatterns = [
    path("student/dashboard/", views.student_placement_dashboard, name="student_placement_dashboard"),
    path("student/", views.student_placement_dashboard, name="student_placement_dashboard_root"),
    path("apply/<int:offer_id>/", views.apply_for_offer, name="apply_for_offer"),
    path("accept-offer/<int:offer_id>/", views.accept_offer, name="accept_offer"),
    path("reject-offer/<int:offer_id>/", views.reject_offer, name="reject_offer"),
    path("admin/placements/", views.admin_dashboard, name="placements_admin_dashboard"),
    path("admin/statistics/", views.placement_statistics_dashboard, name="placement_statistics_dashboard"),
    path("admin/applications/", views.admin_applications_dashboard, name="admin_applications_dashboard"),
    path("admin/update-status/", views.update_application_status, name="update_application_status"),
    path("applicants/<int:offer_id>/", views.view_applicants, name="placements_view_applicants"),
]
