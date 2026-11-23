import calendar
import hashlib
from datetime import date

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.audit.services import record
from apps.tenancy.permissions import has_perm, site_view

from . import grid, ledger_grid, setup
from .forms import UploadForm
from .models import ImportJob
from .xlsx import SheetError

MODULES = {ImportJob.Kind.SETUP: setup, ImportJob.Kind.GRID: grid, ImportJob.Kind.LEDGER: ledger_grid}


def _parse(kind: str, data: bytes, options: dict):
    if kind == ImportJob.Kind.LEDGER:
        options["lock"] = False  # geçmiş aylar tek tek yazılır; kilit gerekirse sonra Muhasebe ayarlarından
        return ledger_grid.parse(data, options)
    if kind == ImportJob.Kind.GRID:
        payload, issues = grid.parse(data, options)
        if not options.get("opening_date"):
            last = date.fromisoformat(payload["months"][-1])
            options["opening_date"] = last.replace(day=calendar.monthrange(last.year, last.month)[1]).isoformat()
        return payload, issues
    return setup.parse(data)


@site_view("setup.manage")
def setup_home(request, site):
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload = form.cleaned_data["file"]
        data = upload.read()
        options = form.options()
        options["lock"] = options["lock"] and has_perm(request, "ledger.lock", site.pk)
        kind = form.cleaned_data["kind"]
        try:
            payload, issues = _parse(kind, data, options)
        except SheetError as exc:
            form.add_error("file", str(exc))
        else:
            digest = hashlib.sha256(data).hexdigest()
            with transaction.atomic():
                job = ImportJob.objects.create(
                    site=site,
                    kind=kind,
                    file_name=upload.name[:200],
                    file_sha256=digest,
                    options=options,
                    payload=payload,
                    issues=issues.as_list(),
                    created_by=request.user,
                )
                record("import.uploaded", job, {"tur": kind, "dosya": job.file_name}, site_id=site.pk)
            return redirect("imports:job_detail", site_id=site.pk, job_id=job.pk)
    jobs = ImportJob.objects.filter(site=site).select_related("created_by")[:20]
    return render(request, "imports/setup_home.html", {"site": site, "form": form, "jobs": jobs})


@site_view("setup.manage")
def template_download(request, site):
    response = HttpResponse(
        setup.build_template(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = 'attachment; filename="kurulum-sablonu.xlsx"'
    return response


@site_view("setup.manage")
def job_detail(request, site, job_id: int):
    job = get_object_or_404(ImportJob, pk=job_id, site=site)
    module = MODULES[job.kind]
    ctx = {"site": site, "job": job}
    if job.status == ImportJob.Status.PREVIEW:
        p = module.plan(site, job.payload, job.options)
        issues = job.issues + p.issues.as_list()
        order = {"error": 0, "warning": 1, "info": 2}
        ctx.update(
            {
                "plan": p,
                "summary": p.summary,
                "issues": sorted(issues, key=lambda i: order[i["level"]]),
                "errors": sum(1 for i in issues if i["level"] == "error"),
                "duplicate": ImportJob.objects.filter(
                    site=site, file_sha256=job.file_sha256, status=ImportJob.Status.APPLIED
                ).first(),
                "ledger_totals": {
                    "charged": sum(r.charged for r in getattr(p, "ledger_rows", [])),
                    "paid": sum(r.paid for r in getattr(p, "ledger_rows", [])),
                    "opening": sum(r.opening for r in getattr(p, "ledger_rows", [])),
                    "balance": sum(r.balance for r in getattr(p, "ledger_rows", [])),
                },
                "grid_totals": {
                    "expected": sum(r.expected for r in p.grid_rows),
                    "paid": sum(r.paid for r in p.grid_rows),
                    "owed": sum(r.owed for r in p.grid_rows),
                    "advance": sum(r.advance for r in p.grid_rows),
                },
            }
        )
    else:
        ctx.update({"summary": job.summary, "issues": job.issues})
    return render(request, "imports/job_detail.html", ctx)


@site_view("setup.manage")
def job_apply(request, site, job_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    job = get_object_or_404(ImportJob, pk=job_id, site=site)
    if job.status != ImportJob.Status.PREVIEW:
        messages.error(request, "Bu dosya zaten işlendi.")
        return redirect("imports:job_detail", site_id=site.pk, job_id=job.pk)
    try:
        with transaction.atomic():
            job = ImportJob.objects.select_for_update().get(pk=job.pk)
            if job.status != ImportJob.Status.PREVIEW:
                raise setup.ApplyError("Bu dosya zaten işlendi.")
            p = MODULES[job.kind].apply(site, job.payload, job.options, request.user)
            job.status = ImportJob.Status.APPLIED
            job.summary = p.summary
            job.issues = job.issues + p.issues.as_list()
            job.payload = {}  # kişisel veriler önizleme bitince tutulmaz
            job.applied_at, job.applied_by = timezone.now(), request.user
            job.save()
            record("import.applied", job, p.summary, site_id=site.pk)
    except setup.ApplyError as exc:
        messages.error(request, str(exc))
        return redirect("imports:job_detail", site_id=site.pk, job_id=job.pk)
    messages.success(request, "İçe aktarma tamamlandı.")
    return redirect("imports:job_detail", site_id=site.pk, job_id=job.pk)


@site_view("setup.manage")
def job_discard(request, site, job_id: int):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    job = get_object_or_404(ImportJob, pk=job_id, site=site, status=ImportJob.Status.PREVIEW)
    with transaction.atomic():
        job.status, job.payload = ImportJob.Status.DISCARDED, {}
        job.save(update_fields=["status", "payload", "updated_at"])
        record("import.discarded", job, site_id=site.pk)
    messages.info(request, "Dosyadan vazgeçildi; hiçbir kayıt değişmedi.")
    return redirect("imports:setup_home", site_id=site.pk)
