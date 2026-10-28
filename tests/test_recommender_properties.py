"""Property and unit tests for hargaikan.core.recommender (Properties 39–44)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from hypothesis import given
from hypothesis import strategies as st

from hargaikan.config import Settings
from hargaikan.core.location_fallback import BandResolution, resolve_band
from hargaikan.core.recommender import (
    CONF_RANK,
    RecommendationResult,
    matches_allergy,
    profile_differences,
    recommend,
)
from hargaikan.core.text import normalise_term
from hargaikan.domain import messages as m
from hargaikan.domain.models import (
    FishProfile,
    Location,
    PriceBand,
    SimilarityEdge,
    Species,
    SpeciesAlias,
    UserPreferences,
)

MY = Location("my", "Malaysia", "nasional", None, None, None)
KL = Location("kl", "Kuala Lumpur", "negeri", "my", None, None)
CHOW_KIT = Location("ck", "Pasar Chow Kit", "pasar", "kl", None, None)
LOCS = {loc.id: loc for loc in (MY, KL, CHOW_KIT)}

NAMES = ["Ikan Merah", "Ikan Jenahak", "Ikan Kembung", "Ikan Selar", "Udang Harimau", "Sotong", "Ikan Bawal Hitam",
         "Ikan Bawal Putih", "Ketam Bunga", "Ikan Tongkol"]
EN_NAMES = ["Red Snapper", "Golden Snapper", "Mackerel", "Prawn", "Squid", None]
ALIASES = ["merah", "jenahak", "kembong", "selar kuning", "udang galah", "sotong torak", "bawal", "ketam"]
TERMS = ["udang", "MERAH", "  bawal ", "ketam", "snapper", "ikan", "", "  ", "sotong"]
GROUPS = ["ikan_bersirip", "krustasea", "moluska"]
REASONS = ["Tekstur dan rasa hampir sama", "Sesuai untuk digoreng", "Isi putih yang serupa",
           "Ikan ini 100% selamat", "   "]
MEDIANS = [5.0, 8.0, 10.0, 12.0, 15.0, 20.0]
SCORES = [0.5, 0.7, 0.9, 1.0]
CONFIDENCE = ["rendah", "sederhana", "tinggi"]


def _band(species_id: str, location_id: str, median: float, count: int = 5, conf: str = "sederhana",
          source: str = "IKANSIGHT") -> PriceBand:
    return PriceBand(species_id, location_id, None, median * 0.9, median, median * 1.1, count, conf,  # type: ignore[arg-type]
                     date(2024, 1, 1), date(2024, 1, 14), source)


@dataclass(frozen=True)
class RecInput:
    original: Species
    prefs: UserPreferences | None
    catalog: dict[str, Species]
    aliases: list[SpeciesAlias]
    edges: list[SimilarityEdge]
    profiles: dict[str, FishProfile]
    bands: list[PriceBand]
    s: Settings

    def resolve(self, species_id: str) -> BandResolution:
        return resolve_band("ck", species_id, None, self.bands, LOCS)

    def run(self) -> RecommendationResult:
        return recommend(self.original, self.prefs, self.catalog, self.aliases, self.edges, self.profiles,
                         self.resolve, self.s)


@st.composite
def rec_inputs(draw: st.DrawFn, with_budget: bool | None = None) -> RecInput:
    names = draw(st.lists(st.sampled_from(NAMES), min_size=3, max_size=8, unique=True))
    species = [
        Species(
            id=f"s{i}", code=f"SP_{i}", canonical_name_ms=name, canonical_name_en=draw(st.sampled_from(EN_NAMES)),
            allergen_groups=tuple(draw(st.lists(st.sampled_from(GROUPS), max_size=2, unique=True))),
            active=draw(st.integers(min_value=0, max_value=9)) > 0 if i else True,
        )
        for i, name in enumerate(names)
    ]
    catalog = {sp.id: sp for sp in species}
    ids = list(catalog)
    aliases = [SpeciesAlias(sid, a, "ms") for sid in ids for a in draw(st.lists(st.sampled_from(ALIASES), max_size=2))]
    # Mostly real candidates; occasionally a self-link or an unknown id (both must be ignored).
    targets = st.sampled_from([*ids[1:]] * 6 + ["s0", "unknown"])
    reasons = st.sampled_from(REASONS[:3] * 4 + REASONS[3:])
    edges = [
        SimilarityEdge("s0", draw(targets), draw(st.sampled_from(SCORES)), draw(reasons),
                       ("tekstur",), draw(st.integers(min_value=0, max_value=3)) > 0)
        for _ in range(draw(st.integers(min_value=0, max_value=12)))
    ]
    # Edges from other species must be ignored.
    edges += [SimilarityEdge(ids[-1], ids[0], 0.9, "Serupa", ("rasa",), True)]
    bands = []
    for sid in ids:
        if draw(st.integers(min_value=0, max_value=9)) == 0:
            continue  # no band anywhere for this species
        # The original tends to be pricier so cheaper candidates exist; ties and dearer ones still occur.
        median = draw(st.sampled_from(MEDIANS[3:] if sid == "s0" and draw(st.booleans()) else MEDIANS))
        bands.append(_band(sid, draw(st.sampled_from(["ck", "kl"])), median,
                           draw(st.integers(min_value=1, max_value=12)), draw(st.sampled_from(CONFIDENCE)),
                           draw(st.sampled_from(["IKANSIGHT", "SYNTHETIC", "synthetic-demo"]))))
    profiles = {
        sp.code: FishProfile(sp.code, draw(st.sampled_from(["lembut", "pejal"])), draw(st.sampled_from(["manis", "tawar"])),
                             draw(st.sampled_from(["sedikit", "banyak"])),
                             tuple(draw(st.lists(st.sampled_from(["goreng", "kari", "stim"]), max_size=2, unique=True))),
                             sp.allergen_groups, None)
        for sp in species if draw(st.booleans())
    }
    if with_budget is None:
        budget = draw(st.none() | st.sampled_from([4.0, 6.0, 9.0, 11.0, 14.0, 25.0]))
    elif with_budget:
        budget = draw(st.sampled_from([4.0, 6.0, 9.0, 11.0, 14.0, 25.0]) | st.floats(min_value=0.5, max_value=30))
    else:
        budget = None
    # Allergy data is often empty so that candidates survive; when present it exercises every channel.
    some = st.integers(min_value=0, max_value=2).map(lambda k: k == 0)
    prefs = None if with_budget is None and draw(st.booleans()) else UserPreferences(
        user_id="u1",
        allergy_species_ids=tuple(draw(st.lists(st.sampled_from(ids), max_size=2, unique=True))) if draw(some) else (),
        allergy_terms=tuple(draw(st.lists(st.sampled_from(TERMS), max_size=2))) if draw(some) else (),
        allergy_groups=tuple(draw(st.lists(st.sampled_from(GROUPS), max_size=1))) if draw(some) else (),
        budget_rm_per_kg=budget,
    )
    s = Settings(min_records_for_recommendation=draw(st.integers(min_value=1, max_value=5)),
                 max_recommendations=draw(st.integers(min_value=1, max_value=5)))
    return RecInput(catalog["s0"], prefs, catalog, aliases, edges, profiles, bands, s)


# ------------------------------------------------------------------------------------ oracles


def _allergic(sp: Species, inp: RecInput) -> bool:
    p = inp.prefs or UserPreferences(user_id="")
    if sp.id in p.allergy_species_ids or set(sp.allergen_groups) & set(p.allergy_groups):
        return True
    names = [sp.canonical_name_ms, sp.canonical_name_en or "", *(a.alias for a in inp.aliases if a.species_id == sp.id)]
    names_n = [normalise_term(n) for n in names if n]
    terms_n = [normalise_term(t) for t in p.allergy_terms if normalise_term(t)]
    return any(t in n for t in terms_n for n in names_n)


def _forbidden(text: str) -> bool:
    return any(p.lower() in text.lower() for p in m.FORBIDDEN_PHRASES)


def _eligible(inp: RecInput) -> dict[str, float]:
    """candidate id → candidate median, for every candidate passing filters 1–4."""
    orig = inp.resolve("s0").band
    if orig is None:
        return {}
    out = {}
    for e in inp.edges:
        cand = inp.catalog.get(e.to_species_id)
        if e.from_species_id != "s0" or cand is None or cand.id == "s0" or not cand.active:
            continue
        if _allergic(cand, inp) or not e.verified or not e.reason.strip() or _forbidden(e.reason):
            continue
        band = inp.resolve(cand.id).band
        if band is None or band.record_count < inp.s.min_records_for_recommendation or band.median >= orig.median:
            continue
        out[cand.id] = band.median
    return out


def _texts(res: RecommendationResult) -> list[str]:
    texts = [res.disclaimer, res.empty_message or "", res.empty_reason or ""]
    for it in res.items:
        texts += [it.reason, *it.differences]
    return texts


# --------------------------------------------------------------------------------- properties


# Feature: hargaikan-v2, Property 39: Allergy filter excludes every matching species
@given(inp=rec_inputs())
def test_allergy_filter_excludes_every_matching_species(inp: RecInput) -> None:
    """*For any* catalog, aliases, similarity edges, bands and user allergy data (species ids,
    free-text terms, groups), no recommended species has an id in the allergy list, a normalised name
    or alias containing a normalised allergy term, or an allergen group in the user's groups.

    **Validates: Requirements 16.1**
    """
    res = inp.run()
    for it in res.items:
        assert not _allergic(it.species, inp)
        assert not matches_allergy(it.species, inp.aliases, inp.prefs or UserPreferences(user_id=""))


# Feature: hargaikan-v2, Property 40: Recommendations are verified, data-backed and cheaper
@given(inp=rec_inputs())
def test_recommendations_are_verified_data_backed_and_cheaper(inp: RecInput) -> None:
    """*For any* recommender input, every returned item has a verified edge from the original species,
    a resolved band with record_count ≥ the configured minimum, and
    `savings_rm_per_kg = original median − candidate median > 0`.

    **Validates: Requirements 16.2, 16.3, 16.4, 16.14**
    """
    res = inp.run()
    orig = inp.resolve("s0").band
    for it in res.items:
        verified = [e for e in inp.edges if e.from_species_id == "s0" and e.to_species_id == it.species.id and e.verified]
        assert verified
        assert it.similarity_score in {e.similarity_score for e in verified}
        band = inp.resolve(it.species.id).band
        assert band is not None and orig is not None
        assert it.record_count == band.record_count >= inp.s.min_records_for_recommendation
        assert it.median == band.median
        assert it.savings_rm_per_kg == orig.median - band.median
        assert it.savings_rm_per_kg > 0
    assert len({it.species.id for it in res.items}) == len(res.items)


# Feature: hargaikan-v2, Property 41: Budget preference
@given(inp=rec_inputs(with_budget=True))
def test_budget_preference(inp: RecInput) -> None:
    """*For any* recommender input with a budget: if at least one eligible candidate has
    median ≤ budget, every returned item has median ≤ budget and over_budget false. Otherwise every
    returned item has over_budget true and items are sorted by (median − budget) ascending.

    **Validates: Requirements 16.5, 16.6**
    """
    assert inp.prefs is not None and inp.prefs.budget_rm_per_kg is not None
    budget = inp.prefs.budget_rm_per_kg
    res = inp.run()
    eligible = _eligible(inp)
    assert {it.species.id for it in res.items} <= set(eligible)
    if any(med <= budget for med in eligible.values()):
        assert res.items
        assert all(it.median <= budget and not it.over_budget for it in res.items)
    else:
        assert len(res.items) == min(len(eligible), inp.s.max_recommendations)
        assert all(it.over_budget for it in res.items)
        gaps = [it.median - budget for it in res.items]
        assert gaps == sorted(gaps)


# Feature: hargaikan-v2, Property 42: Ranking and cap
@given(inp=rec_inputs())
def test_ranking_and_cap(inp: RecInput) -> None:
    """*For any* recommender input where items are within budget (or no budget is set), returned items
    are ordered by similarity_score descending, then savings descending, then confidence_level
    descending, and the count is at most max_recommendations.

    **Validates: Requirements 16.7**
    """
    res = inp.run()
    assert len(res.items) <= inp.s.max_recommendations
    if any(it.over_budget for it in res.items):
        return
    keys = [(it.similarity_score, it.savings_rm_per_kg, CONF_RANK[it.confidence_level]) for it in res.items]
    assert keys == sorted(keys, reverse=True)
    budget = inp.prefs.budget_rm_per_kg if inp.prefs else None
    within = [c for c, med in _eligible(inp).items() if budget is None or med <= budget]
    assert len(res.items) == min(len(within), inp.s.max_recommendations)


# Feature: hargaikan-v2, Property 43: Recommendation items are complete
@given(inp=rec_inputs())
def test_recommendation_items_are_complete(inp: RecInput) -> None:
    """*For any* returned item, the reason, P25, median, P75, savings, differences, record_count,
    location name and period are all present (non-null, and reason and location name non-empty).

    **Validates: Requirements 16.8**
    """
    for it in inp.run().items:
        assert it.reason.strip() and it.location_name.strip()
        for v in (it.p25, it.median, it.p75, it.savings_rm_per_kg, it.differences, it.record_count,
                  it.period_start, it.period_end):
            assert v is not None
        assert it.p25 <= it.median <= it.p75
        assert it.location_name in {"Pasar Chow Kit", "Kuala Lumpur"}
        assert it.is_demo == ("SYNTHETIC" in inp.resolve(it.species.id).band.band_source.upper())  # type: ignore[union-attr]


# Feature: hargaikan-v2, Property 44: Recommendation result envelope
@given(inp=rec_inputs())
def test_recommendation_result_envelope(inp: RecInput) -> None:
    """*For any* recommender input, the result disclaimer equals the fixed disclaimer text. The result
    has empty_message "Tiada cadangan" exactly when items is empty, and empty_reason "Data harga ikan
    asal belum tersedia." when the original species has no band. `original_allergy_match` equals
    `matches_allergy(original)`, and no text in the result contains a Forbidden_Phrase.

    **Validates: Requirements 16.9, 16.10, 16.11, 16.12, 16.13**
    """
    res = inp.run()
    assert res.disclaimer == (
        "Cadangan ini bukan nasihat perubatan. Jika anda mempunyai alergi makanan, semak nama spesies, label "
        "produk dan risiko pencemaran silang dengan penjual atau profesional kesihatan."
    )
    assert (res.empty_message == "Tiada cadangan") == (not res.items)
    if res.items:
        assert res.empty_message is None and res.empty_reason is None
    if inp.resolve("s0").band is None:
        assert not res.items
        assert res.empty_reason == "Data harga ikan asal belum tersedia."
    else:
        assert res.empty_reason is None
    assert res.original_allergy_match == _allergic(inp.original, inp)
    assert not any(_forbidden(t) for t in _texts(res))


# ------------------------------------------------------------------------------------ examples

MERAH = Species("merah", "IKAN_MERAH", "Ikan Merah", "Red Snapper", ("ikan_bersirip",), True)
JENAHAK = Species("jenahak", "IKAN_JENAHAK", "Ikan Jenahak", "Golden Snapper", ("ikan_bersirip",), True)
UDANG = Species("udang", "UDANG_HARIMAU", "Udang Harimau", "Tiger Prawn", ("krustasea",), True)
EX_CATALOG = {sp.id: sp for sp in (MERAH, JENAHAK, UDANG)}
EX_PROFILES = {
    "IKAN_MERAH": FishProfile("IKAN_MERAH", "lembut", "manis", "sedikit", ("goreng", "kari"), ("ikan_bersirip",), None),
    "IKAN_JENAHAK": FishProfile("IKAN_JENAHAK", "pejal", "manis", "sedikit", ("goreng", "kari"), ("ikan_bersirip",), None),
}
EX_BANDS = [_band("merah", "kl", 30.0, 12, "tinggi"), _band("jenahak", "kl", 22.0, 8, "sederhana"),
            _band("udang", "kl", 18.0, 10, "tinggi")]


def _ex_recommend(edges, prefs=None, bands=EX_BANDS):  # type: ignore[no-untyped-def]
    return recommend(MERAH, prefs, EX_CATALOG, [], edges, EX_PROFILES,
                     lambda sid: resolve_band("ck", sid, None, bands, LOCS), Settings())


def test_ikan_merah_recommends_jenahak() -> None:
    edge = SimilarityEdge("merah", "jenahak", 0.85, "Sama-sama ikan snapper berisi putih", ("tekstur",), True)
    res = _ex_recommend([edge])
    assert res.empty_message is None and len(res.items) == 1
    it = res.items[0]
    assert it.species == JENAHAK
    assert it.reason == "Sama-sama ikan snapper berisi putih"
    assert it.savings_rm_per_kg == 8.0
    assert (it.median, it.record_count, it.confidence_level) == (22.0, 8, "sederhana")
    assert it.location_name == "Kuala Lumpur"
    assert it.differences == ("Tekstur: lembut → pejal",)
    assert not it.over_budget and not it.is_demo
    assert res.disclaimer == m.RECOMMENDATION_DISCLAIMER


def test_draft_only_similarity_gives_tiada_cadangan() -> None:
    edges = [SimilarityEdge("merah", "jenahak", 0.85, "Serupa", ("tekstur",), False),
             SimilarityEdge("merah", "udang", 0.4, "Serupa", ("rasa",), False)]
    res = _ex_recommend(edges)
    assert res.items == ()
    assert res.empty_message == "Tiada cadangan"
    assert res.empty_reason is None


def test_original_without_band_gives_original_no_data() -> None:
    edge = SimilarityEdge("merah", "jenahak", 0.85, "Serupa", ("tekstur",), True)
    res = _ex_recommend([edge], bands=EX_BANDS[1:])
    assert (res.items, res.empty_message, res.empty_reason) == ((), "Tiada cadangan", "Data harga ikan asal belum tersedia.")


def test_allergy_group_excludes_candidate_and_flags_original() -> None:
    edges = [SimilarityEdge("merah", "jenahak", 0.85, "Serupa", ("tekstur",), True),
             SimilarityEdge("merah", "udang", 0.4, "Rasa manis", ("rasa",), True)]
    prefs = UserPreferences(user_id="u", allergy_groups=("ikan_bersirip",))
    res = _ex_recommend(edges, prefs)
    assert [it.species.id for it in res.items] == ["udang"]
    assert res.original_allergy_match is True
    assert res.items[0].differences == (m.PROFILE_INCOMPLETE,)


def test_allergy_term_matches_alias_substring() -> None:
    prefs = UserPreferences(user_id="u", allergy_terms=("  SNAPPER ",))
    assert matches_allergy(JENAHAK, [], prefs)
    assert not matches_allergy(UDANG, [], prefs)
    assert matches_allergy(UDANG, [SpeciesAlias("udang", "udang belang", "ms")], UserPreferences("u", allergy_terms=("belang",)))
    assert not matches_allergy(UDANG, [], UserPreferences("u", allergy_terms=("", "   ")))


def test_over_budget_items_are_labelled_and_nearest_first() -> None:
    edges = [SimilarityEdge("merah", "jenahak", 0.85, "Serupa", ("tekstur",), True),
             SimilarityEdge("merah", "udang", 0.4, "Rasa manis", ("rasa",), True)]
    res = _ex_recommend(edges, UserPreferences(user_id="u", budget_rm_per_kg=10.0))
    assert [it.species.id for it in res.items] == ["udang", "jenahak"]
    assert all(it.over_budget for it in res.items)
    res = _ex_recommend(edges, UserPreferences(user_id="u", budget_rm_per_kg=20.0))
    assert [(it.species.id, it.over_budget) for it in res.items] == [("udang", False)]


def test_forbidden_phrases_never_appear() -> None:
    edges = [SimilarityEdge("merah", "jenahak", 0.85, "Ikan ini dijamin selamat untuk semua", ("tekstur",), True),
             SimilarityEdge("merah", "udang", 0.4, "Penjual penipu jual mahal", ("rasa",), True)]
    res = _ex_recommend(edges)
    assert res.items == () and res.empty_message == m.NO_RECOMMENDATION
    assert not any(_forbidden(t) for t in _texts(res))


def test_profile_differences_lists_changed_fields() -> None:
    a = FishProfile("A", "lembut", "manis", "sedikit", ("goreng", "kari"), (), None)
    b = FishProfile("B", "pejal", "manis", "banyak", ("kari", "goreng"), (), "nota")
    assert profile_differences(a, b) == ("Tekstur: lembut → pejal", "Tulang: sedikit → banyak")
    assert profile_differences(a, a) == ()
    assert profile_differences(None, b) == ("Profil ikan belum lengkap",)
    c = FishProfile("C", "lembut", "manis", "sedikit", ("stim",), (), None)
    assert profile_differences(a, c) == ("Cara masakan: goreng, kari → stim",)
