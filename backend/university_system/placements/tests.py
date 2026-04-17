from datetime import date
from threading import Barrier, Thread

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from academics.models import Program
from users.models import Student, User as ProfileUser

from .models import Application, Company, PlacementOffer


class OfferAcceptanceConcurrencyTests(TransactionTestCase):
	reset_sequences = True

	def setUp(self):
		self.program = Program.objects.create(degree="BTech", branch="CSE", duration_years=4)

		auth_user = get_user_model().objects.create_user(
			username="student1",
			password="pass1234",
			email="student1@example.com",
		)
		self.profile_user = ProfileUser.objects.create(
			auth_user=auth_user,
			name="Student One",
			email="student1@example.com",
			phone="9999999999",
			dob=date(2003, 1, 1),
			gender="Other",
			user_type=ProfileUser.UserType.STUDENT,
		)
		self.student = Student.objects.get(user=self.profile_user)
		self.student.roll_number = "24CSE001"
		self.student.admission_year = 2024
		self.student.program = self.program
		self.student.current_semester = 6
		self.student.academic_status = "ACTIVE"
		self.student.curr_cpi = "8.10"
		self.student.backlog_count = 0
		self.student.save()

		company = Company.objects.create(
			company_name="Acme",
			contact="John",
			email="hr@acme.com",
			industry_type="Software",
		)
		self.offer_a = PlacementOffer.objects.create(
			company=company,
			role_name="SDE A",
			package_ctc="18.00",
			offer_type=PlacementOffer.OfferType.JOB,
			min_cpi="7.00",
			max_backlogs=0,
			min_semester=5,
			max_semester=8,
			application_deadline=timezone.now() + timezone.timedelta(days=10),
		)
		self.offer_b = PlacementOffer.objects.create(
			company=company,
			role_name="SDE B",
			package_ctc="17.00",
			offer_type=PlacementOffer.OfferType.JOB,
			min_cpi="7.00",
			max_backlogs=0,
			min_semester=5,
			max_semester=8,
			application_deadline=timezone.now() + timezone.timedelta(days=10),
		)

		self.app_a = Application.objects.create(
			student=self.student,
			placement_offer=self.offer_a,
			status="Offered",
		)
		self.app_b = Application.objects.create(
			student=self.student,
			placement_offer=self.offer_b,
			status="Offered",
		)

		self.auth_user = auth_user

	def _accept_in_parallel(self, client, offer_id, barrier, results, key):
		barrier.wait()
		response = client.get(reverse("accept_offer", args=[offer_id]))
		results[key] = response.status_code

	def _student_client(self):
		client = Client()
		client.force_login(self.auth_user)
		return client

	@override_settings(ACCEPT_OFFER_LOCK_HOLD_SECONDS=0.2)
	def test_parallel_accept_requests_allow_only_one_accepted_offer(self):
		barrier = Barrier(2)
		results = {}
		client_a = Client()
		client_b = Client()
		client_a.force_login(self.auth_user)
		client_b.force_login(self.auth_user)

		t1 = Thread(target=self._accept_in_parallel, args=(client_a, self.offer_a.id, barrier, results, "offer_a"))
		t2 = Thread(target=self._accept_in_parallel, args=(client_b, self.offer_b.id, barrier, results, "offer_b"))

		t1.start()
		t2.start()
		t1.join()
		t2.join()

		self.app_a.refresh_from_db()
		self.app_b.refresh_from_db()

		accepted_count = Application.objects.filter(student=self.student, status="Accepted").count()
		rejected_count = Application.objects.filter(student=self.student, status="Rejected").count()

		# Log results for concurrency demonstration.
		print("Concurrency results:", results)
		print("Final statuses:", {"offer_a": self.app_a.status, "offer_b": self.app_b.status})

		self.assertEqual(accepted_count, 1)
		self.assertEqual(rejected_count, 1)
		self.assertIn(self.app_a.status, {"Accepted", "Rejected"})
		self.assertIn(self.app_b.status, {"Accepted", "Rejected"})
		self.assertNotEqual(self.app_a.status, self.app_b.status)
		self.assertEqual(results.get("offer_a"), 302)
		self.assertEqual(results.get("offer_b"), 302)

	def test_reject_offer_updates_status_when_not_accepted(self):
		client = self._student_client()
		response = client.get(reverse("reject_offer", args=[self.offer_a.id]))

		self.app_a.refresh_from_db()
		self.app_b.refresh_from_db()

		self.assertEqual(response.status_code, 302)
		self.assertEqual(self.app_a.status, "Rejected")
		self.assertEqual(self.app_b.status, "Offered")

	def test_reject_offer_blocked_after_any_acceptance(self):
		client = self._student_client()

		response_accept = client.get(reverse("accept_offer", args=[self.offer_a.id]))
		response_reject_other = client.get(reverse("reject_offer", args=[self.offer_b.id]))

		self.app_a.refresh_from_db()
		self.app_b.refresh_from_db()

		self.assertEqual(response_accept.status_code, 302)
		self.assertEqual(response_reject_other.status_code, 302)
		self.assertEqual(self.app_a.status, "Accepted")
		self.assertEqual(self.app_b.status, "Rejected")
