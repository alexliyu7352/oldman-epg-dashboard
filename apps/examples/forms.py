"""Forms used by the Dashboard's executable examples."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import select
from wtforms import (
    BooleanField,
    DateField,
    DateTimeLocalField,
    DecimalField,
    IntegerField,
    RadioField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
    TimeField,
    ValidationError,
)
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Optional, Regexp, URL

from oldman.i18n import gettext_lazy as _
from oldman.web.components.forms import (
    Actions,
    AjaxAutocompleteWidget,
    AjaxSelectField,
    AjaxSelectMultipleField,
    ColorPickerField,
    DateTimePickerWidget,
    FieldLayout,
    FormLayout,
    FormStep,
    InputSpinnerWidget,
    JSONListField,
    FileExtension,
    FileSize,
    ModelChoice,
    ModelChoiceField,
    Row,
    RichTextField,
    SlugField,
    TagsField,
    TagsSelectWidget,
    TailwindForm,
    TailwindModelForm,
    TailwindTableFilterForm,
    UploadField,
)

from .models import ExampleAsset, ExampleLogo, ExampleProject, ExampleStreamProfile, ExampleTeam


LOGO_COUNTRY_CHOICES = (
    ("", _("All countries")),
    ("US", _("United States")),
    ("GB", _("United Kingdom")),
    ("CA", _("Canada")),
    ("AU", _("Australia")),
)

PROJECT_STATUS_CHOICES = (
    ("planned", _("Planned")),
    ("active", _("Active")),
    ("review", _("Review")),
    ("paused", _("Paused")),
    ("completed", _("Completed")),
)
PROJECT_PRIORITY_CHOICES = (
    ("low", _("Low")),
    ("normal", _("Normal")),
    ("high", _("High")),
    ("critical", _("Critical")),
)
PROJECT_TEAM_CHOICE = ModelChoice(
    model=ExampleTeam,
    value_field="id",
    label_field="name",
    order_by=("name",),
    empty_label=cast(str, _("All teams")),
)


class ExampleProjectFilterForm(TailwindTableFilterForm):
    """Filter the shared ExampleProject Table without a parallel query protocol."""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Name, slug, team or description")})
    team_id = ModelChoiceField(_("Team"), model_choice=PROJECT_TEAM_CHOICE, validators=[Optional()])
    status = SelectField(_("Status"), choices=(("", _("All statuses")), *PROJECT_STATUS_CHOICES), validators=[Optional()])
    priority = SelectField(_("Priority"), choices=(("", _("All priorities")), *PROJECT_PRIORITY_CHOICES), validators=[Optional()])
    is_active = SelectField(
        _("Availability"),
        choices=(("", _("All projects")), ("true", _("Active only")), ("false", _("Archived only"))),
        validators=[Optional()],
    )

    field_layout = (
        FieldLayout("q", "md:col-span-4"),
        FieldLayout("team_id", "md:col-span-2"),
        FieldLayout("status", "md:col-span-2"),
        FieldLayout("priority", "md:col-span-2"),
        FieldLayout("is_active", "md:col-span-2"),
    )


class CommunicationProjectForm(TailwindForm):
    """Validate the current database choices and the two explicit Demo peers."""

    project_id = SelectField(_("Project"), coerce=int, choices=[], validators=[DataRequired()])
    peer_id = SelectField(
        _("Receiving service"),
        choices=(("monitor_a", "monitor_a (nats_a)"), ("monitor_b", "monitor_b (nats_b)")),
        validators=[DataRequired()],
    )
    field_layout = (FieldLayout("project_id", "md:col-span-6"), FieldLayout("peer_id", "md:col-span-6"))


class ExampleProjectForm(TailwindModelForm):
    """Create and edit the same Project rows rendered by every Table example."""

    team_id = ModelChoiceField(_("Team"), model_choice=PROJECT_TEAM_CHOICE, validators=[DataRequired()])
    name = StringField(_("Name"), validators=[DataRequired(), Length(max=150)])
    slug = StringField(
        _("Slug"),
        validators=[DataRequired(), Length(max=150), Regexp(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")],
    )
    description = TextAreaField(_("Description"), validators=[Optional(), Length(max=600)])
    status = SelectField(_("Status"), choices=PROJECT_STATUS_CHOICES, validators=[DataRequired()])
    priority = SelectField(_("Priority"), choices=PROJECT_PRIORITY_CHOICES, validators=[DataRequired()])
    budget = DecimalField(_("Budget"), validators=[InputRequired(), NumberRange(min=0)], places=2)
    progress = IntegerField(_("Progress"), validators=[InputRequired(), NumberRange(min=0, max=100)])
    start_date = DateField(_("Start date"), validators=[Optional()])
    end_date = DateField(_("End date"), validators=[Optional()])
    is_active = BooleanField(_("Active"), default=True)

    field_layout = (
        FieldLayout("team_id", "md:col-span-6"),
        FieldLayout("name", "md:col-span-6"),
        FieldLayout("slug", "md:col-span-12"),
        FieldLayout("status", "md:col-span-4"),
        FieldLayout("priority", "md:col-span-4"),
        FieldLayout("progress", "md:col-span-4"),
        FieldLayout("budget", "md:col-span-4"),
        FieldLayout("start_date", "md:col-span-4"),
        FieldLayout("end_date", "md:col-span-4"),
        FieldLayout("description", "md:col-span-12"),
        FieldLayout("is_active", "md:col-span-12"),
    )

    class Meta(TailwindModelForm.Meta):
        """Bind only fields exercised by the Table CRUD example."""

        model = ExampleProject
        fields = (
            "team_id",
            "name",
            "slug",
            "description",
            "status",
            "priority",
            "budget",
            "progress",
            "start_date",
            "end_date",
            "is_active",
        )

    async def clean_slug(self) -> str:
        """Reject duplicate stable slugs before the database constraint fires."""
        slug = str(self.slug.data or "").strip()
        if self.session is None:
            raise RuntimeError("ExampleProjectForm validation requires a database session")
        result = await self.session.execute(select(ExampleProject).where(ExampleProject.slug == slug))
        existing = result.scalar_one_or_none()
        if existing is not None and int(existing.id) != int(getattr(self.instance, "id", 0) or 0):
            raise ValidationError(_("A project with this slug already exists."))
        return slug

    async def clean(self) -> dict[str, object] | None:
        """Validate the optional project date range."""
        start_date = self.cleaned_data.get("start_date")
        end_date = self.cleaned_data.get("end_date")
        if start_date is not None and end_date is not None and end_date < start_date:
            raise ValidationError(_("End date must not be earlier than start date."))
        return None


class BasicFieldsForm(TailwindForm):
    """Common scalar fields used by both HTML and JSON submissions."""

    name = StringField(_("Name"), validators=[DataRequired(), Length(max=80)])
    email = StringField(_("Email"), validators=[DataRequired(), Regexp(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")])
    age = IntegerField(_("Age"), validators=[Optional(), NumberRange(min=0, max=130)])
    budget = DecimalField(_("Budget"), validators=[Optional()], places=2)
    notes = TextAreaField(_("Notes"), validators=[Optional(), Length(max=300)])
    enabled = BooleanField(_("Enabled"))


class ChoiceFieldsForm(TailwindForm):
    """Native WTForms single and multiple choice controls."""

    status = SelectField(
        _("Status"),
        choices=[("planned", _("Planned")), ("active", _("Active")), ("paused", _("Paused"))],
        validators=[DataRequired()],
    )
    priority = RadioField(
        _("Priority"),
        choices=[("low", _("Low")), ("normal", _("Normal")), ("high", _("High"))],
        validators=[DataRequired()],
    )
    regions = SelectMultipleField(
        _("Regions"),
        choices=[("us", _("United States")), ("eu", _("Europe")), ("apac", _("Asia Pacific"))],
        validators=[DataRequired()],
    )
    notifications = BooleanField(_("Enable notifications"))


class LayoutExampleForm(BasicFieldsForm):
    """The same scalar fields arranged through Oldman's declarative layout."""

    layout = FormLayout(
        Row("name", "email", width="md:col-span-6"),
        Row("age", "budget", "enabled", width="md:col-span-4"),
        FieldLayout("notes"),
        Actions(submit=_("Submit layout")),
    )


class ValidationExampleForm(TailwindForm):
    """Show field and cross-field validation without client-only rules."""

    username = StringField(_("Username"), validators=[DataRequired(), Length(min=4, max=20)])
    start = IntegerField(_("Start"), validators=[InputRequired(), NumberRange(min=0, max=100)])
    end = IntegerField(_("End"), validators=[InputRequired(), NumberRange(min=0, max=100)])

    async def clean(self) -> None:
        """Reject a range whose end precedes its start."""
        start = self.cleaned_data.get("start")
        end = self.cleaned_data.get("end")
        if isinstance(start, int) and isinstance(end, int) and end < start:
            raise ValidationError(_("End must be greater than or equal to start."))


class DateTimeExampleForm(TailwindForm):
    """Native date/time fields and the existing Flatpickr adapter."""

    date = DateField(_("Date"), validators=[DataRequired()])
    time = TimeField(_("Time"), validators=[DataRequired()])
    scheduled_at = DateTimeLocalField(
        _("Scheduled at"),
        format="%Y-%m-%dT%H:%M",
        validators=[DataRequired()],
        widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"),
    )


class MaskExampleForm(TailwindForm):
    """Inputs enhanced by the existing Cleave-backed FormMask component."""

    phone = StringField(
        _("Phone"),
        validators=[DataRequired()],
        render_kw={"data-om-component": "form-mask", "data-om-mask-type": "phone"},
    )
    date_code = StringField(
        _("Date code"),
        validators=[DataRequired()],
        render_kw={"data-om-component": "form-mask", "data-om-mask-type": "date"},
    )
    amount = StringField(
        _("Amount"),
        validators=[DataRequired()],
        render_kw={"data-om-component": "form-mask", "data-om-mask-type": "numeral"},
    )


class SliderExampleForm(TailwindForm):
    """Values synchronized from the existing noUiSlider component."""

    utilization = IntegerField(_("Utilization"), validators=[InputRequired(), NumberRange(min=0, max=100)], default=45)


class SlugExampleForm(TailwindForm):
    """Exercise live slug previews with server-owned final normalization."""

    title = StringField(_("Page title"), validators=[DataRequired(), Length(max=80)])
    slug = SlugField(
        _("ASCII slug"),
        source_field="title",
        validators=[Length(max=80)],
        description=_("Generated from the page title while you type; you can still enter a custom slug."),
    )
    unicode_title = StringField(_("Localized title"), validators=[DataRequired(), Length(max=80)])
    unicode_slug = SlugField(
        _("Unicode slug"),
        source_field="unicode_title",
        allow_unicode=True,
        validators=[Length(max=80)],
        description=_("Unicode letters stay visible while spaces and punctuation become separators."),
    )

    layout = FormLayout(
        Row("title", "slug", width="md:col-span-6"),
        Row("unicode_title", "unicode_slug", width="md:col-span-6"),
    )


class InputSpinnerExampleForm(TailwindForm):
    """Exercise native integer and decimal stepping at both configured limits."""

    replicas = IntegerField(
        _("Worker replicas"),
        default=3,
        validators=[InputRequired(), NumberRange(min=1, max=10)],
        widget=InputSpinnerWidget(),
        render_kw={"min": 1, "max": 10, "step": 1},
        description=_("Whole-number steps from 1 through 10."),
    )
    buffer_seconds = DecimalField(
        _("Buffer seconds"),
        default=2.5,
        places=1,
        validators=[InputRequired(), NumberRange(min=0, max=30)],
        widget=InputSpinnerWidget(),
        render_kw={"min": 0, "max": 30, "step": "0.5"},
        description=_("Half-second steps from 0 through 30."),
    )

    layout = FormLayout(
        Row("replicas", "buffer_seconds", width="md:col-span-6"),
    )


class TagsExampleForm(TailwindForm):
    """Show free-text and existing-choice tags without mixing their data contracts."""

    keywords = TagsField(
        _("Keywords"),
        delimiter=",",
        default="python,sanic,redis",
        validators=[DataRequired(), Length(max=500)],
        description=_("Enter or paste comma-separated keywords."),
    )
    aliases = TagsField(
        _("Aliases"),
        delimiter="|",
        default="api|web",
        validators=[DataRequired(), Length(max=500)],
        description=_("This field uses a vertical bar as its separator."),
    )
    categories = SelectMultipleField(
        _("Categories"),
        choices=(("backend", _("Backend")), ("dashboard", _("Dashboard")), ("operations", _("Operations"))),
        default=("backend", "dashboard"),
        widget=TagsSelectWidget(),
        validators=[DataRequired()],
        description=_("Choose one or more fixed local options."),
    )
    tag_ids = AjaxSelectMultipleField(
        _("Database tags"),
        provider="example_tags",
        route_name="example_select_provider",
        page_size=6,
        tags=True,
        coerce=int,
        validators=[DataRequired()],
        description=_("Search and select tags from the example database."),
    )

    layout = FormLayout(
        Row("keywords", "aliases", width="md:col-span-6"),
        Row("categories", "tag_ids", width="md:col-span-6"),
    )


class ColorPickerExampleForm(TailwindForm):
    """Submit native RGB and Pickr-enhanced RGBA colors through one field API."""

    brand_color = ColorPickerField(
        _("Brand color"),
        default="#2563eb",
        validators=[DataRequired()],
        description=_("Falls back to the browser's native color control without JavaScript."),
    )
    overlay_color = ColorPickerField(
        _("Overlay color"),
        default="#0f172acc",
        validators=[DataRequired()],
        description=_("The enhanced picker keeps the alpha channel in the submitted HEX value."),
    )

    layout = FormLayout(Row("brand_color", "overlay_color", width="md:col-span-6"))


class RichTextExampleForm(TailwindForm):
    """Edit trusted formatted content through Quill and the native textarea fallback."""

    title = StringField(_("Article title"), validators=[DataRequired(), Length(max=100)])
    body = RichTextField(
        _("Article body"),
        default=_("<h2>Oldman release notes</h2><p>Use the toolbar to edit this trusted content.</p>"),
        validators=[DataRequired(), Length(max=4000)],
        description=_("The textarea remains the submitted control; Quill only enhances the editing experience."),
    )

    layout = FormLayout(FieldLayout("title"), FieldLayout("body"))


class MultiStepProjectForm(ExampleProjectForm):
    """Create one real project through a single three-step ModelForm."""

    layout = FormLayout(
        FormStep(
            _("Identity"),
            Row("team_id", "name", width="md:col-span-6"),
            "slug",
            description=_("Choose the owning team and the stable project identity."),
        ),
        FormStep(
            _("Planning"),
            Row("status", "priority", width="md:col-span-6"),
            Row("budget", "progress", width="md:col-span-6"),
            description=_("Set the operational state, priority, budget and progress."),
        ),
        FormStep(
            _("Details"),
            Row("start_date", "end_date", width="md:col-span-6"),
            "description",
            "is_active",
            description=_("Review the optional schedule and description before saving."),
        ),
        Actions(submit=_("Create project")),
    )


class ContainerExampleForm(BasicFieldsForm):
    """One ordinary Form reused inline and inside a remote Modal."""


class ExampleAssetForm(TailwindModelForm):
    """Create or edit both managed files on one real ExampleAsset."""

    display_name = StringField(_("Display name"), validators=[DataRequired(), Length(max=160)])
    document_path = UploadField(
        _("Document"),
        validators=[FileSize(1024 * 1024), FileExtension(("txt", "pdf"))],
        render_kw={"class": "hidden", "data-om-upload-input": True, "accept": ".txt,.pdf"},
    )
    preview_path = UploadField(
        _("Preview image"),
        validators=[FileSize(512 * 1024), FileExtension(("png", "jpg", "jpeg", "webp"))],
        render_kw={"class": "hidden", "data-om-upload-input": True, "accept": "image/png,image/jpeg,image/webp"},
    )
    description = TextAreaField(_("Description"), validators=[Optional(), Length(max=500)])

    class Meta(TailwindModelForm.Meta):
        """Bind the editable ExampleAsset fields."""

        model = ExampleAsset
        fields = ("display_name", "document_path", "preview_path", "description")

    async def validate(self, extra_validators: dict[str, Any] | None = None) -> bool:
        """Require a document for new records while edits may keep the stored file."""
        valid = await super().validate(extra_validators=extra_validators)
        if self.is_bound and self.instance is None and self.document_path.data is None:
            self.add_error("document_path", _("This field is required."))
            self._validation_succeeded = False
            return False
        return valid


class AssetRollbackForm(TailwindModelForm):
    """Upload one replacement preview whose database transaction is rolled back."""

    preview_path = UploadField(
        _("Temporary preview image"),
        validators=[DataRequired(), FileSize(512 * 1024), FileExtension(("png", "jpg", "jpeg", "webp"))],
        render_kw={"class": "hidden", "data-om-upload-input": True, "accept": "image/png,image/jpeg,image/webp"},
    )

    class Meta(TailwindModelForm.Meta):
        """Bind only the preview used by the rollback demonstration."""

        model = ExampleAsset
        fields = ("preview_path",)


class StreamProfileForm(TailwindModelForm):
    """Edit a Text-backed variable list of stream source URLs."""

    name = StringField(_("Profile name"), validators=[DataRequired(), Length(max=150)])
    status = SelectField(
        _("Status"),
        choices=[("active", _("Active")), ("paused", _("Paused")), ("disabled", _("Disabled"))],
        validators=[DataRequired()],
    )
    sources_json = JSONListField(
        StringField(_("Source URL"), validators=[DataRequired(), URL()]),
        min_entries=1,
        max_entries=8,
    )

    class Meta(TailwindModelForm.Meta):
        """Bind the three editable StreamProfile fields."""

        model = ExampleStreamProfile
        fields = ("name", "status", "sources_json")

    async def clean_name(self) -> str:
        """Normalize and validate the profile's unique name."""
        name = str(self.name.data or "").strip()
        if self.session is None or not name:
            return name
        result = await self.session.execute(select(ExampleStreamProfile).where(ExampleStreamProfile.name == name))
        existing = result.scalar_one_or_none()
        if existing is not None and int(existing.id) != int(getattr(self.instance, "id", 0) or 0):
            self.add_error("name", _("A stream profile with this name already exists."))
        return name

    async def clean(self) -> dict[str, object] | None:
        """Normalize sources and reject duplicates as a business list error."""
        sources = self.cleaned_data.get("sources_json")
        if not isinstance(sources, list):
            return None
        normalized = [str(source).strip() for source in sources]
        if len(set(normalized)) != len(normalized):
            raise ValidationError(_("Source URLs must be unique."))
        return {**self.cleaned_data, "sources_json": normalized}


class LogoSelectForm(TailwindModelForm):
    """Edit a StreamProfile Logo through a rich dependent remote Select."""

    country_code = SelectField(_("Country"), choices=LOGO_COUNTRY_CHOICES, validators=[Optional()])
    logo_id = AjaxSelectField(
        _("Logo"),
        provider="example_logos",
        route_name="example_select_provider",
        page_size=8,
        dependent_fields=("country_code",),
        enhance_choices=True,
        label_mode="html",
        validators=[DataRequired()],
    )

    class Meta(TailwindModelForm.Meta):
        """Persist only the selected Logo foreign key."""

        model = ExampleStreamProfile
        fields = ("logo_id",)

    def __init__(self, *args: Any, initial_logo: ExampleLogo | None = None, **kwargs: Any) -> None:
        """Seed the native option needed before the provider initial lookup runs."""
        super().__init__(*args, **kwargs)
        if self.is_bound or initial_logo is None:
            return
        self.country_code.data = initial_logo.country_code
        self.logo_id.choices = [(initial_logo.id, f"{initial_logo.name} · {initial_logo.country_code}")]
        self.logo_id.data = initial_logo.id

    async def clean_logo_id(self) -> int:
        """Reject forged, unavailable or country-mismatched submitted IDs."""
        logo_id = int(str(self.logo_id.data))
        logo = await self._submitted_logo(logo_id)
        if logo is None or not logo.is_available:
            raise ValidationError(_("Select an available logo."))
        country_code = str(self.country_code.data or "")
        if country_code and logo.country_code != country_code:
            raise ValidationError(_("The selected logo does not belong to this country."))
        return logo_id

    async def _submitted_logo(self, logo_id: int) -> ExampleLogo | None:
        """Read the submitted Logo in the active write transaction."""
        if self.session is None:
            raise RuntimeError("LogoSelectForm validation requires a database session")
        return await self.session.get(ExampleLogo, logo_id)


class LogoMultipleSelectForm(TailwindForm):
    """Show remote multi-select pagination without inventing persistence semantics."""

    logo_ids = AjaxSelectMultipleField(
        _("Logos"),
        provider="example_logos",
        route_name="example_select_provider",
        page_size=8,
        enhance_choices=True,
        label_mode="html",
        validators=[Optional()],
    )
    country_codes = AjaxSelectMultipleField(
        _("Countries"),
        provider="example_countries",
        route_name="example_select_provider",
        page_size=10,
        enhance_choices=True,
        coerce=str,
        validators=[Optional()],
    )

    def __init__(self, *args: Any, initial_logos: tuple[ExampleLogo, ...] = (), **kwargs: Any) -> None:
        """Seed selected options so the provider exercises ordered multi-value lookup."""
        super().__init__(*args, **kwargs)
        if self.is_bound or not initial_logos:
            return
        self.logo_ids.choices = [(logo.id, f"{logo.name} · {logo.country_code}") for logo in initial_logos]
        self.logo_ids.data = [logo.id for logo in initial_logos]


class LogoAutocompleteForm(TailwindModelForm):
    """Edit the same Logo foreign key through text plus hidden ID autocomplete."""

    country_code = SelectField(_("Country"), choices=LOGO_COUNTRY_CHOICES, validators=[Optional()])
    logo_id = StringField(
        _("Logo lookup"),
        widget=AjaxAutocompleteWidget(
            provider="example_logos",
            route_name="example_select_provider",
            page_size=8,
            dependent_fields=("country_code",),
            label_mode="html",
        ),
        validators=[DataRequired()],
    )

    class Meta(TailwindModelForm.Meta):
        """Persist only the selected Logo foreign key."""

        model = ExampleStreamProfile
        fields = ("logo_id",)

    def __init__(self, *args: Any, initial_logo: ExampleLogo | None = None, **kwargs: Any) -> None:
        """Seed both the hidden ID and visible label for an edit form."""
        super().__init__(*args, **kwargs)
        if self.is_bound or initial_logo is None:
            return
        self.country_code.data = initial_logo.country_code
        self.logo_id.data = str(initial_logo.id)
        render_kw = dict(self.logo_id.render_kw or {})
        render_kw["value"] = f"{initial_logo.name} · {initial_logo.country_code}"
        self.logo_id.render_kw = render_kw

    async def clean_logo_id(self) -> int:
        """Apply the same server-side ID and dependency checks as the Select form."""
        try:
            logo_id = int(str(self.logo_id.data))
        except (TypeError, ValueError) as exc:
            raise ValidationError(_("Select an available logo.")) from exc
        if self.session is None:
            raise RuntimeError("LogoAutocompleteForm validation requires a database session")
        logo = await self.session.get(ExampleLogo, logo_id)
        if logo is None or not logo.is_available:
            raise ValidationError(_("Select an available logo."))
        country_code = str(self.country_code.data or "")
        if country_code and logo.country_code != country_code:
            raise ValidationError(_("The selected logo does not belong to this country."))
        return logo_id


FORM_PAGE_FORMS = {
    "basics": BasicFieldsForm,
    "choices": ChoiceFieldsForm,
    "layouts": LayoutExampleForm,
    "validation": ValidationExampleForm,
    "date-time": DateTimeExampleForm,
    "masks": MaskExampleForm,
    "sliders": SliderExampleForm,
    "slug": SlugExampleForm,
    "input-spinner": InputSpinnerExampleForm,
    "tags": TagsExampleForm,
    "color-picker": ColorPickerExampleForm,
    "rich-text": RichTextExampleForm,
    "multi-step": MultiStepProjectForm,
    "containers": ContainerExampleForm,
}


__all__ = [
    "AssetRollbackForm",
    "BasicFieldsForm",
    "ChoiceFieldsForm",
    "ContainerExampleForm",
    "ColorPickerExampleForm",
    "DateTimeExampleForm",
    "ExampleAssetForm",
    "FORM_PAGE_FORMS",
    "InputSpinnerExampleForm",
    "LayoutExampleForm",
    "LogoAutocompleteForm",
    "LogoMultipleSelectForm",
    "LogoSelectForm",
    "MaskExampleForm",
    "MultiStepProjectForm",
    "RichTextExampleForm",
    "SliderExampleForm",
    "SlugExampleForm",
    "StreamProfileForm",
    "TagsExampleForm",
    "ValidationExampleForm",
]
