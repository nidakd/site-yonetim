from django.contrib import messages
from django.db import transaction
from django.db.models import Prefetch, Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.audit.services import record
from apps.core.utils import changes, snapshot
from apps.people.models import Occupancy, active_on
from apps.tenancy.permissions import site_view

from .forms import BlockForm, BulkUnitForm, UnitForm, UnitTypeForm
from .models import Block, Unit, UnitType


def units_with_people(site, *, archived=False):
    today = timezone.localdate()
    current = Occupancy.objects.filter(active_on(today)).select_related("person").order_by("relation", "start_date")
    return (
        Unit.objects.filter(site=site, is_archived=archived)
        .select_related("block", "unit_type")
        .prefetch_related(Prefetch("occupancies", queryset=current, to_attr="current_occupancies"))
    )


@site_view()
def unit_list(request, site):
    archived = request.GET.get("arsiv") == "1"
    units = units_with_people(site, archived=archived)
    block_id = request.GET.get("blok")
    if block_id and block_id.isdigit():
        units = units.filter(block_id=int(block_id))
    q = request.GET.get("q", "").strip()
    if q:
        today = timezone.localdate()
        person_match = Occupancy.objects.filter(active_on(today)).filter(
            Q(person__first_name__icontains=q) | Q(person__last_name__icontains=q) | Q(person__phone__icontains=q)
        )
        units = units.filter(Q(number__iexact=q) | Q(pk__in=person_match.values("unit_id")))
    from apps.reports.services import unit_balances

    units = list(units)
    balances = unit_balances(site, [u.pk for u in units])
    for u in units:
        u.balance = balances.get(u.pk, 0)
    return render(
        request,
        "property/unit_list.html",
        {
            "site": site,
            "units": units,
            "blocks": Block.objects.filter(site=site),
            "q": q,
            "block_id": block_id,
            "archived": archived,
        },
    )


@site_view("property.manage")
def unit_create(request, site):
    form = UnitForm(request.POST or None, site=site)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            unit = form.save()
            record("unit.created", unit, {"no": unit.label}, site_id=site.pk)
        messages.success(request, f"{unit.label} eklendi.")
        if "add_another" in request.POST:
            return redirect("property:unit_create", site_id=site.pk)
        return redirect("property:unit_detail", site_id=site.pk, unit_id=unit.pk)
    return render(request, "property/unit_form.html", {"site": site, "form": form, "title": "Yeni bölüm"})


@site_view("property.manage")
def unit_bulk_create(request, site):
    form = BulkUnitForm(request.POST or None, site=site)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        with transaction.atomic():
            for n in range(d["first"], d["last"] + 1):
                Unit.objects.create(
                    site=site, block=d["block"], number=str(n), kind=d["kind"], unit_type=d["unit_type"]
                )
            count = d["last"] - d["first"] + 1
            record(
                "unit.bulk_created",
                site,
                {"blok": d["block"].name if d["block"] else None, "ilk": d["first"], "son": d["last"]},
                site_id=site.pk,
            )
        messages.success(
            request, f"{count} bölüm eklendi. Arsa payı ve m² bilgilerini bölüm sayfasından girebilirsiniz."
        )
        return redirect("property:unit_list", site_id=site.pk)
    return render(request, "property/unit_bulk_form.html", {"site": site, "form": form})


@site_view()
def unit_detail(request, site, unit_id: int):
    unit = get_object_or_404(Unit.objects.select_related("block", "unit_type"), pk=unit_id, site=site)
    occupancies = unit.occupancies.select_related("person").order_by("end_date", "-start_date")
    today = timezone.localdate()
    from apps.billing.models import DuesAgreement
    from apps.reports.services import unit_open_charges, unit_statement

    return render(
        request,
        "property/unit_detail.html",
        {
            "site": site,
            "unit": unit,
            "current": [o for o in occupancies if o.is_active_on(today)],
            "past": [o for o in occupancies if not o.is_active_on(today)],
            "agreements": DuesAgreement.objects.filter(unit=unit).order_by("-valid_from"),
            "open_charges": unit_open_charges(unit),
            "statement": unit_statement(unit, limit=12),
            "today": today,
        },
    )


@site_view("property.manage")
def unit_update(request, site, unit_id: int):
    unit = get_object_or_404(Unit, pk=unit_id, site=site)
    fields = UnitForm.Meta.fields
    before = snapshot(unit, fields)
    form = UnitForm(request.POST or None, instance=unit, site=site)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            unit = form.save()
            record("unit.updated", unit, changes(before, snapshot(unit, fields)), site_id=site.pk)
        messages.success(request, "Bölüm güncellendi.")
        return redirect("property:unit_detail", site_id=site.pk, unit_id=unit.pk)
    return render(request, "property/unit_form.html", {"site": site, "form": form, "title": f"{unit.label} · düzenle"})


@site_view("property.manage")
def unit_archive(request, site, unit_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    unit = get_object_or_404(Unit, pk=unit_id, site=site)
    with transaction.atomic():
        unit.is_archived = not unit.is_archived
        unit.save(update_fields=["is_archived", "updated_at"])
        record("unit.archived" if unit.is_archived else "unit.restored", unit, site_id=site.pk)
    messages.success(request, "Bölüm arşivlendi." if unit.is_archived else "Bölüm arşivden çıkarıldı.")
    return redirect("property:unit_detail", site_id=site.pk, unit_id=unit.pk)


@site_view()
def structure(request, site):
    """Bloklar ve bölüm tipleri."""
    from apps.tenancy.permissions import has_perm

    can_manage = has_perm(request, "property.manage", site.pk)
    block_form = BlockForm(prefix="blok", site=site)
    type_form = UnitTypeForm(prefix="tip", site=site)
    if request.method == "POST":
        if not can_manage:
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied
        if "blok-name" in request.POST:
            block_form = BlockForm(request.POST, prefix="blok", site=site)
            if block_form.is_valid():
                with transaction.atomic():
                    block = block_form.save()
                    record("block.created", block, {"name": block.name}, site_id=site.pk)
                messages.success(request, f"{block.name} eklendi.")
                return redirect("property:structure", site_id=site.pk)
        else:
            type_form = UnitTypeForm(request.POST, prefix="tip", site=site)
            if type_form.is_valid():
                with transaction.atomic():
                    ut = type_form.save()
                    record("unit_type.created", ut, {"name": ut.name, "aidat": ut.default_amount}, site_id=site.pk)
                messages.success(request, f"{ut.name} eklendi.")
                return redirect("property:structure", site_id=site.pk)
    return render(
        request,
        "property/structure.html",
        {
            "site": site,
            "blocks": Block.objects.filter(site=site),
            "unit_types": UnitType.objects.filter(site=site),
            "block_form": block_form,
            "type_form": type_form,
            "can_manage": can_manage,
        },
    )


@site_view("property.manage")
def block_update(request, site, block_id: int):
    return _simple_edit(request, site, get_object_or_404(Block, pk=block_id, site=site), BlockForm, "block.updated")


@site_view("property.manage")
def unit_type_update(request, site, type_id: int):
    obj = get_object_or_404(UnitType, pk=type_id, site=site)
    return _simple_edit(request, site, obj, UnitTypeForm, "unit_type.updated")


def _simple_edit(request, site, obj, form_class, action):
    fields = form_class.Meta.fields
    before = snapshot(obj, fields)
    form = form_class(request.POST or None, instance=obj, site=site)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save()
            record(action, obj, changes(before, snapshot(obj, fields)), site_id=site.pk)
        messages.success(request, "Kaydedildi.")
        return redirect("property:structure", site_id=site.pk)
    return render(request, "property/simple_form.html", {"site": site, "form": form, "title": f"{obj} · düzenle"})
