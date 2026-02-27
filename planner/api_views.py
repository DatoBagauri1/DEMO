from __future__ import annotations

from collections.abc import Mapping

from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from planner.models import ClickEvent, PackageOption, PlanRequest, SavedPackage, SavedPlace
from planner.serializers import (
    PackageOptionSerializer,
    PlanStartSerializer,
    PlanStatusSerializer,
    SavedPlaceSerializer,
    SavedPlaceToggleSerializer,
)
from planner.services.airports import search_airports
from planner.services.plan_service import create_plan_request
from planner.services.planner_nlp import parse_trip_text
from planner.services.provider_health import provider_health_payload
from planner.services.provider_registry import provider_status
from planner.services.security import is_allowed_outbound_url
from planner.tasks import refresh_top_packages_task


class PlanStartThrottle(UserRateThrottle):
    scope = "plan_start"


class ClickTrackThrottle(UserRateThrottle):
    scope = "click_track"


class AirportSearchThrottle(UserRateThrottle):
    scope = "airport_search"


def compact_validation_errors(detail):  # noqa: ANN001, ANN201
    if isinstance(detail, list):
        if len(detail) == 1:
            return compact_validation_errors(detail[0])
        return [compact_validation_errors(item) for item in detail]
    if isinstance(detail, Mapping):
        return {str(key): compact_validation_errors(value) for key, value in detail.items()}
    return str(detail)


class ClickEventSerializer(serializers.Serializer):
    plan_id = serializers.UUIDField(required=False, allow_null=True)
    package_id = serializers.UUIDField(required=False, allow_null=True)
    provider = serializers.CharField(max_length=64, required=False, allow_blank=True, default="travelpayouts")
    link_type = serializers.ChoiceField(
        choices=("flight", "hotel", "tour", "place", "other"),
        required=False,
        default="other",
    )
    destination = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    correlation_id = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    outbound_url = serializers.URLField(max_length=1500, required=False, allow_null=True)
    url = serializers.URLField(max_length=1500, required=False, allow_null=True)

    def validate(self, attrs):  # noqa: ANN201
        outbound_url = attrs.get("outbound_url") or attrs.get("url")
        if not outbound_url:
            raise serializers.ValidationError({"outbound_url": "outbound_url or url is required."})
        if not is_allowed_outbound_url(outbound_url):
            raise serializers.ValidationError({"outbound_url": "Outbound URL is not allowed."})
        attrs["outbound_url"] = outbound_url
        attrs["url"] = outbound_url
        return attrs


class AirportSearchAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [AirportSearchThrottle]

    def get(self, request):  # noqa: ANN001, ANN201
        query = str(request.query_params.get("q") or "").strip()
        if len(query) < 1:
            return Response({"results": []})
        try:
            limit = int(request.query_params.get("limit") or 12)
        except (TypeError, ValueError):
            limit = 12
        results = search_airports(query, limit=max(1, min(limit, 25)))
        return Response({"results": results})


class PlanInterpretAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    class InputSerializer(serializers.Serializer):
        text = serializers.CharField(max_length=1200)

    def post(self, request):  # noqa: ANN001, ANN201
        serializer = self.InputSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "detail": "validation_error",
                    "errors": compact_validation_errors(serializer.errors),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        parsed = parse_trip_text(serializer.validated_data["text"])
        return Response(parsed, status=status.HTTP_200_OK)


class PlanStartAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [PlanStartThrottle]

    def post(self, request):  # noqa: ANN001, ANN201
        serializer = PlanStartSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "detail": "validation_error",
                    "errors": compact_validation_errors(serializer.errors),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        idempotency_key = str(request.headers.get("X-Idempotency-Key") or "").strip()[:64] or None
        plan = create_plan_request(request.user, serializer.validated_data, idempotency_key=idempotency_key)
        return Response(
            {
                "plan_id": str(plan.id),
                "status": plan.status,
                "status_url": request.build_absolute_uri(reverse("planner-api:plan-status", kwargs={"plan_id": plan.id})),
                "results_url": request.build_absolute_uri(reverse("planner:results", kwargs={"plan_id": plan.id})),
                "share_url": request.build_absolute_uri(reverse("planner:share", kwargs={"token": plan.public_token})),
            },
            status=status.HTTP_202_ACCEPTED,
        )


class PlanRefreshAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, plan_id):  # noqa: ANN001, ANN201
        plan = get_object_or_404(PlanRequest, pk=plan_id, user=request.user)
        task = refresh_top_packages_task.delay(str(plan.id), 5)
        return Response(
            {
                "plan_id": str(plan.id),
                "refresh_task_id": task.id,
                "message": "Refresh queued for top packages.",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class PlanStatusAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @method_decorator(never_cache)
    def get(self, request, plan_id):  # noqa: ANN001, ANN201
        plan = get_object_or_404(PlanRequest, pk=plan_id, user=request.user)
        serializer = PlanStatusSerializer(plan)
        payload = serializer.data
        flags = provider_status()
        payload["fx_configured"] = flags.get("fx_enabled", False)
        payload["links_only_enabled"] = flags.get("links_only_enabled", True)
        response = Response(payload)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response


class PlanPackagesAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @method_decorator(never_cache)
    def get(self, request, plan_id):  # noqa: ANN001, ANN201
        plan = get_object_or_404(PlanRequest, pk=plan_id, user=request.user)
        sort_mode = request.query_params.get("sort", "best_value")
        queryset = plan.package_options.select_related("flight_option", "hotel_option", "candidate").prefetch_related("tour_options")
        if sort_mode == "cheapest":
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
            queryset = queryset.order_by("-score", "estimated_total_min")
        serializer = PackageOptionSerializer(queryset, many=True, context={"request": request})
        response = Response(serializer.data)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response


class PackageSaveToggleAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, package_id):  # noqa: ANN001, ANN201
        package = get_object_or_404(PackageOption, pk=package_id, plan__user=request.user)
        item, created = SavedPackage.objects.get_or_create(user=request.user, package=package)
        if not created:
            item.delete()
        return Response({"saved": created}, status=status.HTTP_200_OK)


class ClickTrackingAPIView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ClickTrackThrottle]

    def post(self, request):  # noqa: ANN001, ANN201
        serializer = ClickEventSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "detail": "validation_error",
                    "errors": compact_validation_errors(serializer.errors),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        plan = None
        package = None
        if serializer.validated_data.get("plan_id"):
            plan = PlanRequest.objects.filter(pk=serializer.validated_data["plan_id"]).first()
        if serializer.validated_data.get("package_id"):
            package = PackageOption.objects.filter(pk=serializer.validated_data["package_id"]).first()

        outbound_url = serializer.validated_data["outbound_url"]
        ClickEvent.objects.create(
            user=request.user if request.user.is_authenticated else None,
            plan=plan,
            package=package,
            provider=serializer.validated_data.get("provider") or "travelpayouts",
            link_type=serializer.validated_data.get("link_type") or "other",
            destination=serializer.validated_data.get("destination", ""),
            correlation_id=serializer.validated_data.get("correlation_id", "")[:64],
            outbound_url=outbound_url,
            url=outbound_url,
        )
        return Response({"tracked": True}, status=status.HTTP_201_CREATED)


class ProviderStatusAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):  # noqa: ANN001, ANN201
        return Response(provider_status())


class ProviderHealthAPIView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request):  # noqa: ANN001, ANN201
        return Response(provider_health_payload())


class ProfileMeAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @method_decorator(never_cache)
    def get(self, request):  # noqa: ANN001, ANN201
        user = request.user
        payload = {
            "username": user.username,
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "date_joined": user.date_joined,
            "last_login": user.last_login,
            "saved_places_count": SavedPlace.objects.filter(user=user).count(),
        }
        response = Response(payload)
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response


class SavedPlacesListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @method_decorator(never_cache)
    def get(self, request):  # noqa: ANN001, ANN201
        queryset = SavedPlace.objects.filter(user=request.user).order_by("-created_at")
        serializer = SavedPlaceSerializer(queryset, many=True)
        response = Response({"results": serializer.data, "count": queryset.count()})
        response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return response


class SavedPlaceSaveToggleAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):  # noqa: ANN001, ANN201
        serializer = SavedPlaceToggleSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {
                    "detail": "validation_error",
                    "errors": compact_validation_errors(serializer.errors),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = serializer.validated_data
        user = request.user

        saved_place_id = data.get("saved_place_id")
        if saved_place_id:
            saved_place = get_object_or_404(SavedPlace, pk=saved_place_id, user=user)
            place_id = saved_place.id
            saved_place.delete()
            return Response(
                {"saved": False, "place_id": place_id, "saved_places_count": SavedPlace.objects.filter(user=user).count()},
                status=status.HTTP_200_OK,
            )

        external_id = data.get("external_id")
        if external_id:
            lookup = {"user": user, "external_id": external_id}
        else:
            lookup = {
                "user": user,
                "name": data["name"],
                "city": data.get("city"),
                "country": data.get("country"),
            }

        existing = SavedPlace.objects.filter(**lookup).first()
        if existing:
            place_id = existing.id
            existing.delete()
            return Response(
                {"saved": False, "place_id": place_id, "saved_places_count": SavedPlace.objects.filter(user=user).count()},
                status=status.HTTP_200_OK,
            )

        saved_place = SavedPlace(
            user=user,
            name=data["name"],
            city=data.get("city"),
            country=data.get("country"),
            lat=data.get("lat"),
            lon=data.get("lon"),
            source=data.get("source") or "manual",
            external_id=data.get("external_id"),
            image_url=data.get("image_url"),
            outbound_url=data.get("outbound_url"),
            notes=data.get("notes"),
        )
        saved_place.full_clean()
        saved_place.save()
        return Response(
            {
                "saved": True,
                "place_id": saved_place.id,
                "saved_place": SavedPlaceSerializer(saved_place).data,
                "saved_places_count": SavedPlace.objects.filter(user=user).count(),
            },
            status=status.HTTP_201_CREATED,
        )
