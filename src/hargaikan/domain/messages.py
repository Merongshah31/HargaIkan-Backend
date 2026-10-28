"""Every Bahasa Melayu user-facing string. Pages and services import from here.

Pure data only: no supabase, pinecone, requests or streamlit imports and no I/O.
"""

# --- Fixed strings given by the SRS (exact text) ---------------------------------------------
VERDICT_BELOW = "Di bawah julat sejarah"
VERDICT_WITHIN = "Dalam julat sejarah"
VERDICT_ABOVE = "Di atas julat sejarah"
NO_DATA = "Data belum tersedia"
FALLBACK_TEMPLATE = "Data khusus {requested} belum tersedia. Rujukan {used} digunakan."
#   → "Data khusus Pasar Chow Kit belum tersedia. Rujukan Kuala Lumpur digunakan."
NO_RECOMMENDATION = "Tiada cadangan"
RECOMMENDATION_DISCLAIMER = (
    "Cadangan ini bukan nasihat perubatan. Jika anda mempunyai alergi makanan, "
    "semak nama spesies, label produk dan risiko pencemaran silang dengan penjual atau profesional kesihatan."
)
TRAVEL_NOT_ESTIMATED = "Kos perjalanan belum dianggarkan"
DEMO_DATA = "Data demo"

# --- Strings proposed by the design ----------------------------------------------------------
INVALID_PRICE = "Sila masukkan harga yang sah melebihi RM0."
SEARCH_EMPTY = "Sila taip nama ikan."
SEARCH_NOT_FOUND = "Ikan tidak ditemui. Cuba nama lain."
SEARCH_CONFIRM = "Adakah ini ikan yang anda maksudkan?"
SEARCH_FALLBACK_NOTICE = "Carian ringkas digunakan buat sementara waktu."
ORIGINAL_NO_DATA = "Data harga ikan asal belum tersedia."
OVER_BUDGET = "Melebihi bajet"
ALLERGY_CAUTION = "Ikan ini sepadan dengan alergi dalam profil anda. Sila berhati-hati."
FORECAST_UNAVAILABLE = "Ramalan belum tersedia."
FORECAST_MAY_EXCEED_P75 = "Julat anggaran menunjukkan harga mungkin melebihi P75 sejarah dalam tempoh ini."
EARLY_BUY = (
    "Harga semasa di bawah P25 sejarah dan julat anggaran (P10–P90: RM{p10}–RM{p90}/kg) menunjukkan "
    "harga mungkin naik. Anggaran ini tidak pasti dan bukan arahan untuk membeli. Jika membeli lebih awal, "
    "simpan ikan di dalam peti sejuk (0–4°C) atau peti sejuk beku dan masak dengan sempurna."
)
BUDGET_INVALID = "Bajet mesti nombor antara RM0.01 dan RM{max}/kg."
CONSENT_REQUIRED_ALLERGY = "Sila beri persetujuan sebelum menyimpan maklumat alergi."
CONSENT_REQUIRED_LOCATION = "Sila beri persetujuan sebelum menyimpan lokasi pilihan."
SESSION_NOTICE = "Profil anda disimpan untuk sesi pelayar ini."
EMPTY_PROFILE = "Lengkapkan profil anda untuk melihat papan pemuka peribadi."
SECTION_UNAVAILABLE = "Bahagian ini tidak dapat dimuatkan buat masa ini."
PROFILE_UNAVAILABLE = "Profil tidak dapat dimuatkan buat masa ini. Harga rujukan masih boleh dilihat."
NOT_COMPUTED = "Belum dikira"
DELETE_ALL = "Padam semua data saya"

# Absolute-safety and seller-honesty phrases that no generated text may contain.
FORBIDDEN_PHRASES = (
    "100% selamat",
    "selamat sepenuhnya",
    "pasti selamat",
    "dijamin selamat",
    "tidak jujur",
    "penipu",
    "menipu",
    "tidak amanah",
)

# --- App shell: titles, intro and the Source_Label legend -------------------------------------
APP_TITLE = "HargaIkan"
APP_INTRO = (
    "HargaIkan membantu anda menyemak harga rujukan ikan di Kuala Lumpur. Bandingkan harga penjual "
    "dengan julat harga sejarah, lihat cadangan ikan serupa yang lebih murah dan anggarkan kos "
    "perjalanan daripada maklumat anda sendiri."
)
APP_INTRO_SOURCES = "Setiap nombor dipaparkan bersama label sumbernya."
LEGEND_TITLE = "Petunjuk label sumber"

PAGE_TITLE_DASHBOARD = "Papan Pemuka"
PAGE_TITLE_CARI_IKAN = "Cari Ikan"
PAGE_TITLE_PROFIL = "Profil Saya"
PAGE_TITLE_RAMALAN = "Ramalan Harga"
PAGE_TITLE_PERJALANAN = "Kos Perjalanan"

# Icons keyed by Source_Label text, so badges never rely on colour alone.
SOURCE_ICONS = {
    "Harga sejarah": "📊",
    "Input pengguna": "✍️",
    "Anggaran model": "📈",
    "Anggaran perjalanan": "🚗",
}
DEMO_ICON = "🧪"
SECTION_UNAVAILABLE_ICON = "⚠️"

# Legend descriptions keyed by Source_Label text (shown on the home page).
SOURCE_DESCRIPTIONS = {
    "Harga sejarah": "Dikira daripada rekod harga sebenar yang telah diterbitkan.",
    "Input pengguna": "Nilai yang anda masukkan sendiri.",
    "Anggaran model": "Anggaran daripada model ramalan yang telah diuji. Anggaran ini tidak pasti.",
    "Anggaran perjalanan": "Kos perjalanan yang dikira hanya daripada maklumat yang anda masukkan.",
}

# --- Recommender: profile differences (core/recommender.py) -----------------------------------
PROFILE_INCOMPLETE = "Profil ikan belum lengkap"
PROFILE_DIFF_TEMPLATE = "{field}: {a} → {b}"
PROFILE_FIELD_LABELS = {
    "texture": "Tekstur",
    "taste": "Rasa",
    "bone_level": "Tulang",
    "cooking_methods": "Cara masakan",
    "allergen_groups": "Kumpulan alergen",
}
PROFILE_EMPTY_VALUE = "tiada"

# --- Profile validation (core/profile_rules.py) -----------------------------------------------
UNKNOWN_SPECIES = "Ikan yang dipilih tidak dikenali. Sila pilih daripada senarai."
UNKNOWN_LOCATION = "Lokasi yang dipilih tidak dikenali. Sila pilih daripada senarai."

# --- FastAPI backend (services/*, api/*) ------------------------------------------------------
AUTH_REQUIRED = "Sesi tidak sah. Sila muat semula halaman."
INVALID_REQUEST = "Permintaan tidak sah. Sila semak maklumat yang dimasukkan."
RESOURCE_NOT_FOUND = "Maklumat yang diminta tidak ditemui."
WITHIN_BUDGET = "Dalam bajet"
PROFILE_SAVED = "Profil anda telah disimpan."
DATA_DELETED = "Semua data anda telah dipadam."
CONSENT_WITHDRAWN_ALLERGIES = "Persetujuan alergi ditarik balik. Maklumat alergi anda telah dipadam."
CONSENT_WITHDRAWN_LOCATION = "Persetujuan lokasi ditarik balik. Lokasi pilihan anda telah dipadam."
DASHBOARD_DEFAULT_LOCATION = "Tiada lokasi pilihan disimpan. Rujukan Kuala Lumpur digunakan."
SUGGESTIONS_TITLE = "Ikan dengan rekod harga terbanyak di Kuala Lumpur"

# Source of a transit fare taken from the maintained fare table (data/curated/transit_fares.yaml).
FARE_TABLE_LABEL = "Jadual tambang"
TRAVEL_COMPUTED_LABEL = "Anggaran perjalanan · Input pengguna"
TRAVEL_COMPUTED_FARE_TABLE_LABEL = "Anggaran perjalanan · Jadual tambang"
TRAVEL_SEPARATE_NOTE = "Kos perjalanan dipaparkan berasingan daripada kos ikan."
TRAVEL_NOT_IN_FORECAST = "Kos perjalanan tidak digunakan dalam ramalan harga."
TRAVEL_FIELD_NOT_NUMBER = "{field} mesti nombor yang sah."
TRAVEL_FIELD_NEGATIVE = "{field} tidak boleh negatif."
TRAVEL_FIELD_NOT_POSITIVE = "{field} mesti melebihi 0."
TRAVEL_FIELD_LABELS = {
    "distance_km": "Jarak sehala (km)",
    "fuel_efficiency_km_per_l": "Kecekapan bahan api (km/l)",
    "fuel_price_rm_per_l": "Harga bahan api (RM/l)",
    "fuel_litres": "Bahan api digunakan (l)",
    "fuel_cost": "Kos bahan api",
    "toll_rm": "Tol",
    "parking_rm": "Letak kereta",
    "transit_fare_rm": "Tambang",
    "travel_total": "Jumlah kos perjalanan",
    "fish_price_rm_per_kg": "Harga ikan (RM/kg)",
    "qty_kg": "Kuantiti (kg)",
    "fish_cost": "Kos ikan",
    "grand_total": "Jumlah keseluruhan",
    "effective_rm_per_kg": "Kos sebenar per kg",
}
FARE_TABLE_INVALID = "Jadual tambang tidak dapat digunakan buat masa ini. Sila masukkan tambang sendiri."

# Deferred / not yet connected integrations (services/deferred.py, GET /capabilities).
VECTOR_SEARCH_NOT_CONNECTED = "Carian semantik belum disambungkan. Carian nama ringkas digunakan."
WEATHER_NOT_CONNECTED = "Data cuaca (MET Malaysia) belum disambungkan."
ROUTING_NOT_CONNECTED = "Anggaran jarak laluan (OpenStreetMap) belum disambungkan. Sila masukkan jarak sendiri."
TRANSIT_API_NOT_CONNECTED = "Tambang pengangkutan awam masa nyata belum disambungkan."
SEASONAL_NOT_CONNECTED = "Kalendar musim dan perayaan belum disambungkan."
FORECAST_PUBLISH_NOTE = "Ramalan hanya dipaparkan selepas model diterbitkan dan mengatasi garis dasar."
