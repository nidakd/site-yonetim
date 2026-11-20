from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.audit.services import record
from apps.core.forms import EndDateForm
from apps.core.utils import changes, snapshot
from apps.property.models import Unit
from apps.tenancy.permissions import site_view

from . import services
from .forms import OccupancyForm, PersonForm
from .models import Occupancy, Person, active_on

PERSONAL_FIELDS = {"first_name", "last_name", "phone", "email", "notes"}


def site_people(site):
    return Person.objects.filter(occupancies__unit__site=site).distinct()


@site_view("people.view")
def person_list(request, site):
    people = site_people(site)
    q = request.GET.get("q", "").strip()
    if q:
        people = people.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(phone__icontains=q))
    show_all = request.GET.get("tumu") == "1"
    today = timezone.localdate()
    rows = []
    for person in people.prefetch_related("occupancies__unit__block"):
        occs = [o for o in person.occupancies.all() if o.unit.site_id == site.pk]
        current = [o for o in occs if o.is_active_on(today)]
        if current or show_all:
            rows.append({"person": person, "current": current, "past": len(occs) - len(current)})
    return render(request, "people/person_list.html", {"site": site, "rows": rows, "q": q, "show_all": show_all})


@site_view("people.view")
def person_detail(request, site, person_id: int):
    person = get_object_or_404(site_people(site), pk=person_id)
    occupancies = person.occupancies.select_related("unit__block").order_by("end_date", "-start_date")
    return render(
        request,
        "people/person_detail.html",
        {
            "site": site,
            "person": person,
            "occupancies": occupancies,
            "today": timezone.localdate(),
            "can_anonymize": services.can_anonymize(person),
        },
    )


@site_view("people.manage")
def person_anonymize(request, site, person_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    person = get_object_or_404(site_people(site), pk=person_id)
    if request.POST.get("confirm") != "1":
        messages.error(request, "Onay kutusunu işaretleyin.")
    else:
        try:
            services.anonymize(person, site_id=site.pk)
        except services.AnonymizeError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Kişi anonimleştirildi. Muhasebe kayıtları korunur.")
    return redirect("people:person_detail", site_id=site.pk, person_id=person.pk)


@site_view("people.manage")
def person_update(request, site, person_id: int):
    person = get_object_or_404(site_people(site), pk=person_id)
    before = snapshot(person, PersonForm.Meta.fields)
    form = PersonForm(request.POST or None, instance=person)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            person = form.save()
            diff = changes(before, snapshot(person, PersonForm.Meta.fields), redact=PERSONAL_FIELDS)
            record("person.updated", person, diff, site_id=site.pk)
        messages.success(request, "Kişi bilgileri güncellendi.")
        return redirect("people:person_detail", site_id=site.pk, person_id=person.pk)
    return render(request, "people/person_form.html", {"site": site, "form": form, "person": person})


@site_view("people.manage")
def occupancy_create(request, site, unit_id: int):
    unit = get_object_or_404(Unit, pk=unit_id, site=site)
    form = OccupancyForm(request.POST or None, initial={"start_date": timezone.localdate(), "relation": "owner"})
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            person = d["person"]
            if person is None:
                person = Person.objects.create(
                    first_name=d["first_name"].strip(),
                    last_name=d["last_name"].strip(),
                    phone=d["phone"],
                    email=d.get("email") or "",
                )
                record("person.created", person, site_id=site.pk)
            occ = form.save(commit=False)
            occ.unit, occ.person = unit, person
            occ.save()
            record(
                "occupancy.created",
                occ,
                {"bolum": unit.pk, "kisi": person.pk, "iliski": occ.relation, "baslangic": str(occ.start_date)},
                site_id=site.pk,
            )
        messages.success(
            request, f"{person.full_name}, {unit.label} bölümüne {occ.get_relation_display().lower()} olarak eklendi."
        )
        return redirect("property:unit_detail", site_id=site.pk, unit_id=unit.pk)
    return render(request, "people/occupancy_form.html", {"site": site, "unit": unit, "form": form})


@site_view("people.manage")
def occupancy_end(request, site, occupancy_id: int):
    occ = get_object_or_404(Occupancy.objects.select_related("unit", "person"), pk=occupancy_id, unit__site=site)
    form = EndDateForm(request.POST or None, start=occ.start_date, initial={"end_date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            occ.end_date = form.cleaned_data["end_date"]
            occ.save(update_fields=["end_date", "updated_at"])
            record("occupancy.ended", occ, {"bitis": str(occ.end_date)}, site_id=site.pk)
        messages.success(request, "İlişki sonlandırıldı. Geçmiş kayıtlarda görünmeye devam eder.")
        return redirect("property:unit_detail", site_id=site.pk, unit_id=occ.unit_id)
    return render(request, "people/occupancy_end.html", {"site": site, "occ": occ, "form": form})


def current_people(unit):
    return unit.occupancies.filter(active_on(timezone.localdate())).select_related("person")
