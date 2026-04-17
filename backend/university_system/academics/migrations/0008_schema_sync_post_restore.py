import django.db.models.deletion
from django.db import migrations, models


def populate_attendance_offering(apps, schema_editor):
    Attendance = apps.get_model("academics", "Attendance")
    for attendance in Attendance.objects.select_related("enrollment__offering").all():
        attendance.offering_id = attendance.enrollment.offering_id
        attendance.save(update_fields=["offering"])


class Migration(migrations.Migration):

    dependencies = [
        ("academics", "0007_coursegradescale_unique_offering_min_score"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendance",
            name="offering",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                to="academics.courseoffering",
            ),
        ),
        migrations.RunPython(populate_attendance_offering, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="attendance",
            name="offering",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                to="academics.courseoffering",
            ),
        ),
        migrations.AddIndex(
            model_name="attendance",
            index=models.Index(fields=["offering", "date"], name="attendance_offering_date_idx"),
        ),
        migrations.AddField(
            model_name="enrollment",
            name="status",
            field=models.CharField(
                choices=[("PASS", "Pass"), ("BACKLOG", "Backlog"), ("ONGOING", "Ongoing")],
                default="ONGOING",
                max_length=20,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="enrollment",
            name="unique_student_course_enrollment",
        ),
        migrations.AddConstraint(
            model_name="enrollment",
            constraint=models.UniqueConstraint(
                fields=("student", "offering", "attempt_no"),
                name="unique_student_course_enrollment_attempt",
            ),
        ),
        migrations.RemoveField(
            model_name="prerequisite",
            name="min_grade_req",
        ),
        migrations.AddField(
            model_name="prerequisite",
            name="min_grade_req",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                to="academics.gradescale",
            ),
        ),
        migrations.AlterField(
            model_name="assessmentcomponent",
            name="type",
            field=models.CharField(
                choices=[
                    ("MIDSEM", "Midsem"),
                    ("ENDSEM", "Endsem"),
                    ("QUIZ", "Quiz"),
                    ("PROJECT", "Project"),
                    ("LAB", "Lab"),
                    ("ASSIGNMENT", "Assignment"),
                    ("ATTENDANCE", "Attendance"),
                ],
                max_length=50,
            ),
        ),
    ]
