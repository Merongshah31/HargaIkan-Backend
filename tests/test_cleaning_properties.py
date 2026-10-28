"""Property-based tests for hargaikan.pipeline.cleaning (Properties 4–9 and 11 of the design)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, timedelta

from hypothesis import given
from hypothesis import strategies as st

from hargaikan.config import Settings
from hargaikan.core.text import normalise_name, normalise_term
from hargaikan.domain.ids import species_id
from hargaikan.domain.models import Species, SpeciesAlias
from hargaikan.pipeline.cleaning import (
    CleanRow,
    IngestReport,
    ParsedPremise,
    RawRow,
    classify,
    clean,
    dedupe,
    dedupe_key,
    is_representable,
    map_species,
    normalise_premise,
    parse_date,
    parse_item,
    species_code,
    to_price_records,
    with_unmatched,
)

SETTINGS = Settings()

# --- Shared strategies ---------------------------------------------------------------------------

whitespace = st.text(alphabet=" \t\u00a0\u3000", max_size=3)
whitespace_nonempty = st.text(alphabet=" \t\u00a0\u3000", min_size=1, max_size=3)
any_chars = st.characters(blacklist_categories=("Cs",))


def _dmy(d: date) -> str:
    """d.strftime("%d/%m/%Y") with a zero-padded 4-digit year on every platform."""
    return f"{d:%d/%m/}{d.year:04d}"


# --- Property 4 ----------------------------------------------------------------------------------

species_text = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="()"), min_size=1, max_size=30).filter(
    lambda s: s.strip() != ""
)
grade_text = st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters=")"), min_size=1, max_size=30).filter(
    lambda s: s.strip() != ""
)


# Feature: hargaikan-v2, Property 4: Item parsing splits species and grade
@given(s=species_text, g=grade_text, lead=whitespace, trail=whitespace)
def test_item_parsing_splits_species_and_grade(s: str, g: str, lead: str, trail: str) -> None:
    """**Validates: Requirements 3.1, 3.2**

    For any non-blank species text without parentheses and any non-blank grade text without ")",
    with arbitrary case and surrounding whitespace, parse_item(f"{s} ({g})") returns
    (normalise_name(s), normalise_name(g)), and parse_item(s) returns (normalise_name(s), None).
    """
    s_ws = f"{lead}{s}{trail}"
    assert parse_item(f"{s_ws} ({g})") == (normalise_name(s), normalise_name(g))
    assert parse_item(f"{s_ws} ({g})".lower()) == (normalise_name(s.lower()), normalise_name(g.lower()))
    assert parse_item(s_ws) == (normalise_name(s), None)


# --- Property 5 ----------------------------------------------------------------------------------

word = st.text(alphabet=st.characters(categories=("Lu", "Ll", "Nd")), min_size=1, max_size=8).filter(
    lambda w: (n := normalise_name(w)) != "" and len(n.split()) == 1 and "(" not in n and ")" not in n
)


@st.composite
def premise_variants(draw: st.DrawFn) -> tuple[str, str]:
    """(minimal-whitespace premise, the same premise with whitespace inserted)."""
    chain = draw(st.lists(word, min_size=1, max_size=4))
    branch = draw(st.one_of(st.none(), st.lists(word, min_size=1, max_size=3)))

    def join(words: list[str]) -> str:
        out = words[0]
        for w in words[1:]:
            out += draw(whitespace_nonempty) + w
        return out

    minimal = " ".join(chain) + (f"({' '.join(branch)})" if branch else "")
    spaced = draw(whitespace) + join(chain) + draw(whitespace)
    if branch:
        spaced += "(" + draw(whitespace) + join(branch) + draw(whitespace) + ")" + draw(whitespace)
    return minimal, spaced


# Feature: hargaikan-v2, Property 5: Premise normalisation is idempotent and whitespace-invariant
@given(p=st.text(alphabet=any_chars, max_size=40), variants=premise_variants())
def test_premise_normalisation_idempotent_and_whitespace_invariant(p: str, variants: tuple[str, str]) -> None:
    """**Validates: Requirements 3.3**

    For any premise string, normalising the display of a normalised premise gives the same
    ParsedPremise, and inserting or removing whitespace around words and parentheses does not
    change the key.
    """
    once = normalise_premise(p)
    assert normalise_premise(once.display) == once

    minimal, spaced = variants
    assert normalise_premise(spaced).key == normalise_premise(minimal).key
    with_spaces = normalise_premise(minimal).display  # "CHAIN (BRANCH)" with a space before "("
    assert normalise_premise(with_spaces).key == normalise_premise(minimal).key


# --- Property 6 ----------------------------------------------------------------------------------


def _raw(**overrides: object) -> RawRow:
    base: dict[str, object] = dict(
        item="IKAN BAWAL HITAM",
        premise="AEON ( AU2 )",
        harga_RM_per_kg="48.9",
        harga_terendah=None,
        harga_tertinggi=None,
        bilangan_rekod="2",
        tarikh_mula="19/10/2026",
        tarikh_akhir="26/10/2026",
        harga_min=None,
        harga_maks=None,
        snapshot=0,
    )
    base.update(overrides)
    return RawRow(**base)  # type: ignore[arg-type]


@st.composite
def invalid_calendar_strings(draw: st.DrawFn) -> str:
    """Strings shaped like a supported format but naming no real date (e.g. 31/02/2026, month 13)."""
    year = draw(st.integers(1, 9999))
    month = draw(st.integers(0, 99))
    day = draw(st.integers(0, 99))
    try:
        date(year, month, day)
        month = 13  # it was a real date: force an invalid month
    except ValueError:
        pass
    if draw(st.booleans()):
        return f"{day:02d}/{month:02d}/{year:04d}"
    return f"{year:04d}-{month:02d}-{day:02d}"


# Fewer than 4 digits cannot contain the 4-digit year that both formats require.
non_date_text = st.text(alphabet=any_chars, max_size=30).filter(lambda s: sum(c.isdigit() for c in s) < 4)
other_fields = st.fixed_dictionaries(
    {
        "harga_RM_per_kg": st.one_of(st.none(), st.sampled_from(["48.9", "-1", "0", "abc", "inf"])),
        "bilangan_rekod": st.one_of(st.none(), st.sampled_from(["2", "0", "1.5", "x"])),
        "harga_terendah": st.one_of(st.none(), st.sampled_from(["10", "99"])),
    }
)


# Feature: hargaikan-v2, Property 6: Date formats parse to the same date
@given(
    d=st.dates(),
    bad=st.one_of(non_date_text, invalid_calendar_strings()),
    bad_in_start=st.booleans(),
    d1=st.dates(min_value=date(1, 1, 2)),
    gap=st.integers(1, 3650),
    fields=other_fields,
)
def test_date_formats_parse_to_the_same_date(
    d: date, bad: str, bad_in_start: bool, d1: date, gap: int, fields: dict[str, object]
) -> None:
    """**Validates: Requirements 3.4, 3.5**

    For any calendar date d, parse_date(d.strftime("%d/%m/%Y")) == parse_date(d.isoformat()) == d.
    For any string matching neither format, or any row with tarikh_mula later than tarikh_akhir,
    the row is classified invalid_date.
    """
    assert parse_date(_dmy(d)) == parse_date(d.isoformat()) == d

    assert parse_date(bad) is None
    row = _raw(tarikh_mula=bad, **fields) if bad_in_start else _raw(tarikh_akhir=bad, **fields)
    rows, _ = clean([row], SETTINGS)
    assert rows[0].quality_status == "invalid_date"

    later = d1
    earlier = d1 - timedelta(days=min(gap, (d1 - date.min).days))
    if later > earlier:
        rows, _ = clean([_raw(tarikh_mula=_dmy(later), tarikh_akhir=earlier.isoformat(), **fields)], SETTINGS)
        assert rows[0].quality_status == "invalid_date"


# --- Property 7 ----------------------------------------------------------------------------------

ITEMS = ["IKAN BAWAL HITAM (ANTARA 2 HINGGA 5 EKOR SEKILO)", "ikan kembung", "IKAN TONGKOL"]
PREMISES = ["AEON ( AU2 )", "AEON BIG (KEPONG)", "NSK GROCER ( TIMES SQUARE )"]
DAYS = [date(2026, 10, 12), date(2026, 10, 19), date(2026, 10, 26)]


@st.composite
def spaced(draw: st.DrawFn, options: list[str]) -> str:
    text = draw(st.sampled_from(options))
    text = draw(st.sampled_from([text, text.lower(), text.upper()]))
    return draw(whitespace) + text.replace(" ", draw(st.sampled_from([" ", "  ", "\t"]))) + draw(whitespace)


@st.composite
def date_value(draw: st.DrawFn) -> object:
    d = draw(st.sampled_from(DAYS))
    return draw(st.sampled_from([d, _dmy(d), d.isoformat(), f"{d.isoformat()} 00:00:00", "bukan tarikh"]))


value = st.one_of(
    st.none(),
    st.sampled_from(["48.9", "29.9", "abc", "", "0", "-3"]),
    st.floats(min_value=-10, max_value=100, allow_nan=False),
)
count = st.one_of(st.none(), st.sampled_from(["1", "2", "3", "0", "x"]), st.integers(-1, 5))


@st.composite
def raw_rows(draw: st.DrawFn) -> RawRow:
    return RawRow(
        item=draw(spaced(ITEMS)),
        premise=draw(spaced(PREMISES)),
        harga_RM_per_kg=draw(value),
        harga_terendah=draw(value),
        harga_tertinggi=draw(value),
        bilangan_rekod=draw(count),
        tarikh_mula=draw(date_value()),
        tarikh_akhir=draw(date_value()),
        harga_min=draw(value),
        harga_maks=draw(value),
        snapshot=draw(st.integers(0, 3)),
    )


# Feature: hargaikan-v2, Property 7: Dedupe keeps one row per key from the highest snapshot
@given(rows=st.lists(raw_rows(), max_size=25), data=st.data())
def test_dedupe_keeps_one_row_per_key_from_highest_snapshot(rows: list[RawRow], data: st.DataObject) -> None:
    """**Validates: Requirements 3.6**

    For any list of raw rows spread across snapshots, the deduplicated output has unique
    Dedupe_Keys, its key set equals the input key set, each kept row has the highest snapshot index
    among rows sharing its key, the output set does not depend on input order, and deduplicating
    twice equals deduplicating once.
    """
    out, duplicates, conflicts = dedupe(rows)
    out_keys = [dedupe_key(r) for r in out]
    assert len(out_keys) == len(set(out_keys))
    assert set(out_keys) == {dedupe_key(r) for r in rows}
    assert duplicates == len(rows) - len(out)
    assert 0 <= conflicts <= duplicates

    for kept in out:
        k = dedupe_key(kept)
        assert kept.snapshot == max(r.snapshot for r in rows if dedupe_key(r) == k)
        assert kept in rows

    shuffled = data.draw(st.permutations(rows))
    assert set(dedupe(shuffled)[0]) == set(out)
    assert set(dedupe(out)[0]) == set(out)


# --- Property 8 ----------------------------------------------------------------------------------

NAMES = ["IKAN A", "IKAN B", "IKAN C"]
maybe_date = st.one_of(st.none(), st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31)))
price = st.one_of(
    st.none(),
    st.floats(allow_nan=True, allow_infinity=True),
    st.floats(min_value=-5, max_value=600),
    st.sampled_from([0.0, 1.0, 500.0, 0.99, 500.01]),
)
aux = st.one_of(st.none(), st.floats(min_value=-5, max_value=600))
counts = st.one_of(st.none(), st.integers(-5, 50))
fence = st.tuples(st.floats(-50, 300), st.floats(0, 400)).map(lambda t: (min(t), max(t)))
fences_strategy = st.dictionaries(st.sampled_from(NAMES), fence, max_size=3)
PREMISE = ParsedPremise(key="AEON (AU2)", chain="AEON", branch="AU2", display="AEON (AU2)")


@st.composite
def clean_rows(draw: st.DrawFn) -> CleanRow:
    return CleanRow(
        species_name=draw(st.sampled_from(NAMES)),
        grade=None,
        premise=PREMISE,
        raw_item_name="IKAN",
        price_rm_per_kg=draw(price),
        price_low=draw(aux),
        price_high=draw(aux),
        price_min=draw(aux),
        price_max=draw(aux),
        record_count=draw(counts),
        date_start=draw(maybe_date),
        date_end=draw(maybe_date),
        snapshot=0,
        quality_status="ok",
    )


def oracle_status(row: CleanRow, fences: Mapping[str, tuple[float, float]], s: Settings) -> str:
    """Independent statement of the rule order in Requirements 3.5 and 3.7–3.10."""
    rules: list[tuple[str, bool]] = []
    rules.append(("invalid_date", row.date_start is None or row.date_end is None or row.date_start > row.date_end))
    p = row.price_rm_per_kg
    price_bad = p is None or math.isnan(p) or math.isinf(p) or p <= 0
    rules.append(("invalid_price", price_bad))
    c = row.record_count
    rules.append(("invalid_count", c is None or isinstance(c, bool) or not isinstance(c, int) or c < 1))
    if not price_bad:
        inconsistent = (
            (row.price_low is not None and row.price_low > p)
            or (row.price_high is not None and p > row.price_high)
            or (row.price_min is not None and row.price_max is not None and row.price_min > row.price_max)
        )
        rules.append(("inconsistent_range", inconsistent))
        in_range = s.min_price_rm_per_kg <= p <= s.max_price_rm_per_kg
        in_fence = row.species_name not in fences or fences[row.species_name][0] <= p <= fences[row.species_name][1]
        rules.append(("outlier", not (in_range and in_fence)))
    for status, failed in rules:
        if failed:
            return status
    return "ok"


# Feature: hargaikan-v2, Property 8: Row classification follows the rule order
@given(row=clean_rows(), fences=fences_strategy, bounds=st.tuples(st.floats(0.01, 50), st.floats(50, 1000)))
def test_row_classification_follows_rule_order(
    row: CleanRow, fences: dict[str, tuple[float, float]], bounds: tuple[float, float]
) -> None:
    """**Validates: Requirements 3.7, 3.8, 3.9, 3.10**

    For any clean row with generated dates, prices, counts and range values, classify returns the
    same Quality_Status as an independent oracle that applies the rules in order: invalid_date,
    invalid_price, invalid_count, inconsistent_range, outlier, ok.
    """
    for s in (SETTINGS, Settings(min_price_rm_per_kg=bounds[0], max_price_rm_per_kg=bounds[1])):
        assert classify(row, fences, s) == oracle_status(row, fences, s)


# --- Property 9 ----------------------------------------------------------------------------------


@st.composite
def varied_raw_rows(draw: st.DrawFn) -> RawRow:
    """Raw rows mixing valid values (so ok/outlier/inconsistent rows occur) with broken ones."""
    good_price = st.floats(min_value=0.5, max_value=600).map(lambda v: f"{v:.2f}")
    return RawRow(
        item=draw(spaced(ITEMS)),
        premise=draw(spaced(PREMISES)),
        harga_RM_per_kg=draw(st.one_of(good_price, good_price, value)),
        harga_terendah=draw(st.one_of(st.none(), good_price)),
        harga_tertinggi=draw(st.one_of(st.none(), good_price)),
        bilangan_rekod=draw(st.one_of(st.sampled_from(["1", "2", "3"]), count)),
        tarikh_mula=draw(date_value()),
        tarikh_akhir=draw(date_value()),
        harga_min=draw(st.one_of(st.none(), good_price)),
        harga_maks=draw(st.one_of(st.none(), good_price)),
        snapshot=draw(st.integers(0, 3)),
    )


# Feature: hargaikan-v2, Property 9: Cleaning preserves provenance and accounts for every row
@given(rows=st.lists(varied_raw_rows(), max_size=30), source=st.sampled_from(["IKANSIGHT", "SYNTHETIC"]))
def test_cleaning_preserves_provenance_and_accounts_for_every_row(rows: list[RawRow], source: str) -> None:
    """**Validates: Requirements 3.12, 3.13**

    For any set of raw rows, every output price record keeps its raw item name, unit "kg", source
    name, snapshot index, dates and Quality_Status. The ingest report satisfies
    rows_in = rows_out + duplicates, and the per-status counts sum to rows_out.
    """
    cleaned, report = clean(rows, SETTINGS)
    assert report.rows_in == len(rows)
    assert report.rows_out == len(cleaned)
    assert report.rows_in == report.rows_out + report.duplicates
    assert sum(report.by_status.values()) == report.rows_out
    for status, n in report.by_status.items():
        assert n == sum(1 for r in cleaned if r.quality_status == status)

    # Every clean row traces back to an input row with the same raw item name and snapshot.
    for r in cleaned:
        assert any(str(raw.item) == r.raw_item_name and raw.snapshot == r.snapshot for raw in rows)

    assignments, _, _ = map_species(cleaned, [], [])
    records = to_price_records(assignments, source_name=source)
    representable = [(r, sid) for r, sid in assignments if is_representable(r)]
    assert len(records) == len(representable) == report.rows_out - report.unrepresentable
    for rec, (row, sid) in zip(records, representable, strict=True):
        assert rec.raw_item_name == row.raw_item_name
        assert rec.unit == "kg"
        assert rec.source_name == source
        assert rec.source_snapshot == row.snapshot
        assert (rec.date_start, rec.date_end) == (row.date_start, row.date_end)
        assert rec.quality_status == row.quality_status
        assert rec.species_id == sid
        assert rec.grade == row.grade
    # Rows left out are exactly those the price_records constraints cannot hold.
    for row, _ in assignments:
        assert is_representable(row) == (row.quality_status not in {"invalid_date", "invalid_price", "invalid_count"})


# --- Property 11 ---------------------------------------------------------------------------------

ascii_word = st.text(alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=2, max_size=7)
ascii_name = st.lists(ascii_word, min_size=1, max_size=3).map(" ".join)


@st.composite
def catalogs(draw: st.DrawFn) -> tuple[list[Species], list[SpeciesAlias], dict[str, str]]:
    """Species with unique codes and aliases whose normalised terms are unique across the catalog."""
    names = draw(st.lists(ascii_name, min_size=0, max_size=4, unique_by=normalise_term))
    alias_names = draw(st.lists(ascii_name, max_size=8, unique_by=normalise_term))
    species = [
        Species(id=species_id(f"CAT_{i}"), code=f"CAT_{i}", canonical_name_ms=n, canonical_name_en=None,
                allergen_groups=(), active=True)
        for i, n in enumerate(names)
    ]
    aliases: list[SpeciesAlias] = []
    alias_owner: dict[str, str] = {}
    if species:
        for a in alias_names:
            owner = draw(st.sampled_from(species))
            aliases.append(SpeciesAlias(species_id=owner.id, alias=a, language=draw(st.sampled_from(["ms", "en", "local", "raw"]))))
            alias_owner[normalise_term(a)] = owner.id
    return species, aliases, alias_owner


def _variant(draw: st.DrawFn, name: str) -> str:
    """Case and whitespace variant of a name (same normalised term)."""
    text = draw(st.sampled_from([name, name.lower(), name.title()]))
    return draw(whitespace) + text.replace(" ", draw(st.sampled_from([" ", "  ", "\t"]))) + draw(whitespace)


@st.composite
def mapping_inputs(draw: st.DrawFn) -> tuple[list[Species], list[SpeciesAlias], dict[str, str], list[CleanRow]]:
    species, aliases, alias_owner = draw(catalogs())
    known_terms = set(alias_owner) | {normalise_term(sp.canonical_name_ms) for sp in species}
    fresh = draw(st.lists(ascii_name.filter(lambda n: normalise_term(n) not in known_terms), max_size=4))
    pool = [a.alias for a in aliases] + fresh
    rows: list[CleanRow] = []
    if pool:
        for _ in range(draw(st.integers(0, 12))):
            raw = _variant(draw, draw(st.sampled_from(pool)))
            grade = draw(st.sampled_from([None, "BESAR", "ANTARA 2 HINGGA 5 EKOR SEKILO"]))
            raw_item = f"{raw} ({grade})" if grade else raw
            name, parsed_grade = parse_item(raw_item)
            rows.append(
                CleanRow(species_name=name, grade=parsed_grade, premise=PREMISE, raw_item_name=raw_item,
                         price_rm_per_kg=10.0, price_low=None, price_high=None, price_min=None, price_max=None,
                         record_count=1, date_start=date(2026, 10, 19), date_end=date(2026, 10, 26), snapshot=0,
                         quality_status="ok")
            )
    return species, aliases, alias_owner, rows


# Feature: hargaikan-v2, Property 11: Species mapping is deterministic
@given(inputs=mapping_inputs(), data=st.data())
def test_species_mapping_is_deterministic(
    inputs: tuple[list[Species], list[SpeciesAlias], dict[str, str], list[CleanRow]], data: st.DataObject
) -> None:
    """**Validates: Requirements 4.1, 4.2, 4.3**

    For any catalog and parsed names, a name that equals an alias after normalisation maps to that
    alias's species. Each distinct unmatched normalised name creates exactly one new species with a
    raw alias and appears in the report. Mapping the same input twice yields identical codes, ids
    and assignments.
    """
    species, aliases, alias_owner, rows = inputs
    assignments, new_species, new_aliases = map_species(rows, species, aliases)
    assert [r for r, _ in assignments] == rows

    # Alias matches map to the alias's species.
    for row, sid in assignments:
        term = normalise_term(row.species_name)
        if term in alias_owner:
            assert sid == alias_owner[term]

    # Each distinct unmatched normalised name → exactly one new active species with a raw alias.
    unmatched_terms = {normalise_term(r.species_name) for r in rows if normalise_term(r.species_name) not in alias_owner}
    assert len(new_species) == len(unmatched_terms)
    by_term = {normalise_term(sp.canonical_name_ms): sp for sp in new_species}
    assert set(by_term) == unmatched_terms
    for sp in new_species:
        assert sp.active
        assert sp.id == species_id(sp.code)
        assert sp.code.startswith(species_code(sp.canonical_name_ms))
        sp_aliases = [a for a in new_aliases if a.species_id == sp.id]
        assert sp_aliases and all(a.language == "raw" for a in sp_aliases)
        raw_names = {r.raw_item_name for r, sid in assignments if sid == sp.id}
        assert {a.alias for a in sp_aliases} <= raw_names
    for row, sid in assignments:
        term = normalise_term(row.species_name)
        if term in unmatched_terms:
            assert sid == by_term[term].id
    all_codes = [sp.code for sp in species] + [sp.code for sp in new_species]
    assert len(all_codes) == len(set(all_codes))

    report = with_unmatched(IngestReport(0, 0, 0, 0, {}, (), ()), new_species)
    assert set(report.unmatched_species) == {sp.canonical_name_ms for sp in new_species}

    # Same input twice → identical codes, ids and assignments; row order does not matter either.
    assert map_species(rows, species, aliases) == (assignments, new_species, new_aliases)
    order = data.draw(st.permutations(range(len(rows))))
    shuffled = [rows[i] for i in order]
    s_assign, s_species, s_aliases = map_species(shuffled, species, aliases)
    assert (s_species, s_aliases) == (new_species, new_aliases)
    assert [sid for _, sid in s_assign] == [assignments[i][1] for i in order]
