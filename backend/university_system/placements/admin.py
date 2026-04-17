from django.contrib import admin
from .models import Company, PlacementOffer, Application, FinalOutcome

admin.site.register(Company)


@admin.register(PlacementOffer)
class PlacementOfferAdmin(admin.ModelAdmin):
	list_display = (
		"company",
		"role_name",
		"offer_type",
		"min_cpi",
		"min_semester",
		"max_semester",
		"max_backlogs",
		"application_deadline",
	)
	list_filter = ("offer_type", "min_semester", "company")
	fields = (
		"company",
		"role_name",
		"package_ctc",
		"offer_type",
		"min_cpi",
		"allowed_programs",
		"max_backlogs",
		"min_semester",
		"max_semester",
		"application_deadline",
	)


admin.site.register(Application)
admin.site.register(FinalOutcome)