"""EPG 后台声明式表单。"""

from __future__ import annotations

import datetime as dt
import inspect
from typing import Any, cast

from wtforms import BooleanField, DateField, DateTimeLocalField, DecimalField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from apps.epg_admin.models import CatalogChannel, CatalogFeed, CatalogMatchDecision, ChannelName, ChannelsEpg, EpgList
from oldman.i18n import gettext_lazy as _
from oldman.web.components.forms import (
    AjaxAutocompleteWidget,
    AjaxSelectWidget,
    DateTimePickerWidget,
    FieldLayout,
    TailwindModelForm,
    TailwindTableFilterForm,
)
from oldman.web.components.selects import SelectChoice


def utcnow_naive() -> dt.datetime:
    """返回当前 UTC 时间，并保持现有数据库字段使用的无时区 datetime。"""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


def optional_int(value: object) -> int | None:
    """把远程 select 的空值转换成 None，其余值转换成 int。"""
    if value in {"", None}:
        return None
    return int(cast(Any, value))


class ChannelsEpgFilterForm(TailwindTableFilterForm):
    """频道列表筛选表单。"""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Search name, URL, country")})

    field_layout = (
        FieldLayout("q", "md:col-span-5"),
    )


class ChannelsEpgForm(TailwindModelForm):
    """频道新建和编辑表单。"""

    name = StringField(_("Name"), validators=[DataRequired()])
    country = StringField(_("Country"), validators=[Optional()])
    src_url = StringField(_("Source URL"), validators=[DataRequired()])
    icon = StringField(_("Icon"), validators=[Optional()])
    hits = IntegerField(_("Hits"), validators=[Optional(), NumberRange(min=0)], default=0, render_kw={"min": 0})
    last_date = DateField(_("Last Date"), validators=[DataRequired()], format="%Y-%m-%d")
    tvg_id = StringField(_("TVG ID"), validators=[Optional()])
    tvg_id_source = StringField(_("TVG Source"), validators=[Optional()], default="manual")
    tvg_id_confidence = IntegerField(_("TVG Confidence"), validators=[Optional(), NumberRange(min=0, max=100)], default=0, render_kw={"min": 0, "max": 100})
    tvg_id_locked = BooleanField(_("Lock TVG ID"))
    tvg_id_reason = StringField(_("TVG Reason"), validators=[Optional()])
    description = TextAreaField(_("Description"), validators=[Optional()], render_kw={"rows": 4})

    field_layout = (
        FieldLayout("name", "md:col-span-6"),
        FieldLayout("country", "md:col-span-6"),
        FieldLayout("src_url", "md:col-span-12"),
        FieldLayout("icon", "md:col-span-12"),
        FieldLayout("hits", "md:col-span-6"),
        FieldLayout("last_date", "md:col-span-6"),
        FieldLayout("tvg_id", "md:col-span-6"),
        FieldLayout("tvg_id_source", "md:col-span-6"),
        FieldLayout("tvg_id_confidence", "md:col-span-6"),
        FieldLayout("tvg_id_locked", "md:col-span-6 flex items-end"),
        FieldLayout("tvg_id_reason", "md:col-span-12"),
        FieldLayout("description", "md:col-span-12"),
    )

    class Meta:
        """声明频道模型表单绑定关系。"""

        model = ChannelsEpg
        fields = [
            "name",
            "country",
            "src_url",
            "icon",
            "hits",
            "last_date",
            "tvg_id",
            "tvg_id_source",
            "tvg_id_confidence",
            "tvg_id_locked",
            "tvg_id_reason",
            "description",
        ]

    async def save(self, *, commit: bool = False, session: Any = None) -> ChannelsEpg:
        """保存频道实例并维护旧业务需要的派生字段。"""
        channel = await super().save(commit=False)
        if channel.create_date is None:
            channel.create_date = utcnow_naive()
        channel.tvg_id_lookup = channel.tvg_id.lower() if channel.tvg_id else None
        if commit:
            active_session = session or self.session
            if active_session is None:
                raise ValueError("save(commit=True) requires a session")
            active_session.add(channel)
            flush_result = active_session.flush()
            if inspect.isawaitable(flush_result):
                await flush_result
        return channel


class EpgListFilterForm(TailwindTableFilterForm):
    """节目单列表筛选表单。"""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Search title or description")})
    channel_id = SelectField(_("Channel"), choices=[], validators=[Optional()])
    date_from = DateField(_("Date From"), validators=[Optional()], format="%Y-%m-%d")
    date_to = DateField(_("Date To"), validators=[Optional()], format="%Y-%m-%d")

    field_layout = (
        FieldLayout("q", "lg:col-span-3 md:col-span-6"),
        FieldLayout("channel_id", "lg:col-span-3 md:col-span-6"),
        FieldLayout("date_from", "lg:col-span-2 md:col-span-6"),
        FieldLayout("date_to", "lg:col-span-2 md:col-span-6"),
    )

    def __init__(self, *args: Any, channel_choices: list[SelectChoice] | None = None, **kwargs: Any) -> None:
        """初始化节目单筛选表单并注入频道选项。"""
        super().__init__(*args, **kwargs)
        self.channel_id.choices = [("", _("All channels"))] + [(str(choice.id), choice.text) for choice in channel_choices or []]


class EpgListForm(TailwindModelForm):
    """节目单新建和编辑表单。"""

    channel_id = SelectField(
        _("Channel"),
        choices=[],
        coerce=int,
        validate_choice=False,
        widget=AjaxSelectWidget(
            provider="channels",
            route_name="admin_select_provider",
            page_size=20,
            enhance_choices=True,
        ),
        validators=[DataRequired()],
    )
    channel_lookup = StringField(
        _("Channel Lookup"),
        widget=AjaxAutocompleteWidget(
            provider="channels",
            route_name="admin_select_provider",
            page_size=10,
        ),
        validators=[Optional()],
    )
    title = StringField(_("Title"), validators=[Optional()])
    start_date = DateTimeLocalField(
        _("Start Time"),
        validators=[DataRequired()],
        format="%Y-%m-%dT%H:%M",
        widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"),
    )
    description = TextAreaField(_("Description"), validators=[Optional()], render_kw={"rows": 5})

    field_layout = (
        FieldLayout("channel_id", "md:col-span-12"),
        FieldLayout("channel_lookup", "md:col-span-12"),
        FieldLayout("title", "md:col-span-12"),
        FieldLayout("start_date", "md:col-span-12"),
        FieldLayout("description", "md:col-span-12"),
    )

    class Meta:
        """声明节目单模型表单绑定关系。"""

        model = EpgList
        fields = ["channel_id", "title", "start_date", "description"]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """初始化节目单表单，并为编辑页远程频道控件补齐当前值。"""
        super().__init__(*args, **kwargs)
        self.bind_initial_channel_controls()

    def bind_initial_channel_controls(self) -> None:
        """把当前节目频道写入远程 select/autocomplete 的首屏回显。"""
        if self.is_bound:
            return
        channel = getattr(self.instance, "channel", None)
        if channel is None:
            return
        channel_id = optional_int(getattr(channel, "id", None))
        channel_name = getattr(channel, "name", "")
        if channel_id in {None, ""}:
            return

        self.channel_id.choices = [(channel_id, str(channel_name))]
        self.channel_id.data = channel_id
        self.channel_lookup.data = str(channel_id)
        render_kw = dict(getattr(self.channel_lookup, "render_kw", None) or {})
        render_kw["value"] = str(channel_name)
        self.channel_lookup.render_kw = render_kw

    async def save(self, *, commit: bool = False, session: Any = None) -> EpgList:
        """保存节目单实例并维护创建时间。"""
        item = await super().save(commit=False)
        if item.create_date is None:
            item.create_date = utcnow_naive()
        if commit:
            active_session = session or self.session
            if active_session is None:
                raise ValueError("save(commit=True) requires a session")
            active_session.add(item)
            flush_result = active_session.flush()
            if inspect.isawaitable(flush_result):
                await flush_result
        return item


class ChannelNameFilterForm(TailwindTableFilterForm):
    """频道名称列表筛选表单。"""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Search names or logo")})
    published = SelectField(
        _("Published"),
        choices=[("", _("All status")), ("true", _("Published")), ("false", _("Draft"))],
        validators=[Optional()],
    )
    country_id = StringField(
        _("Country ID"),
        validators=[Optional()],
        render_kw={"inputmode": "numeric", "data-om-component": "form-mask", "data-om-mask-type": "numeral"},
    )
    language_id = StringField(
        _("Language ID"),
        validators=[Optional()],
        render_kw={"inputmode": "numeric", "data-om-component": "form-mask", "data-om-mask-type": "numeral"},
    )
    bound_epg = SelectField(
        _("Bound EPG"),
        choices=[("", _("All bindings")), ("true", _("Bound")), ("false", _("Unbound"))],
        validators=[Optional()],
    )

    field_layout = (
        FieldLayout("q", "lg:col-span-3 md:col-span-6"),
        FieldLayout("published", "lg:col-span-2 md:col-span-6"),
        FieldLayout("country_id", "lg:col-span-2 md:col-span-6"),
        FieldLayout("language_id", "lg:col-span-2 md:col-span-6"),
        FieldLayout("bound_epg", "lg:col-span-2 md:col-span-6"),
    )


class ChannelNameForm(TailwindModelForm):
    """频道名称新建和编辑表单。"""

    name = StringField(_("Name"), validators=[DataRequired()])
    name_cn = StringField(_("Chinese Name"), validators=[Optional()])
    name_tw = StringField(_("Traditional Chinese Name"), validators=[Optional()])
    name_es = StringField(_("Spanish Name"), validators=[Optional()])
    logo = StringField(_("Logo"), validators=[DataRequired()])
    logo_size = DecimalField(_("Logo Size"), validators=[Optional()], places=2)
    epg_id = SelectField(
        _("Bound EPG"),
        choices=[],
        coerce=optional_int,
        validate_choice=False,
        widget=AjaxSelectWidget(
            provider="channels",
            route_name="admin_select_provider",
            page_size=20,
            enhance_choices=True,
        ),
        validators=[Optional()],
    )
    epg_lookup = StringField(
        _("EPG Lookup"),
        widget=AjaxAutocompleteWidget(
            provider="channels",
            route_name="admin_select_provider",
            page_size=10,
        ),
        validators=[Optional()],
    )
    published = BooleanField(_("Published"))
    channel_order = IntegerField(
        _("Order"),
        validators=[Optional(), NumberRange(min=0)],
        default=9999,
        render_kw={"min": 0, "data-om-component": "form-mask", "data-om-mask-type": "numeral"},
    )
    category_id = IntegerField(
        _("Category ID"),
        validators=[DataRequired(), NumberRange(min=0)],
        render_kw={"min": 0, "data-om-component": "form-mask", "data-om-mask-type": "numeral"},
    )
    country_id = IntegerField(
        _("Country ID"),
        validators=[DataRequired(), NumberRange(min=0)],
        render_kw={"min": 0, "data-om-component": "form-mask", "data-om-mask-type": "numeral"},
    )
    language_id = IntegerField(
        _("Language ID"),
        validators=[DataRequired(), NumberRange(min=0)],
        render_kw={"min": 0, "data-om-component": "form-mask", "data-om-mask-type": "numeral"},
    )
    description = TextAreaField(_("Description"), validators=[Optional()], render_kw={"rows": 4})
    last_date = DateField(_("Last Date"), validators=[DataRequired()], format="%Y-%m-%d")

    field_layout = (
        FieldLayout("name", "md:col-span-6"),
        FieldLayout("logo", "md:col-span-6"),
        FieldLayout("name_cn", "md:col-span-4"),
        FieldLayout("name_tw", "md:col-span-4"),
        FieldLayout("name_es", "md:col-span-4"),
        FieldLayout("epg_id", "md:col-span-12"),
        FieldLayout("epg_lookup", "md:col-span-12"),
        FieldLayout("published", "md:col-span-3 flex items-end"),
        FieldLayout("channel_order", "md:col-span-3"),
        FieldLayout("logo_size", "md:col-span-3"),
        FieldLayout("last_date", "md:col-span-3"),
        FieldLayout("category_id", "md:col-span-4"),
        FieldLayout("country_id", "md:col-span-4"),
        FieldLayout("language_id", "md:col-span-4"),
        FieldLayout("description", "md:col-span-12"),
    )

    class Meta:
        """声明频道名称模型表单绑定关系。"""

        model = ChannelName
        fields = [
            "name",
            "name_cn",
            "name_tw",
            "name_es",
            "logo",
            "logo_size",
            "epg_id",
            "published",
            "channel_order",
            "category_id",
            "country_id",
            "language_id",
            "description",
            "last_date",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """初始化频道名称表单，并为编辑页远程频道控件补齐当前值。"""
        self.reference_ids = dict(kwargs.pop("reference_ids", None) or {})
        super().__init__(*args, **kwargs)
        self.bind_reference_defaults()
        self.bind_initial_epg_controls()

    def bind_reference_defaults(self) -> None:
        """把真实数据库里的旧外键默认值写入新建表单。"""
        if self.is_bound or getattr(self.instance, "id", None):
            return
        for field_name in ("category_id", "country_id", "language_id"):
            value = self.reference_ids.get(field_name)
            if value is not None and field_name in self._fields:
                self._fields[field_name].data = int(value)

    def bind_initial_epg_controls(self) -> None:
        """把当前绑定频道写入远程 select/autocomplete 的首屏回显。"""
        if self.is_bound:
            return
        channel = getattr(self.instance, "_bound_epg", None)
        if channel is None:
            return
        channel_id = optional_int(getattr(channel, "id", None))
        channel_name = getattr(channel, "name", "")
        if channel_id in {None, ""}:
            return

        self.epg_id.choices = [(channel_id, str(channel_name))]
        self.epg_id.data = channel_id
        self.epg_lookup.data = str(channel_id)
        render_kw = dict(getattr(self.epg_lookup, "render_kw", None) or {})
        render_kw["value"] = str(channel_name)
        self.epg_lookup.render_kw = render_kw

    async def save(self, *, commit: bool = False, session: Any = None) -> ChannelName:
        """保存频道名称实例并维护创建时间。"""
        channel_name = await super().save(commit=False)
        if channel_name.create_date is None:
            channel_name.create_date = utcnow_naive()
        if commit:
            active_session = session or self.session
            if active_session is None:
                raise ValueError("save(commit=True) requires a session")
            active_session.add(channel_name)
            flush_result = active_session.flush()
            if inspect.isawaitable(flush_result):
                await flush_result
        return channel_name

    async def clean(self) -> None:
        """校验旧外键是否存在，避免数据库约束错误变成 500。"""
        reference_ids = {
            "category_id": self.cleaned_data.get("category_id"),
            "country_id": self.cleaned_data.get("country_id"),
            "language_id": self.cleaned_data.get("language_id"),
        }
        if any(value in {"", None} for value in reference_ids.values()):
            return None
        from apps.epg_admin import services

        exists = await services.channel_name_reference_ids_exist(reference_ids)
        labels = {
            "category_id": _("Category ID"),
            "country_id": _("Country ID"),
            "language_id": _("Language ID"),
        }
        for field_name, ok in exists.items():
            if not ok:
                self.add_error(field_name, _("%(label)s does not exist.", label=labels[field_name]))
        return None


class CatalogChannelFilterForm(TailwindTableFilterForm):
    """频道目录列表筛选表单。"""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Search key, ID, identity")})
    status = SelectField(
        _("Status"),
        choices=[("", _("All status")), ("provisional", _("Provisional")), ("accepted", _("Accepted")), ("rejected", _("Rejected")), ("manual_review", _("Manual Review"))],
        validators=[Optional()],
    )
    owner_country_code = StringField(_("Owner Country"), validators=[Optional()], render_kw={"placeholder": "US"})
    confidence_min = IntegerField(_("Confidence Min"), validators=[Optional(), NumberRange(min=0, max=100)], render_kw={"min": 0, "max": 100})
    confidence_max = IntegerField(_("Confidence Max"), validators=[Optional(), NumberRange(min=0, max=100)], render_kw={"min": 0, "max": 100})

    field_layout = (
        FieldLayout("q", "lg:col-span-3 md:col-span-6"),
        FieldLayout("status", "lg:col-span-2 md:col-span-6"),
        FieldLayout("owner_country_code", "lg:col-span-2 md:col-span-6"),
        FieldLayout("confidence_min", "lg:col-span-2 md:col-span-6"),
        FieldLayout("confidence_max", "lg:col-span-2 md:col-span-6"),
    )


class CatalogChannelForm(TailwindModelForm):
    """频道目录新建和编辑表单。"""

    channel_key = StringField(_("Channel Key"), validators=[DataRequired()])
    owner_country_code = StringField(_("Owner Country"), validators=[DataRequired()])
    channel_id = StringField(_("Channel ID"), validators=[DataRequired()])
    identity_name = StringField(_("Identity Name"), validators=[DataRequired()])
    status = SelectField(
        _("Status"),
        choices=[("provisional", _("Provisional")), ("accepted", _("Accepted")), ("rejected", _("Rejected")), ("manual_review", _("Manual Review"))],
        validators=[DataRequired()],
    )
    confidence = IntegerField(_("Confidence"), validators=[DataRequired(), NumberRange(min=0, max=100)], default=0, render_kw={"min": 0, "max": 100})
    evidence_json = TextAreaField(_("Evidence JSON"), validators=[Optional()], render_kw={"rows": 6})

    field_layout = (
        FieldLayout("channel_key", "md:col-span-6"),
        FieldLayout("owner_country_code", "md:col-span-6"),
        FieldLayout("channel_id", "md:col-span-6"),
        FieldLayout("identity_name", "md:col-span-6"),
        FieldLayout("status", "md:col-span-6"),
        FieldLayout("confidence", "md:col-span-6"),
        FieldLayout("evidence_json", "md:col-span-12"),
    )

    class Meta:
        """声明频道目录模型表单绑定关系。"""

        model = CatalogChannel
        fields = ["channel_key", "owner_country_code", "channel_id", "identity_name", "status", "confidence", "evidence_json"]

    async def save(self, *, commit: bool = False, session: Any = None) -> CatalogChannel:
        """保存频道目录实例并维护创建/更新时间。"""
        catalog_channel = await super().save(commit=False)
        now = utcnow_naive()
        if catalog_channel.created_at is None:
            catalog_channel.created_at = now
        catalog_channel.updated_at = now
        if commit:
            active_session = session or self.session
            if active_session is None:
                raise ValueError("save(commit=True) requires a session")
            active_session.add(catalog_channel)
            flush_result = active_session.flush()
            if inspect.isawaitable(flush_result):
                await flush_result
        return catalog_channel


class CatalogFeedFilterForm(TailwindTableFilterForm):
    """CatalogFeed 列表筛选表单。"""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Search TVG ID, name, channel")})
    status = SelectField(
        _("Status"),
        choices=[("", _("All status")), ("provisional", _("Provisional")), ("accepted", _("Accepted")), ("rejected", _("Rejected")), ("manual_review", _("Manual Review")), ("retired", _("Retired"))],
        validators=[Optional()],
        validate_choice=False,
    )
    service_country_code = StringField(_("Country"), validators=[Optional()], render_kw={"placeholder": "US"})
    version_kind = StringField(_("Version"), validators=[Optional()], render_kw={"placeholder": "default"})
    is_default = SelectField(_("Default"), choices=[("", _("All feeds")), ("true", _("Default")), ("false", _("Variant"))], validators=[Optional()])
    has_logo = SelectField(_("Logo"), choices=[("", _("All logos")), ("true", _("Has logo")), ("false", _("No logo"))], validators=[Optional()])
    created_from = DateTimeLocalField(_("Created From"), validators=[Optional()], format="%Y-%m-%dT%H:%M", widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"))
    created_to = DateTimeLocalField(_("Created To"), validators=[Optional()], format="%Y-%m-%dT%H:%M", widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"))
    updated_from = DateTimeLocalField(_("Updated From"), validators=[Optional()], format="%Y-%m-%dT%H:%M", widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"))
    updated_to = DateTimeLocalField(_("Updated To"), validators=[Optional()], format="%Y-%m-%dT%H:%M", widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"))

    field_layout = (
        FieldLayout("q", "lg:col-span-3 md:col-span-6"),
        FieldLayout("status", "lg:col-span-2 md:col-span-6"),
        FieldLayout("service_country_code", "lg:col-span-2 md:col-span-6"),
        FieldLayout("version_kind", "lg:col-span-2 md:col-span-6"),
        FieldLayout("is_default", "lg:col-span-2 md:col-span-6"),
        FieldLayout("has_logo", "lg:col-span-2 md:col-span-6"),
        FieldLayout("created_from", "lg:col-span-3 md:col-span-6"),
        FieldLayout("created_to", "lg:col-span-3 md:col-span-6"),
        FieldLayout("updated_from", "lg:col-span-3 md:col-span-6"),
        FieldLayout("updated_to", "lg:col-span-3 md:col-span-6"),
    )


class UpstreamRecordFilterForm(TailwindTableFilterForm):
    """上游原始记录审计列表筛选表单。"""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Search source, key, payload, feed")})
    source_code = StringField(_("Source"), validators=[Optional()], render_kw={"placeholder": "source"})
    status = SelectField(
        _("Status"),
        choices=[
            ("", _("All status")),
            ("active", _("Active")),
            ("disappeared", _("Disappeared")),
            ("stale", _("Stale")),
            ("ignored", _("Ignored")),
            ("error", _("Error")),
        ],
        validators=[Optional()],
        validate_choice=False,
    )
    record_kind = StringField(_("Kind"), validators=[Optional()], render_kw={"placeholder": "channel"})
    catalog_feed_id = StringField(
        _("Catalog Feed"),
        validators=[Optional()],
        widget=AjaxAutocompleteWidget(
            provider="catalog_feeds",
            route_name="admin_select_provider",
            page_size=20,
        ),
    )
    last_seen_from = DateTimeLocalField(_("Last Seen From"), validators=[Optional()], format="%Y-%m-%dT%H:%M", widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"))
    last_seen_to = DateTimeLocalField(_("Last Seen To"), validators=[Optional()], format="%Y-%m-%dT%H:%M", widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"))

    field_layout = (
        FieldLayout("q", "lg:col-span-3 md:col-span-6"),
        FieldLayout("source_code", "lg:col-span-2 md:col-span-6"),
        FieldLayout("status", "lg:col-span-2 md:col-span-6"),
        FieldLayout("record_kind", "lg:col-span-2 md:col-span-6"),
        FieldLayout("catalog_feed_id", "lg:col-span-3 md:col-span-6"),
        FieldLayout("last_seen_from", "lg:col-span-3 md:col-span-6"),
        FieldLayout("last_seen_to", "lg:col-span-3 md:col-span-6"),
    )


class LogoAssetFilterForm(TailwindTableFilterForm):
    """Logo 资产质量工作台筛选表单。"""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Search feed, hash, source")})
    catalog_feed_id = StringField(
        _("Catalog Feed"),
        validators=[Optional()],
        widget=AjaxAutocompleteWidget(
            provider="catalog_feeds",
            route_name="admin_select_provider",
            page_size=20,
        ),
    )
    source_kind = StringField(_("Source Kind"), validators=[Optional()], render_kw={"placeholder": "upstream"})
    mime_type = StringField(_("Mime"), validators=[Optional()], render_kw={"placeholder": "image/png"})
    quality_min = IntegerField(_("Quality Min"), validators=[Optional(), NumberRange(min=0, max=100)], render_kw={"min": 0, "max": 100})
    quality_max = IntegerField(_("Quality Max"), validators=[Optional(), NumberRange(min=0, max=100)], render_kw={"min": 0, "max": 100})

    field_layout = (
        FieldLayout("q", "lg:col-span-3 md:col-span-6"),
        FieldLayout("catalog_feed_id", "lg:col-span-3 md:col-span-6"),
        FieldLayout("source_kind", "lg:col-span-2 md:col-span-6"),
        FieldLayout("mime_type", "lg:col-span-2 md:col-span-6"),
        FieldLayout("quality_min", "lg:col-span-1 md:col-span-6"),
        FieldLayout("quality_max", "lg:col-span-1 md:col-span-6"),
    )


MATCH_DECISION_CHOICES = [
    ("accepted", _("Accepted")),
    ("rejected", _("Rejected")),
    ("manual_review", _("Manual Review")),
    ("ignored", _("Ignored")),
    ("merged", _("Merged")),
]


class MatchDecisionFilterForm(TailwindTableFilterForm):
    """人工匹配决策审计列表筛选表单。"""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Search scope, reason, operator, feed")})
    decision_scope = StringField(_("Scope"), validators=[Optional()], render_kw={"placeholder": "channel/feed/logo"})
    decision = SelectField(
        _("Decision"),
        choices=[("", _("All decisions")), *MATCH_DECISION_CHOICES],
        validators=[Optional()],
        validate_choice=False,
    )
    decided_by = StringField(_("Operator"), validators=[Optional()], render_kw={"placeholder": _("Operator")})
    source_record_id = StringField(
        _("Source Record"),
        validators=[Optional()],
        widget=AjaxAutocompleteWidget(
            provider="upstream_records",
            route_name="admin_select_provider",
            page_size=20,
        ),
    )
    catalog_channel_id = StringField(
        _("Catalog Channel"),
        validators=[Optional()],
        widget=AjaxAutocompleteWidget(
            provider="catalog_channels",
            route_name="admin_select_provider",
            page_size=20,
        ),
    )
    catalog_feed_id = StringField(
        _("Catalog Feed"),
        validators=[Optional()],
        widget=AjaxAutocompleteWidget(
            provider="catalog_feeds",
            route_name="admin_select_provider",
            page_size=20,
        ),
    )

    field_layout = (
        FieldLayout("q", "lg:col-span-3 md:col-span-6"),
        FieldLayout("decision_scope", "lg:col-span-2 md:col-span-6"),
        FieldLayout("decision", "lg:col-span-2 md:col-span-6"),
        FieldLayout("decided_by", "lg:col-span-2 md:col-span-6"),
        FieldLayout("source_record_id", "lg:col-span-3 md:col-span-6"),
        FieldLayout("catalog_channel_id", "lg:col-span-3 md:col-span-6"),
        FieldLayout("catalog_feed_id", "lg:col-span-3 md:col-span-6"),
    )


class NotificationFilterForm(TailwindTableFilterForm):
    """通知中心筛选表单，驱动实时通知 table endpoint。"""

    q = StringField(_("Search"), render_kw={"type": "search", "placeholder": _("Search title, source, description")})
    notification_type = SelectField(
        _("Type"),
        choices=[("", _("All types")), ("decision", _("Decisions")), ("logo", _("Logo Quality")), ("upstream", _("Upstream"))],
        validators=[Optional()],
    )
    severity = SelectField(
        _("Severity"),
        choices=[("", _("All severity")), ("critical", _("Critical")), ("warning", _("Warning")), ("info", _("Info"))],
        validators=[Optional()],
    )
    created_from = DateTimeLocalField(
        _("Created From"),
        validators=[Optional()],
        format="%Y-%m-%dT%H:%M",
        widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"),
    )
    created_to = DateTimeLocalField(
        _("Created To"),
        validators=[Optional()],
        format="%Y-%m-%dT%H:%M",
        widget=DateTimePickerWidget(date_format="Y-m-d\\TH:i"),
    )

    field_layout = (
        FieldLayout("q", "xl:col-span-4 lg:col-span-6 md:col-span-12"),
        FieldLayout("notification_type", "xl:col-span-2 lg:col-span-3 md:col-span-6"),
        FieldLayout("severity", "xl:col-span-2 lg:col-span-3 md:col-span-6"),
        FieldLayout("created_from", "xl:col-span-2 lg:col-span-3 md:col-span-6"),
        FieldLayout("created_to", "xl:col-span-2 lg:col-span-3 md:col-span-6"),
    )


class MatchDecisionForm(TailwindModelForm):
    """人工匹配决策弹窗编辑表单。"""

    decision = SelectField(
        _("Decision"),
        choices=MATCH_DECISION_CHOICES,
        validators=[DataRequired()],
        render_kw={"required": True},
    )
    reason = TextAreaField(_("Reason"), validators=[DataRequired(), Length(max=255)], render_kw={"rows": 4, "maxlength": 255, "required": True})

    field_layout = (
        FieldLayout("decision", "md:col-span-12"),
        FieldLayout("reason", "md:col-span-12"),
    )

    class Meta:
        """声明人工匹配决策只允许编辑结论和理由。"""

        model = CatalogMatchDecision
        fields = ["decision", "reason"]


class CatalogFeedForm(TailwindModelForm):
    """CatalogFeed 新建和编辑表单。"""

    catalog_channel_id = SelectField(
        _("Catalog Channel"),
        choices=[],
        coerce=int,
        validate_choice=False,
        widget=AjaxSelectWidget(
            provider="catalog_channels",
            route_name="admin_select_provider",
            page_size=20,
            enhance_choices=True,
        ),
        validators=[DataRequired()],
    )
    feed_suffix = StringField(_("Feed Suffix"), validators=[Optional()])
    tvg_id = StringField(_("TVG ID"), validators=[DataRequired()])
    is_default = BooleanField(_("Default Feed"))
    canonical_name = StringField(_("Canonical Name"), validators=[DataRequired()])
    accepted_names_text = TextAreaField(_("Accepted Names"), validators=[Optional()], render_kw={"rows": 3})
    compatible_tvg_ids_text = TextAreaField(_("Compatible TVG IDs"), validators=[Optional()], render_kw={"rows": 3})
    service_country_code = StringField(_("Country"), validators=[Optional()])
    region_code = StringField(_("Region"), validators=[Optional()])
    language_hints_text = TextAreaField(_("Language Hints"), validators=[Optional()], render_kw={"rows": 3})
    timezone_hints_text = TextAreaField(_("Timezone Hints"), validators=[Optional()], render_kw={"rows": 3})
    version_kind = StringField(_("Version Kind"), validators=[DataRequired()], default="default")
    wikidata_qid = StringField(_("Wikidata QID"), validators=[Optional()])
    wikipedia_title = StringField(_("Wikipedia Title"), validators=[Optional()])
    official_website = StringField(_("Official Website"), validators=[Optional()])
    description = TextAreaField(_("Description"), validators=[Optional()], render_kw={"rows": 5})
    description_language = StringField(_("Description Language"), validators=[Optional()])
    description_source_type = StringField(_("Description Source Type"), validators=[Optional()])
    description_source_url = StringField(_("Description Source URL"), validators=[Optional()])
    status = SelectField(
        _("Status"),
        choices=[("provisional", _("Provisional")), ("accepted", _("Accepted")), ("rejected", _("Rejected")), ("manual_review", _("Manual Review")), ("retired", _("Retired"))],
        validators=[DataRequired()],
        validate_choice=False,
    )
    confidence = IntegerField(_("Confidence"), validators=[DataRequired(), NumberRange(min=0, max=100)], default=0, render_kw={"min": 0, "max": 100})
    evidence_json = TextAreaField(_("Evidence JSON"), validators=[Optional()], render_kw={"rows": 6})

    field_layout = (
        FieldLayout("catalog_channel_id", "md:col-span-12"),
        FieldLayout("tvg_id", "md:col-span-6"),
        FieldLayout("canonical_name", "md:col-span-6"),
        FieldLayout("feed_suffix", "md:col-span-4"),
        FieldLayout("version_kind", "md:col-span-4"),
        FieldLayout("is_default", "md:col-span-4 flex items-end"),
        FieldLayout("service_country_code", "md:col-span-4"),
        FieldLayout("region_code", "md:col-span-4"),
        FieldLayout("status", "md:col-span-4"),
        FieldLayout("confidence", "md:col-span-4"),
        FieldLayout("wikidata_qid", "md:col-span-4"),
        FieldLayout("wikipedia_title", "md:col-span-4"),
        FieldLayout("official_website", "md:col-span-12"),
        FieldLayout("accepted_names_text", "md:col-span-6"),
        FieldLayout("compatible_tvg_ids_text", "md:col-span-6"),
        FieldLayout("language_hints_text", "md:col-span-6"),
        FieldLayout("timezone_hints_text", "md:col-span-6"),
        FieldLayout("description", "md:col-span-12"),
        FieldLayout("description_language", "md:col-span-4"),
        FieldLayout("description_source_type", "md:col-span-4"),
        FieldLayout("description_source_url", "md:col-span-4"),
        FieldLayout("evidence_json", "md:col-span-12"),
    )

    class Meta:
        """声明 CatalogFeed 模型表单绑定关系。"""

        model = CatalogFeed
        fields = [
            "catalog_channel_id",
            "feed_suffix",
            "tvg_id",
            "is_default",
            "canonical_name",
            "accepted_names_text",
            "compatible_tvg_ids_text",
            "service_country_code",
            "region_code",
            "language_hints_text",
            "timezone_hints_text",
            "version_kind",
            "wikidata_qid",
            "wikipedia_title",
            "official_website",
            "description",
            "description_language",
            "description_source_type",
            "description_source_url",
            "status",
            "confidence",
            "evidence_json",
        ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """初始化 feed 表单，并为编辑页远程 CatalogChannel 控件补齐当前值。"""
        super().__init__(*args, **kwargs)
        self.bind_initial_catalog_channel()

    def bind_initial_catalog_channel(self) -> None:
        """把当前关联 CatalogChannel 写入远程 select 的首屏回显。"""
        if self.is_bound:
            return
        catalog_channel = getattr(self.instance, "catalog_channel", None)
        if catalog_channel is None:
            return
        channel_id = optional_int(getattr(catalog_channel, "id", None))
        label = catalog_channel_option_label(catalog_channel)
        if channel_id in {None, ""}:
            return

        self.catalog_channel_id.choices = [(channel_id, label)]
        self.catalog_channel_id.data = channel_id

    async def save(self, *, commit: bool = False, session: Any = None) -> CatalogFeed:
        """保存 CatalogFeed 并维护创建/更新时间。"""
        catalog_feed = await super().save(commit=False)
        now = utcnow_naive()
        if catalog_feed.created_at is None:
            catalog_feed.created_at = now
        catalog_feed.updated_at = now
        if commit:
            active_session = session or self.session
            if active_session is None:
                raise ValueError("save(commit=True) requires a session")
            active_session.add(catalog_feed)
            flush_result = active_session.flush()
            if inspect.isawaitable(flush_result):
                await flush_result
        return catalog_feed


def catalog_channel_option_label(catalog_channel: CatalogChannel) -> str:
    """返回 CatalogChannel 远程选择器和编辑回显共用的可读标签。"""
    identity = catalog_channel.identity_name or catalog_channel.channel_key or str(catalog_channel.id)
    channel_id = catalog_channel.channel_id or catalog_channel.channel_key or str(catalog_channel.id)
    return f"{identity} ({channel_id})"
