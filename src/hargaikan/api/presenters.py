"""Convert service results (dataclasses) into response models. No business logic here."""

from __future__ import annotations

from hargaikan.core.recommender import RecommendationItem
from hargaikan.core.travel_calc import round_money
from hargaikan.domain import messages as m
from hargaikan.domain.models import FishProfile, Location, SourceLabel, Species, SpeciesAlias, UserPreferences
from hargaikan.api import schemas as s
from hargaikan.services.catalog_service import CatalogSnapshot
from hargaikan.services.dashboard_service import Dashboard, DashboardItem, Overview, Suggestion
from hargaikan.services.forecast_service import ForecastResult
from hargaikan.services.price_service import PriceView
from hargaikan.services.profile_service import ProfileState
from hargaikan.services.recommendation_service import RecommendationView
from hargaikan.services.search_service import SearchView
from hargaikan.services.travel_service import TravelValue, TravelView


def demo_label(demo: bool) -> str | None:
    return m.DEMO_DATA if demo else None


def loc_ref(loc: Location) -> s.LocationRef:
    return s.LocationRef(id=loc.id, name=loc.name, level=loc.level)


def sp_ref(sp: Species) -> s.SpeciesRef:
    return s.SpeciesRef(id=sp.id, name_ms=sp.canonical_name_ms)


def legend() -> list[s.LegendItemOut]:
    return [
        s.LegendItemOut(label=label.value, icon=m.SOURCE_ICONS[label.value], description=m.SOURCE_DESCRIPTIONS[label.value])
        for label in SourceLabel
    ]


# --- catalog -----------------------------------------------------------------------------------


def _profile(p: FishProfile | None) -> s.FishProfileOut | None:
    if p is None:
        return None
    return s.FishProfileOut(
        texture=p.texture,
        taste=p.taste,
        bone_level=p.bone_level,
        cooking_methods=list(p.cooking_methods),
        allergen_groups=list(p.allergen_groups),
        notes=p.notes,
    )


def species(sp: Species, snap: CatalogSnapshot) -> s.SpeciesOut:
    aliases: tuple[SpeciesAlias, ...] = snap.aliases_by_species.get(sp.id, ())
    return s.SpeciesOut(
        id=sp.id,
        code=sp.code,
        name_ms=sp.canonical_name_ms,
        name_en=sp.canonical_name_en,
        allergen_groups=list(sp.allergen_groups),
        aliases=[s.AliasOut(alias=a.alias, language=a.language) for a in aliases],
        profile=_profile(snap.profiles_by_code.get(sp.code)),
    )


def location(loc: Location) -> s.LocationOut:
    return s.LocationOut(
        id=loc.id,
        name=loc.name,
        level=loc.level,
        parent_id=loc.parent_id,
        lat=loc.lat,
        lng=loc.lng,
        has_coordinates=loc.lat is not None and loc.lng is not None,
    )


def search(v: SearchView) -> s.SearchOut:
    return s.SearchOut(
        query=v.query,
        status=v.status,
        matches=[
            s.SearchMatchOut(species_id=x.species_id, name_ms=x.name_ms, matched_alias=x.matched_alias, score=round(x.score, 4))
            for x in v.matches
        ],
        backend=v.backend,
        degraded=v.degraded,
        notice=v.notice,
        confirm_prompt=v.confirm_prompt,
    )


# --- prices ------------------------------------------------------------------------------------


def _quote(value: float | None) -> s.QuoteOut | None:
    return s.QuoteOut(value=value) if value is not None else None


def reference(v: PriceView) -> s.ReferenceOut:
    band = chart = None
    if v.band is not None and v.used is not None:
        band = s.BandOut(
            p25=v.band.p25,
            median=v.band.median,
            p75=v.band.p75,
            record_count=v.band.record_count,
            confidence_level=v.band.confidence_level,
            period_start=v.band.period_start,
            period_end=v.band.period_end,
            grade=v.band.grade,
            band_source=v.band.band_source,
            location=loc_ref(v.used),
            demo=v.is_demo,
            demo_label=demo_label(v.is_demo),
        )
        if v.series is not None:
            chart = s.ChartOut(
                location=loc_ref(v.used),
                points=[s.WeeklyPointOut(week_start=w, median=med, record_count=n) for w, med, n in v.series],
                band_p25=v.band.p25,
                band_p75=v.band.p75,
                demo=v.is_demo,
                quote=_quote(v.quote),
            )
    verdict = (
        s.VerdictOut(kind=v.verdict.kind.value, text=v.verdict.text, icon=v.verdict.icon) if v.verdict is not None else None
    )
    return s.ReferenceOut(
        species=sp_ref(v.species),
        requested_location=loc_ref(v.requested),
        used_location=loc_ref(v.used) if v.used is not None else None,
        available=v.band is not None,
        is_fallback=v.is_fallback,
        message=v.message,
        band=band,
        grade=v.grade,
        grades=list(v.grades),
        quote=_quote(v.quote),
        quote_error=v.quote_error,
        verdict=verdict,
        chart=chart,
        chart_notice=v.series_notice,
        demo=v.is_demo,
    )


# --- recommendations ---------------------------------------------------------------------------


def _rec_item(it: RecommendationItem) -> s.RecommendationItemOut:
    return s.RecommendationItemOut(
        species=sp_ref(it.species),
        reason=it.reason,
        differences=list(it.differences),
        p25=it.p25,
        median=it.median,
        p75=it.p75,
        savings_rm_per_kg=round(it.savings_rm_per_kg, 2),
        record_count=it.record_count,
        confidence_level=it.confidence_level,
        location_name=it.location_name,
        period_start=it.period_start,
        period_end=it.period_end,
        similarity_score=it.similarity_score,
        over_budget=it.over_budget,
        over_budget_label=m.OVER_BUDGET if it.over_budget else None,
        demo=it.is_demo,
        demo_label=demo_label(it.is_demo),
    )


def recommendations(v: RecommendationView, personalised: bool, notice: str | None = None) -> s.RecommendationsOut:
    r = v.result
    return s.RecommendationsOut(
        species=sp_ref(v.species),
        location=loc_ref(v.location),
        items=[_rec_item(it) for it in r.items],
        empty_message=r.empty_message,
        empty_reason=r.empty_reason,
        original_allergy_match=r.original_allergy_match,
        allergy_caution=v.allergy_caution,
        disclaimer=v.disclaimer,
        personalised=personalised,
        notice=notice,
    )


# --- forecast ----------------------------------------------------------------------------------


def forecast(f: ForecastResult) -> s.ForecastOut:
    v = f.view
    prediction = None
    if v.available and f.prediction_location is not None:
        prediction = s.PredictionOut(
            predicted_median=v.predicted_median,  # type: ignore[arg-type]
            p10=v.p10,
            p90=v.p90,
            method=v.method or "",
            model_version=v.model_version or "",
            mae=v.mae,  # type: ignore[arg-type]
            target_start=v.target_start,  # type: ignore[arg-type]
            target_end=v.target_end,  # type: ignore[arg-type]
            last_data_date=v.last_data_date,  # type: ignore[arg-type]
            is_baseline=v.is_baseline,
            location=loc_ref(f.prediction_location),
            demo=f.demo,
        )
    return s.ForecastOut(
        species=sp_ref(f.species),
        location=loc_ref(f.requested),
        available=prediction is not None,
        message=v.message if prediction is None else None,
        prediction=prediction,
        p75_message=v.p75_message if prediction else None,
        early_buy_message=v.early_buy_message if prediction else None,
        quote=_quote(f.quote),
        quote_error=f.quote_error,
    )


# --- travel ------------------------------------------------------------------------------------


def _money(x: TravelValue) -> s.TravelValueOut:
    value = round_money(x.value)
    text = str(value) if value is not None else None
    return s.TravelValueOut(
        key=x.key,
        name=x.name,
        value=text,
        display=text if text is not None else m.NOT_COMPUTED,
        unit=x.unit,
        source_label=x.source_label,  # type: ignore[arg-type]
        display_label=x.display_label,
        note=x.note,
    )


def travel(v: TravelView) -> s.TravelOut:
    return s.TravelOut(
        mode=v.mode,
        fuel_type=v.fuel_type,
        return_trip=v.return_trip,
        estimated=v.estimated,
        message=v.message,
        inputs=[_money(x) for x in v.inputs],
        components=[_money(x) for x in v.components],
        travel_total=_money(v.travel_total),
        fish_cost=_money(v.fish_cost),
        grand_total=_money(v.grand_total),
        effective_rm_per_kg=_money(v.effective_rm_per_kg),
        fare_source=v.fare_source,  # type: ignore[arg-type]
        fare_source_detail=v.fare_source_detail,
        notes=[m.TRAVEL_SEPARATE_NOTE, m.TRAVEL_NOT_IN_FORECAST, *v.notices],
    )


# --- overview / dashboard ----------------------------------------------------------------------


def _suggestion(x: Suggestion) -> s.SuggestionOut:
    return s.SuggestionOut(
        species=sp_ref(x.species),
        location=loc_ref(x.location),
        p25=x.band.p25,
        median=x.band.median,
        p75=x.band.p75,
        record_count=x.band.record_count,
        confidence_level=x.band.confidence_level,
        period_start=x.band.period_start,
        period_end=x.band.period_end,
        demo=x.is_demo,
        demo_label=demo_label(x.is_demo),
    )


def overview(o: Overview) -> s.OverviewOut:
    return s.OverviewOut(
        species_count=o.species_count,
        premise_count=o.premise_count,
        latest_period=o.latest_period,
        demo=o.demo,
        demo_label=demo_label(o.demo),
        notice=o.notice,
        legend=legend(),
        suggestions_title=m.SUGGESTIONS_TITLE,
        suggestions=[_suggestion(x) for x in o.suggestions],
        suggestions_notice=o.suggestions_notice,
    )


def _dashboard_item(it: DashboardItem) -> s.DashboardItemOut:
    budget = None
    if it.budget is not None:
        budget = s.BudgetOut(
            budget_rm_per_kg=it.budget.budget,
            median=it.budget.median,
            within_budget=it.budget.within,
            text=it.budget.text,
            demo=bool(it.reference and it.reference.is_demo),
        )
    return s.DashboardItemOut(
        species=sp_ref(it.species),
        reference=reference(it.reference) if it.reference else None,
        reference_notice=it.reference_notice,
        recommendations=recommendations(it.recommendations, True) if it.recommendations else None,
        recommendations_notice=it.recommendations_notice,
        forecast=forecast(it.forecast) if it.forecast else None,
        forecast_notice=it.forecast_notice,
        budget=budget,
    )


def dashboard(d: Dashboard) -> s.DashboardOut:
    return s.DashboardOut(
        state=d.state,  # type: ignore[arg-type]
        message=d.message,
        location=loc_ref(d.location) if d.location else None,
        location_note=d.location_note,
        items=[_dashboard_item(it) for it in d.items],
        overview=overview(d.overview) if d.overview else None,
    )


# --- profile -----------------------------------------------------------------------------------


def profile(state: ProfileState, message: str | None = None) -> s.ProfileOut:
    p, prefs = state.profile, state.preferences or UserPreferences(user_id=state.user_id)
    return s.ProfileOut(
        user_id=state.user_id,
        has_profile=p is not None,
        consent_allergies=bool(p and p.consent_allergies),
        consent_location=bool(p and p.consent_location),
        consent_allergies_at=p.consent_allergies_at if p else None,
        consent_location_at=p.consent_location_at if p else None,
        preferences=s.PreferencesOut(
            preferred_species_ids=list(prefs.preferred_species_ids),
            allergy_species_ids=list(prefs.allergy_species_ids),
            allergy_terms=list(prefs.allergy_terms),
            allergy_groups=list(prefs.allergy_groups),
            budget_rm_per_kg=prefs.budget_rm_per_kg,
            preferred_location_id=prefs.preferred_location_id,
        ),
        session_notice=m.SESSION_NOTICE,
        message=message,
    )
