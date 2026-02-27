from datetime import date

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from planner.models import PlanRequest, Profile
from planner.services.airports import airport_exists, normalize_iata


class SignUpForm(UserCreationForm):
    email = forms.EmailField(required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")


class ProfileForm(forms.ModelForm):
    travel_preferences = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Short JSON object or free-form text. Example: nonstop, boutique hotels, walkable areas.",
    )

    class Meta:
        model = Profile
        fields = (
            "default_origin",
            "preferred_currency",
            "default_budget_min",
            "default_budget_max",
            "travel_preferences",
        )


class UserPersonalInfoForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("email", "first_name", "last_name")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.widget.attrs.setdefault("class", "")
            field.widget.attrs["class"] = (
                f"{field.widget.attrs['class']} tp-input w-full rounded-xl border border-ink/10 bg-white px-3 py-2 text-sm text-ink"
            ).strip()
        self.fields["email"].widget.attrs.setdefault("type", "email")


class PlannerWizardForm(forms.Form):
    SEARCH_MODE_CHOICES = (
        (PlanRequest.SearchMode.DIRECT, "Direct destination"),
        (PlanRequest.SearchMode.EXPLORE, "Explore mode"),
    )
    PREFERENCE_CHOICES = (
        ("beach", "Beach"),
        ("nature", "Nature"),
        ("culture", "Culture"),
        ("nightlife", "Nightlife"),
        ("food", "Food"),
        ("quiet", "Quiet"),
        ("adventure", "Adventure"),
        ("family", "Family"),
        ("luxury", "Luxury"),
    )

    planner_text = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))
    search_mode = forms.ChoiceField(choices=SEARCH_MODE_CHOICES, initial=PlanRequest.SearchMode.DIRECT)

    origin_iata = forms.CharField(max_length=3)
    destination_iata = forms.CharField(max_length=3, required=False)
    destination_iatas_text = forms.CharField(required=False, widget=forms.HiddenInput())
    destination_country = forms.CharField(max_length=2, required=False)

    departure_date_from = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    departure_date_to = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))
    trip_length_min = forms.IntegerField(min_value=1, max_value=30, initial=4)
    trip_length_max = forms.IntegerField(min_value=1, max_value=45, initial=8)

    adults = forms.IntegerField(min_value=1, max_value=9, initial=2)
    children = forms.IntegerField(min_value=0, max_value=9, initial=0)
    currency = forms.CharField(max_length=3, initial="USD")

    hotel_stars_min = forms.IntegerField(min_value=1, max_value=5, initial=3, required=False)
    hotel_guest_rating_min = forms.FloatField(min_value=0, max_value=10, initial=7.5, required=False)
    hotel_amenities = forms.MultipleChoiceField(
        required=False,
        choices=(
            ("wifi", "Wi-Fi"),
            ("pool", "Pool"),
            ("parking", "Parking"),
            ("spa", "Spa"),
            ("breakfast", "Breakfast"),
            ("gym", "Gym"),
        ),
    )
    flight_max_stops = forms.IntegerField(min_value=0, max_value=3, initial=1, required=False)
    flight_max_duration_minutes = forms.IntegerField(min_value=60, max_value=2880, initial=1200, required=False)
    preferences = forms.MultipleChoiceField(required=False, choices=PREFERENCE_CHOICES)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.HiddenInput):
                continue

            existing_class = str(widget.attrs.get("class") or "").strip()
            base_class = "tp-input w-full rounded-xl border border-ink/10 bg-white px-3 py-2 text-sm text-black"
            if isinstance(widget, forms.SelectMultiple):
                base_class = f"{base_class} min-h-28"
            widget.attrs["class"] = f"{existing_class} {base_class}".strip()

            if isinstance(widget, (forms.Select, forms.SelectMultiple)):
                existing_style = str(widget.attrs.get("style") or "").strip().rstrip(";")
                style_parts = [part for part in [existing_style, "color: #000", "background-color: #fff"] if part]
                widget.attrs["style"] = "; ".join(style_parts)

    def clean_origin_iata(self) -> str:
        value = normalize_iata(self.cleaned_data["origin_iata"])
        if len(value) != 3 or not airport_exists(value):
            raise forms.ValidationError("Enter a valid origin airport IATA code.")
        return value

    def clean_destination_iata(self) -> str:
        value = normalize_iata(self.cleaned_data.get("destination_iata") or "")
        if not value:
            return ""
        if len(value) != 3 or not airport_exists(value):
            raise forms.ValidationError("Enter a valid destination airport IATA code.")
        return value

    def clean_destination_country(self) -> str:
        value = (self.cleaned_data.get("destination_country") or "").upper().strip()
        if value and len(value) != 2:
            raise forms.ValidationError("Destination country must be an ISO-2 code.")
        return value

    def clean(self):  # noqa: ANN201
        cleaned = super().clean()
        search_mode = cleaned.get("search_mode")
        origin = cleaned.get("origin_iata")
        destination = cleaned.get("destination_iata")

        if search_mode == PlanRequest.SearchMode.DIRECT and not destination:
            self.add_error("destination_iata", "Destination airport is required in direct mode.")
        if origin and destination and origin == destination:
            self.add_error("destination_iata", "Destination must be different from origin.")

        date_from = cleaned.get("departure_date_from")
        date_to = cleaned.get("departure_date_to")
        if not date_from or not date_to:
            self.add_error("departure_date_from", "Provide both departure range dates.")
        elif date_to < date_from:
            self.add_error("departure_date_to", "Departure range end must be on or after start date.")
        elif date_from < date.today():
            self.add_error("departure_date_from", "Departure range must start today or later.")

        trip_min = cleaned.get("trip_length_min")
        trip_max = cleaned.get("trip_length_max")
        if trip_min and trip_max and trip_max < trip_min:
            self.add_error("trip_length_max", "Max trip length must be >= min trip length.")

        adults = cleaned.get("adults") or 0
        children = cleaned.get("children") or 0
        if adults + children <= 0:
            self.add_error("adults", "At least one traveler is required.")

        return cleaned

    def to_plan_payload(self) -> dict:
        destinations = []
        destination_iata = self.cleaned_data.get("destination_iata")
        if destination_iata:
            destinations.append(destination_iata)

        raw_extra = (self.cleaned_data.get("destination_iatas_text") or "").strip()
        if raw_extra:
            for item in raw_extra.split(","):
                code = normalize_iata(item)
                if code and code not in destinations and airport_exists(code):
                    destinations.append(code)

        return {
            "origin_iata": self.cleaned_data["origin_iata"],
            "origin_input": self.cleaned_data["origin_iata"],
            "search_mode": self.cleaned_data["search_mode"],
            "destination_iata": destination_iata,
            "destination_iatas": destinations,
            "destination_input": destination_iata or "",
            "destination_country": self.cleaned_data.get("destination_country") or "",
            "departure_date_from": self.cleaned_data.get("departure_date_from"),
            "departure_date_to": self.cleaned_data.get("departure_date_to"),
            "trip_length_min": self.cleaned_data["trip_length_min"],
            "trip_length_max": self.cleaned_data["trip_length_max"],
            "adults": self.cleaned_data["adults"],
            "children": self.cleaned_data.get("children") or 0,
            "search_currency": self.cleaned_data["currency"].upper(),
            "hotel_filters": {
                "stars_min": self.cleaned_data.get("hotel_stars_min"),
                "guest_rating_min": self.cleaned_data.get("hotel_guest_rating_min"),
                "amenities": self.cleaned_data.get("hotel_amenities", []),
            },
            "flight_filters": {
                "max_stops": self.cleaned_data.get("flight_max_stops"),
                "max_duration_minutes": self.cleaned_data.get("flight_max_duration_minutes"),
            },
            "preferences": {key: 1.0 for key in self.cleaned_data.get("preferences", [])},
            "explore_constraints": {
                "planner_text": self.cleaned_data.get("planner_text") or "",
            },
        }
