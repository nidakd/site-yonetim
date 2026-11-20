"""Sakin portalı: telefon + SMS kodu ile giriş, bölümlerim, ekstre, makbuzlar, anonim site özeti."""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.debug import sensitive_post_parameters

from apps.audit.services import record
from apps.billing.services import open_charges
from apps.comms.sms import SmsError
from apps.core.context import tenant_context
from apps.core.middleware import client_ip
from apps.payments.models import Payment
from apps.reports.services import unit_balances, unit_statement
from apps.tenancy.models import Organization, Site
from domain.phone import mask_phone

from . import services
from .forms import CodeForm, PasswordLoginForm, PhoneForm, ResidentPasswordForm
from .models import ResidentAccount, ResidentConsent

PENDING = "resident_pending_phone"
THROTTLED = "Çok fazla deneme yapıldı. Lütfen 15 dakika sonra tekrar deneyin."


class Resident:
    def __init__(self, account: ResidentAccount, org_id: int, orgs: list[int]):
        self.account, self.org_id, self.orgs = account, org_id, orgs
        self.units = services.resident_units(account.phone)
        self.by_unit = {ru.unit.pk: ru for ru in self.units}

    @property
    def masked_phone(self) -> str:
        return mask_phone(self.account.phone)

    @property
    def sites(self) -> list[Site]:
        return list({ru.unit.site_id: ru.unit.site for ru in self.units}.values())


def resident_view(view=None, *, consent=True):
    """Sakin oturumu + kuruluş seçimi + aydınlatma onayı; görünüm o kuruluşun bağlamında çalışır."""

    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            data = request.session.get(services.SESSION_KEY)
            if not data:
                return redirect("residents:login")
            account = ResidentAccount.objects.filter(pk=data["account"], phone=data["phone"], is_active=True).first()
            orgs = services.orgs_for_phone(account.phone) if account else []
            if not orgs:
                services.end_session(request)
                messages.info(request, "Bu numarayla kayıtlı güncel bir bölüm yok. Yönetiminizle görüşün.")
                return redirect("residents:login")
            org_id = data.get("org")
            if org_id not in orgs:
                if len(orgs) > 1:
                    return redirect("residents:choose_org")
                org_id = orgs[0]
                request.session[services.SESSION_KEY] = {**data, "org": org_id}
            with tenant_context(org_id):
                request.resident = Resident(account, org_id, orgs)
                if (
                    consent
                    and not ResidentConsent.objects.filter(
                        account=account, version=settings.PRIVACY_NOTICE_VERSION
                    ).exists()
                ):
                    return redirect("residents:consent")
                if not data.get("audited"):  # her girişte bir kez, kuruluşun denetim izine
                    record("resident.login", account, {"telefon": request.resident.masked_phone})
                    request.session[services.SESSION_KEY] = {**request.session[services.SESSION_KEY], "audited": True}
                return func(request, *args, **kwargs)

        return wrapper

    return decorator(view) if view else decorator


# --- giriş ------------------------------------------------------------------------------------


def login_phone(request):
    if request.session.get(services.SESSION_KEY):
        return redirect("residents:home")
    form = PhoneForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        phone = form.cleaned_data["phone"]
        try:
            services.request_code(phone, client_ip(request))
        except services.Throttled:
            form.add_error(None, THROTTLED)
        except SmsError:
            form.add_error(None, "SMS şu anda gönderilemiyor. Lütfen daha sonra deneyin ya da yönetiminizle görüşün.")
        else:
            request.session[PENDING] = phone
            return redirect("residents:login_code")
    return render(request, "residents/login_phone.html", {"form": form})


def login_code(request):
    phone = request.session.get(PENDING)
    if not phone:
        return redirect("residents:login")
    form = CodeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if services.verify_code(phone, form.cleaned_data["code"]):
            request.session.pop(PENDING, None)
            account = services.account_for(phone)
            services.start_session(request, account, remember=form.cleaned_data["remember"])
            return redirect("residents:home")
        form.add_error("code", "Kod hatalı ya da süresi dolmuş. Yeni kod isteyebilirsiniz.")
    return render(request, "residents/login_code.html", {"form": form, "masked": mask_phone(phone)})


@sensitive_post_parameters("password")
def login_password(request):
    form = PasswordLoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        phone, ip = form.cleaned_data["phone"], client_ip(request)
        if services.password_locked(phone, ip):
            form.add_error(None, THROTTLED)
        else:
            account = ResidentAccount.objects.filter(phone=phone, is_active=True).first()
            if account and account.check_password(form.cleaned_data["password"]) and services.orgs_for_phone(phone):
                services.start_session(request, account, remember=form.cleaned_data["remember"])
                return redirect("residents:home")
            services.password_failed(phone, ip)
            form.add_error(None, "Telefon ya da şifre hatalı. Şifreniz yoksa SMS koduyla girin.")
    return render(request, "residents/login_password.html", {"form": form})


def logout_view(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    services.end_session(request)
    messages.success(request, "Çıkış yapıldı.")
    return redirect("residents:login")


def choose_org(request):
    data = request.session.get(services.SESSION_KEY)
    if not data:
        return redirect("residents:login")
    orgs = services.orgs_for_phone(data["phone"])
    if request.method == "POST":
        try:
            org_id = int(request.POST.get("org", ""))
        except ValueError:
            org_id = None
        if org_id in orgs:
            request.session[services.SESSION_KEY] = {**data, "org": org_id}
            return redirect("residents:home")
    options = []
    for org in Organization.objects.filter(pk__in=orgs).order_by("name"):
        with tenant_context(org.pk):
            sites = sorted({ru.unit.site.name for ru in services.resident_units(data["phone"])})
        options.append({"org": org, "sites": sites})
    return render(request, "residents/choose_org.html", {"options": options})


@resident_view(consent=False)
def consent(request):
    resident = request.resident
    org = Organization.objects.get(pk=resident.org_id)
    if request.method == "POST":
        with transaction.atomic():
            ResidentConsent.objects.get_or_create(
                account=resident.account, version=settings.PRIVACY_NOTICE_VERSION, defaults={"ip": client_ip(request)}
            )
            record("resident.consent", resident.account, {"surum": settings.PRIVACY_NOTICE_VERSION})
        return redirect("residents:home")
    return render(
        request,
        "residents/consent.html",
        {"resident": resident, "org": org, "sites": resident.sites, "version": settings.PRIVACY_NOTICE_VERSION},
    )


# --- portal -----------------------------------------------------------------------------------


@resident_view
def home(request):
    resident = request.resident
    units = [ru.unit for ru in resident.units]
    balances = unit_balances(units[0].site, [u.pk for u in units]) if units else {}
    if len(resident.sites) > 1:  # bir kuruluşta birden çok sitede bölümü olabilir
        balances = {}
        for site in resident.sites:
            balances.update(unit_balances(site, [u.pk for u in units if u.site_id == site.pk]))
    cards = []
    for ru in resident.units:
        last = (
            Payment.objects.filter(unit=ru.unit, status=Payment.Status.VALID, date__gte=ru.visible_from)
            .exclude(channel=Payment.Channel.OPENING)
            .order_by("-date", "-id")
            .first()
        )
        cards.append({"ru": ru, "balance": balances.get(ru.unit.pk, 0), "last_payment": last})
    summaries = []
    for site in resident.sites:
        ps = services.portal_settings(site)
        if ps.show_collection_rate or ps.show_cash_balance or ps.show_expenses or ps.contact_note:
            summaries.append({"site": site, "settings": ps})
    return render(
        request,
        "residents/home.html",
        {"resident": resident, "cards": cards, "summaries": summaries, "org_count": len(resident.orgs)},
    )


def _resident_unit(request, unit_id: int):
    ru = request.resident.by_unit.get(unit_id)
    if ru is None:
        raise Http404
    return ru


@resident_view
def unit_detail(request, unit_id: int):
    ru = _resident_unit(request, unit_id)
    unit = ru.unit
    charges = open_charges(unit)
    for c in charges:
        c.percent = round(100 * c.paid / c.amount) if c.amount else 0
    payments = (
        Payment.objects.filter(unit=unit, date__gte=ru.visible_from)
        .prefetch_related("allocations__charge")
        .order_by("-date", "-id")[:50]
    )
    statement = unit_statement(unit, start=ru.visible_from)
    return render(
        request,
        "residents/unit_detail.html",
        {
            "resident": request.resident,
            "ru": ru,
            "unit": unit,
            "charges": charges,
            "debt": sum(c.remaining for c in charges),
            "balance": unit_balances(unit.site, [unit.pk]).get(unit.pk, 0),
            "payments": payments,
            "statement": list(reversed(statement))[:60],
        },
    )


@resident_view
def receipt(request, payment_id: int):
    payment = get_object_or_404(
        Payment.objects.select_related("unit__block", "unit__site", "account").exclude(channel=Payment.Channel.OPENING),
        pk=payment_id,
    )
    ru = _resident_unit(request, payment.unit_id)
    if payment.date < ru.visible_from:
        raise Http404
    allocations = payment.allocations.filter(from_advance=False).select_related("charge").order_by("charge__due_date")
    return render(
        request,
        "payments/receipt.html",
        {
            "site": payment.unit.site,
            "payment": payment,
            "allocations": allocations,
            "advance": payment.amount - sum(a.amount for a in allocations),
        },
    )


@resident_view
def site_summary(request, site_id: int):
    site = next((s for s in request.resident.sites if s.pk == site_id), None)
    if site is None:
        raise Http404
    ps = services.portal_settings(site)
    return render(
        request,
        "residents/site_summary.html",
        {"resident": request.resident, "site": site, "settings": ps, "overview": services.site_overview(site, ps)},
    )


@resident_view
@sensitive_post_parameters("password1", "password2")
def password_settings(request):
    account = request.resident.account
    form = ResidentPasswordForm(request.POST or None)
    if request.method == "POST":
        if request.POST.get("remove") and account.has_password:
            account.password = ""
            account.save(update_fields=["password"])
            messages.success(request, "Şifre kaldırıldı; girişte SMS kodu kullanılacak.")
            return redirect("residents:home")
        if form.is_valid():
            with transaction.atomic():
                account.set_password(form.cleaned_data["password1"])
                account.save(update_fields=["password"])
                record("resident.password_set", account)
            messages.success(request, "Şifreniz kaydedildi. Artık telefon ve şifreyle de girebilirsiniz.")
            return redirect("residents:home")
    return render(request, "residents/password.html", {"resident": request.resident, "form": form, "account": account})
