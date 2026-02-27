from typing import Any
from uuid import uuid4
from pathlib import Path

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from planner.forms import PlannerWizardForm, SignUpForm, UserPersonalInfoForm
from planner.models import PackageOption, PlanRequest, SavedPackage, SavedPlace
from planner.services.plan_service import create_plan_request
from planner.services.provider_registry import provider_status
from planner.services.unsplash import get_rotating_hero_images

WIZARD_IDEMPOTENCY_SESSION_KEY = "planner_wizard_idempotency_key"
WHY_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}
VISIBLE_PACKAGE_LIMIT = 1


def _why_image_paths() -> list[str]:
    static_root = Path(__file__).resolve().parent / "static"
    # Prefer the user-provided image folder under static/img/why, keep legacy fallback.
    candidate_folders = [
        (static_root / "img" / "why", "img/why"),
        (static_root / "why", "why"),
    ]
    for folder, static_prefix in candidate_folders:
        if not folder.exists() or not folder.is_dir():
            continue
        files = sorted(
            item.name
            for item in folder.iterdir()
            if item.is_file() and item.suffix.lower() in WHY_IMAGE_EXTS
        )
        if files:
            return [f"{static_prefix}/{name}" for name in files]
    return []


def _sorted_packages(plan: PlanRequest, sort_mode: str):
    queryset = plan.package_options.select_related("flight_option", "hotel_option", "candidate").prefetch_related("tour_options")
    if sort_mode == "budget_first":
        queryset = queryset.order_by("-price_score", "estimated_total_min", "-score", "rank")
    elif sort_mode == "cheapest":
        queryset = queryset.order_by("estimated_total_min", "-score")
    elif sort_mode == "fastest":
        queryset = queryset.order_by("flight_option__duration_minutes", "estimated_total_min")
    elif sort_mode == "fewest_stops":
        queryset = queryset.order_by("flight_option__stops", "flight_option__duration_minutes", "estimated_total_min")
    elif sort_mode == "family_friendly":
        queryset = queryset.order_by("-quality_score", "-convenience_score", "estimated_total_min")
    elif sort_mode == "best_hotel":
        queryset = queryset.order_by("-quality_score", "estimated_total_min")
    else:
        queryset = queryset.order_by("-price_score", "-score", "estimated_total_min", "rank")
    return _dedupe_visible_packages(list(queryset))


def _visible_packages(plan: PlanRequest, sort_mode: str) -> list[PackageOption]:
    return _sorted_packages(plan, sort_mode)[:VISIBLE_PACKAGE_LIMIT]


def _pkg_norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _pkg_norm_link(value: Any) -> str:
    return str(value or "").strip()


def _package_visible_signature(package: PackageOption) -> tuple:
    breakdown = package.price_breakdown or {}
    component_summary = package.component_summary or {}
    flight_summary = component_summary.get("flight") or {}
    hotel_summary = component_summary.get("hotel") or {}
    tour_summary = component_summary.get("tours") or []
    return (
        _pkg_norm_text(package.candidate.airport_code),
        _pkg_norm_text(package.candidate.city_name),
        _pkg_norm_text(package.candidate.country_code),
        _pkg_norm_text(package.currency),
        str(breakdown.get("flight_total") or package.flight_option.total_price),
        str(breakdown.get("hotel_total") or package.hotel_option.total_price),
        str(breakdown.get("tours_total") or "0.00"),
        str(breakdown.get("package_total") or package.total_price),
        _pkg_norm_text(package.flight_option.origin_airport),
        _pkg_norm_text(package.flight_option.destination_airport),
        int(package.flight_option.stops or 0),
        int(package.flight_option.duration_minutes or 0),
        _pkg_norm_text(hotel_summary.get("title") or package.hotel_option.name),
        _pkg_norm_link(flight_summary.get("outbound_url") or package.flight_url or package.flight_option.deeplink_url),
        _pkg_norm_link(hotel_summary.get("outbound_url") or package.hotel_url or package.hotel_option.deeplink_url),
        _pkg_norm_link(package.tours_url),
        tuple(str(item) for item in (package.selected_tour_option_ids or [])),
        tuple(
            _pkg_norm_link((raw or {}).get("outbound_url") or (raw or {}).get("link"))
            for raw in tour_summary[:3]
            if isinstance(raw, dict)
        ),
    )


def _dedupe_visible_packages(packages: list[PackageOption]) -> list[PackageOption]:
    deduped: list[PackageOption] = []
    seen: set[tuple] = set()
    for package in packages:
        signature = _package_visible_signature(package)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(package)
    return deduped


def _plan_for_user_or_404(user, plan_id: str) -> PlanRequest:  # noqa: ANN001
    plan = get_object_or_404(PlanRequest, pk=plan_id)
    if plan.user_id != user.id:
        raise Http404("Plan not found")
    return plan


def _get_or_create_wizard_idempotency_key(request: HttpRequest) -> str:
    if not request.session.session_key:
        request.session.create()
    key = str(request.session.get(WIZARD_IDEMPOTENCY_SESSION_KEY) or "").strip()
    if key:
        return key
    key = uuid4().hex
    request.session[WIZARD_IDEMPOTENCY_SESSION_KEY] = key
    return key


@require_GET
def landing(request: HttpRequest) -> HttpResponse:
    context = {
        "hero_images": get_rotating_hero_images(),
        "providers": provider_status(),
    }
    return render(request, "planner/landing.html", context)


@require_GET
def healthz(_request: HttpRequest) -> HttpResponse:
    return HttpResponse("ok", content_type="text/plain")


@require_GET
def why_view(request: HttpRequest) -> HttpResponse:
    why_images = _why_image_paths()
    context = {
        "why_images": why_images,
        "why_image_count": len(why_images),
        "why_benefits": [
            {
                "icon": "⏱",
                "title": "Stop the comparison marathon",
                "body": "TriPPlanner reduces hours of tab-hopping across flight, hotel, and tour sites into ranked links-only packages with one clear total.",
            },
            {
                "icon": "📉",
                "title": "Price volatility made readable",
                "body": "Prices move constantly. We normalize signals and show a package breakdown so you can compare value without mentally rebuilding totals.",
            },
            {
                "icon": "🧮",
                "title": "Deterministic cost breakdowns",
                "body": "We separate flight, hotel, and tours totals so hidden-fee confusion and mismatched-date comparisons are easier to spot before you click out.",
            },
            {
                "icon": "🧭",
                "title": "Curated options, not endless search pages",
                "body": "Instead of overwhelming generic search results, you get curated package options ranked by price, convenience, and quality signals.",
            },
            {
                "icon": "🔍",
                "title": "Explainable ranking you can trust",
                "body": "Every package includes a Why ranked explanation and clear links-only transparency: no checkout, no lock-in, no hidden booking workflow inside TriPPlanner.",
            },
            {
                "icon": "🔗",
                "title": "Book anywhere you want",
                "body": "TriPPlanner is a links-only planner. We rank and route you to outbound affiliate links so you stay in control of where you actually book.",
            },
        ],
        "why_steps": [
            "Type your trip request (chat-like)",
            "We normalize airports, dates, and budget",
            "We fetch signals (flights, hotels, tours, places)",
            "We build real packages with a total cost breakdown",
            "You click outbound links and book anywhere you want",
        ],
        "why_audiences": [
            "Solo travelers who want fast comparisons without spreadsheeting every site",
            "Couples planning city breaks with a clear budget target",
            "Families comparing convenience, stops, and hotel fit",
            "Business travelers who need quick airport-to-airport options and time-aware ranking",
            "Flexible planners exploring destinations instead of starting with a fixed city",
        ],
        "why_testimonials": [
            {
                "quote": "I stopped bouncing between tabs. The total breakdown made it obvious which option was actually better value.",
                "person": "A couple planning a long weekend",
            },
            {
                "quote": "The explainable ranking helped me justify the slightly higher price because the route was much faster and had fewer stops.",
                "person": "A frequent business traveler",
            },
            {
                "quote": "I liked that it was links-only. No pressure to book inside the app, just solid options and clear totals.",
                "person": "A family trip planner",
            },
        ],
        "why_faqs": [
            {
                "q": "Do you book flights or hotels?",
                "a": "No. TriPPlanner is links-only. We do not run a booking engine, take payments, or create reservations.",
            },
            {
                "q": "Why not just use search result pages directly?",
                "a": "Search pages are useful, but they are often overwhelming and inconsistent for package-level comparison. We rank curated combinations and show a total breakdown first.",
            },
            {
                "q": "How do you handle trust and transparency?",
                "a": "We show explainable ranking signals, keep outbound click tracking visible in behavior, and avoid locking you into an in-app checkout flow.",
            },
            {
                "q": "What about safety and privacy?",
                "a": "Outbound URLs are validated, click tracking is limited to link analytics, and TriPPlanner does not store payment card details because there is no checkout.",
            },
            {
                "q": "Can I still book on my preferred provider?",
                "a": "Yes. That is the point of links-only planning. Use the ranked package as a decision layer, then book on the provider site you prefer.",
            },
        ],
    }
    return render(request, "planner/why.html", context)


@require_http_methods(["GET", "POST"])
def signup_view(request: HttpRequest) -> HttpResponse:
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Your account is ready. Start planning.")
        return redirect("planner:wizard")
    return render(request, "planner/signup.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def profile_view(request: HttpRequest) -> HttpResponse:
    personal_info_form = UserPersonalInfoForm(request.POST or None, instance=request.user)
    if request.method == "POST":
        if personal_info_form.is_valid():
            personal_info_form.save()
            messages.success(request, "Personal info updated.")
            return redirect("profile")
        messages.error(request, "Please fix the highlighted fields.")
    saved_places = list(
        SavedPlace.objects.filter(user=request.user)
        .only(
            "id",
            "name",
            "city",
            "country",
            "source",
            "external_id",
            "image_url",
            "outbound_url",
            "notes",
            "created_at",
            "updated_at",
        )
        .order_by("-created_at")
    )
    return render(
        request,
        "planner/profile.html",
        {
            "personal_info_form": personal_info_form,
            "saved_places": saved_places,
            "saved_places_count": len(saved_places),
            "saved_place_fallback_image": static("img/destinations/travel-adventure-japan-night-landscape.jpg"),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def wizard_view(request: HttpRequest) -> HttpResponse:
    initial: dict[str, Any] = {}
    profile = request.user.profile
    if profile.default_origin:
        initial["origin_iata"] = profile.default_origin
    initial["currency"] = profile.preferred_currency or "USD"
    initial["search_mode"] = PlanRequest.SearchMode.DIRECT

    form = PlannerWizardForm(request.POST or None, initial=initial)
    wizard_idempotency_key = _get_or_create_wizard_idempotency_key(request)
    if request.method == "POST" and form.is_valid():
        submitted_key = str(request.POST.get("idempotency_key") or "").strip()
        idempotency_key = submitted_key or wizard_idempotency_key
        plan = create_plan_request(request.user, form.to_plan_payload(), idempotency_key=f"wizard:{idempotency_key}")
        request.session[WIZARD_IDEMPOTENCY_SESSION_KEY] = uuid4().hex
        return redirect("planner:results", plan_id=plan.id)
    return render(
        request,
        "planner/planner_wizard.html",
        {
            "form": form,
            "providers": provider_status(),
            "idempotency_key": wizard_idempotency_key,
        },
    )


@login_required
@require_GET
def results_view(request: HttpRequest, plan_id: str) -> HttpResponse:
    plan = _plan_for_user_or_404(request.user, plan_id)
    sort_mode = request.GET.get("sort", "best_value")
    return render(
        request,
        "planner/results.html",
        {
            "plan": plan,
            "sort_mode": sort_mode,
            "providers": provider_status(),
            "read_only": False,
        },
    )


@require_GET
def public_share_view(request: HttpRequest, token: str) -> HttpResponse:
    plan = get_object_or_404(PlanRequest, public_token=token)
    sort_mode = request.GET.get("sort", "best_value")
    return render(
        request,
        "planner/results.html",
        {
            "plan": plan,
            "sort_mode": sort_mode,
            "providers": provider_status(),
            "read_only": True,
        },
    )


@require_GET
@never_cache
def package_detail_public_view(request: HttpRequest, token: str, package_id: str) -> HttpResponse:
    plan = get_object_or_404(PlanRequest, public_token=token)
    package = get_object_or_404(
        PackageOption.objects.select_related("plan", "candidate", "flight_option", "hotel_option").prefetch_related("tour_options"),
        pk=package_id,
        plan=plan,
    )
    return render(
        request,
        "planner/package_detail.html",
        {
            "plan": plan,
            "package": package,
            "providers": provider_status(),
            "read_only": not request.user.is_authenticated or request.user.id != plan.user_id,
        },
    )


@require_GET
@never_cache
def progress_partial(request: HttpRequest, plan_id: str) -> HttpResponse:
    if request.user.is_authenticated:
        plan = _plan_for_user_or_404(request.user, plan_id)
    else:
        token = request.GET.get("token", "")
        plan = get_object_or_404(PlanRequest, pk=plan_id, public_token=token)
    response = render(
        request,
        "planner/partials/progress_panel.html",
        {
            "plan": plan,
            "package_count": len(_visible_packages(plan, "best_value")) if plan.status == PlanRequest.Status.COMPLETED else 0,
        },
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@require_GET
@never_cache
def package_cards_partial(request: HttpRequest, plan_id: str) -> HttpResponse:
    if request.user.is_authenticated:
        plan = _plan_for_user_or_404(request.user, plan_id)
    else:
        token = request.GET.get("token", "")
        plan = get_object_or_404(PlanRequest, pk=plan_id, public_token=token)

    sort_mode = request.GET.get("sort", "best_value")
    packages = _visible_packages(plan, sort_mode)
    package_count = len(packages)
    response = render(
        request,
        "planner/partials/package_cards.html",
        {
            "plan": plan,
            "packages": packages,
            "package_count": package_count,
            "sort_mode": sort_mode,
            "read_only": not request.user.is_authenticated,
        },
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@login_required
@require_POST
def toggle_save_package(request: HttpRequest, package_id: str) -> HttpResponse:
    package = get_object_or_404(PackageOption, pk=package_id, plan__user=request.user)
    saved, created = SavedPackage.objects.get_or_create(user=request.user, package=package)
    if not created:
        saved.delete()
    is_saved = created
    if request.headers.get("HX-Request"):
        return render(
            request,
            "planner/partials/save_button.html",
            {
                "package": package,
                "is_saved": is_saved,
                "read_only": False,
            },
        )
    return redirect("planner:results", plan_id=package.plan_id)
