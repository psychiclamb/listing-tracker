from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional

import streamlit as st

def get_conn():
    return st.connection("postgresql", type="sql", url=st.secrets["DB_URL"])


DATA_FILE = Path("progress.json")

# ---- Sütunlar (varyantlar) ----
VARIANTS: List[Tuple[str, str]] = [
    ("dikey", "Dikey"),
    ("yatay", "Yatay"),
    ("kare", "Kare"),
    ("ince_dikey", "İnce dikey"),
    ("ince_yatay", "İnce yatay"),
    ("eksik_dikey", "Eksik dikey"),
    ("eksik_yatay", "Eksik yatay"),
    ("eksik_kare", "Eksik kare"),
    ("eksik_ince_dikey", "Eksik ince dikey"),
    ("eksik_ince_yatay", "Eksik ince yatay"),
]

# ---- Her sütunda olacak checkbox’lar ----
COLUMN_STEPS: List[Tuple[str, str]] = [
    ("eserlerin_editlendi", "Eserlerin editlendi"),
    ("kalite_artirildi", "Kalite - artırıldı"),
    ("urun_aciklamalari_olusturuldu", "Ürün açıklamaları oluşturuldu"),
    ("mockuplar_videolar_olusturuldu", "Mockuplar ve videolar oluşturuldu"),
    ("printify_yuklendi", "Printify'a - yüklendi"),
    ("etsy_yuklendi", "Etsy'e yüklendi"),
]

# ---- Sanatçıya özel (tek seferlik) ----
GLOBAL_STEPS: List[Tuple[str, str]] = [
    ("research_tamamlandi", "Sanatçının satan ve popüler eserlerinin araştırılması"),
    ("eksikler_belirlendi", "Kaynaklarımız içerisinde bulunmayan popüler eserlerin tespit edilmesi"),
    ("eksikler_tamamlandi", "Eksik olduğu tespit edilen popüler eserlerin temin edilmesi"),
]


# ---------------- utils ----------------
def force_rerun() -> None:
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def toast(msg: str) -> None:
    if hasattr(st, "toast"):
        st.toast(msg)
    else:
        st.success(msg)


def norm(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def checkbox_key(artist_id: str, variant_key: Optional[str], step_key: str) -> str:
    if variant_key is None:
        return f"{artist_id}__global__{step_key}"
    return f"{artist_id}__{variant_key}__{step_key}"


def empty_variant_steps() -> Dict[str, bool]:
    return {k: False for k, _ in COLUMN_STEPS}


def empty_global_steps() -> Dict[str, bool]:
    return {k: False for k, _ in GLOBAL_STEPS}


def ensure_checkbox_state(key: str, default_val: bool) -> None:
    if key not in st.session_state:
        st.session_state[key] = default_val


def set_artist_all_session_state(artist_id: str, value: bool) -> None:
    for gk, _ in GLOBAL_STEPS:
        st.session_state[checkbox_key(artist_id, None, gk)] = value
    for vk, _ in VARIANTS:
        for sk, _ in COLUMN_STEPS:
            st.session_state[checkbox_key(artist_id, vk, sk)] = value


def bump_sort_key() -> None:
    """✅ Sortables component'ini zorla yeniden mount etmek için key versiyonunu artır."""
    st.session_state["artist_sort_key_v"] = int(st.session_state.get("artist_sort_key_v", 0)) + 1


# ---------------- data model ----------------
@dataclass
class ArtistProgress:
    id: str
    label: str
    order: int
    global_steps: Dict[str, bool]
    variants: Dict[str, Dict[str, bool]]

    @staticmethod
    def new(label: str, order: int) -> "ArtistProgress":
        artist_id = uuid.uuid4().hex
        variants = {vk: empty_variant_steps() for vk, _ in VARIANTS}
        return ArtistProgress(
            id=artist_id,
            label=label.strip(),
            order=order,
            global_steps=empty_global_steps(),
            variants=variants,
        )


def load_data() -> Dict[str, ArtistProgress]:
    if not DATA_FILE.exists():
        return {}

    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    data: Dict[str, ArtistProgress] = {}

    for key, v in raw.items():
        if not isinstance(v, dict):
            continue

        artist_id = str(v.get("id", "")).strip() or str(key).strip()
        label = str(v.get("label", "")).strip() or artist_id
        order = int(v.get("order", 10**9))

        global_in = dict(v.get("global_steps", {}))
        global_steps = empty_global_steps()
        for gk, _ in GLOBAL_STEPS:
            global_steps[gk] = bool(global_in.get(gk, False))

        variants_in = dict(v.get("variants", {}))
        variants: Dict[str, Dict[str, bool]] = {}
        for vk, _ in VARIANTS:
            steps_in = dict(variants_in.get(vk, {}))
            steps = empty_variant_steps()
            for sk, _ in COLUMN_STEPS:
                steps[sk] = bool(steps_in.get(sk, False))
            variants[vk] = steps

        data[artist_id] = ArtistProgress(
            id=artist_id,
            label=label,
            order=order,
            global_steps=global_steps,
            variants=variants,
        )

    save_data(data)  # normalize
    return data


def save_data(data: Dict[str, ArtistProgress]) -> None:
    raw = {artist_id: asdict(ap) for artist_id, ap in data.items()}
    DATA_FILE.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    toast(f"DB'ye kaydedildi ✅ ({len(data)} sanatçı)")



def calc_done_total(ap: ArtistProgress) -> Tuple[int, int]:
    done = 0
    total = 0
    for gk, _ in GLOBAL_STEPS:
        total += 1
        if ap.global_steps.get(gk, False):
            done += 1
    for vk, _ in VARIANTS:
        for sk, _ in COLUMN_STEPS:
            total += 1
            if ap.variants.get(vk, {}).get(sk, False):
                done += 1
    return done, total


# ---------------- reorder (drag&drop) ----------------
SORTABLES_OK = False
sort_items = None
try:
    from streamlit_sortables import sort_items as _sort_items  # pip install streamlit-sortables
    sort_items = _sort_items
    SORTABLES_OK = True
except Exception:
    SORTABLES_OK = False


def apply_order_from_id_list(data: Dict[str, ArtistProgress], ordered_ids: List[str]) -> bool:
    seen = set()
    new_list = []
    for i in ordered_ids:
        if i in data and i not in seen:
            seen.add(i)
            new_list.append(i)
    for i in data.keys():
        if i not in seen:
            new_list.append(i)

    changed = False
    for idx, artist_id in enumerate(new_list, start=1):
        if data[artist_id].order != idx:
            data[artist_id].order = idx
            changed = True

    if changed:
        save_data(data)
    return changed


# ---------------- UI ----------------
st.set_page_config(page_title="Listing Upload Süreç Takibi", layout="wide")
st.title("📦 Listing Upload Süreç Takibi")

# key version init
if "artist_sort_key_v" not in st.session_state:
    st.session_state["artist_sort_key_v"] = 0

data = load_data()
with st.sidebar.expander("DB Debug", expanded=True):
    conn = st.connection("get_conn()")
    st.write("Ping:", conn.query("select 1 as ok", ttl=0))
    st.write("Count:", conn.query("select count(*) as c from artist_progress", ttl=0))
    st.write("Rows:", conn.query("select id, label, order_num from artist_progress order by order_num asc limit 20", ttl=0))


with st.sidebar:
    st.header("➕ Sanatçı ekle")

    with st.form("add_artist_form", clear_on_submit=True):
        new_name = st.text_input("Sanatçı adı", placeholder="Örn: Claude Monet")
        submitted = st.form_submit_button("Ekle", use_container_width=True)

    if submitted:
        name = (new_name or "").strip()
        if not name:
            st.warning("İsim boş olamaz.")
        else:
            if any(norm(ap.label) == norm(name) for ap in data.values()):
                st.warning("Bu sanatçı zaten listede var.")
            else:
                max_order = max((ap.order for ap in data.values()), default=0)
                ap = ArtistProgress.new(label=name, order=max_order + 1)
                data[ap.id] = ap
                save_data(data)
                

                # ✅ dragdrop listesi anında yeni elemanı görsün
                bump_sort_key()

                toast("Eklendi ✅")
                force_rerun()

    st.divider()
    st.header("↕️ Sıralama")

    if not data:
        st.info("Liste boş. Önce sanatçı ekle.")
    else:
        ordered = sorted(data.values(), key=lambda a: a.order)
        ordered_ids = [a.id for a in ordered]

        if SORTABLES_OK:
            st.caption("Sürükle-bırak ile sırala:")

            # ✅ KEY VERSIONING: her değişimde component yeniden mount olur
            sort_key = f"artist_sort_{st.session_state['artist_sort_key_v']}"

            # Ekranda label göster, id mapping yap
            display = [f"{a.label}  ⟦{a.id[:8]}⟧" for a in ordered]
            display_to_id = {f"{a.label}  ⟦{a.id[:8]}⟧": a.id for a in ordered}

            try:
                new_display = sort_items(display, direction="vertical", key=sort_key)
                new_ids = [display_to_id[x] for x in new_display if x in display_to_id]

                if new_ids and new_ids != ordered_ids:
                    changed = apply_order_from_id_list(data, new_ids)
                    if changed:
                        toast("Sıra güncellendi ✅")
                        # ✅ ana liste de anında güncellensin + component state temizlensin
                        bump_sort_key()
                        force_rerun()

            except Exception:
                st.warning("Drag&drop çalışmadı. Aşağıdaki ↑ ↓ ile sırala.")
                SORTABLES_OK = False

        if not SORTABLES_OK:
            st.caption("↑ ↓ ile sırala (drag&drop için: pip install streamlit-sortables)")
            for i, ap in enumerate(ordered):
                c1, c2, c3 = st.columns([6, 1, 1])
                with c1:
                    st.write(ap.label)
                with c2:
                    if st.button("↑", key=f"up_{ap.id}", disabled=(i == 0)):
                        above = ordered[i - 1]
                        ap.order, above.order = above.order, ap.order
                        save_data(data)
                        toast("Sıra güncellendi ✅")
                        force_rerun()
                with c3:
                    if st.button("↓", key=f"down_{ap.id}", disabled=(i == len(ordered) - 1)):
                        below = ordered[i + 1]
                        ap.order, below.order = below.order, ap.order
                        save_data(data)
                        toast("Sıra güncellendi ✅")
                        force_rerun()

    st.divider()
    st.header("🔎 Filtre / Sıralama")
    q = st.text_input("Ara", placeholder="monet", key="search_q")
    filter_mode = st.selectbox(
        "Göster",
        ["Hepsi", "Sadece tamamlanmamışlar", "Sadece tamamlanmışlar"],
        index=0,
        key="filter_mode",
    )
    sort_mode = st.selectbox(
        "Liste görünümü sırası",
        ["Liste sırası", "Başlık (A→Z)", "İlerleme (çok→az)"],
        index=0,
        key="sort_mode",
    )

    st.divider()
    if st.button("🧨 Her şeyi sıfırla (progress.json sil)", use_container_width=True, key="btn_reset_all"):
        if DATA_FILE.exists():
            DATA_FILE.unlink()
        st.success("Sıfırlandı. Sayfayı yenile.")
        st.stop()

# Main list
artists = list(data.values())

if q.strip():
    qq = q.strip().lower()
    artists = [a for a in artists if qq in a.label.lower()]

if filter_mode != "Hepsi":
    if filter_mode == "Sadece tamamlanmamışlar":
        artists = [a for a in artists if calc_done_total(a)[0] < calc_done_total(a)[1]]
    else:
        artists = [a for a in artists if calc_done_total(a)[0] == calc_done_total(a)[1]]

if sort_mode == "Liste sırası":
    artists.sort(key=lambda a: a.order)
elif sort_mode == "Başlık (A→Z)":
    artists.sort(key=lambda a: a.label.lower())
else:
    artists.sort(key=lambda a: calc_done_total(a)[0] / max(1, calc_done_total(a)[1]), reverse=True)

# Genel ilerleme
overall_done = 0
overall_total = 0
for a in artists:
    d, t = calc_done_total(a)
    overall_done += d
    overall_total += t

st.progress(0 if overall_total == 0 else overall_done / overall_total)
st.caption(f"Genel ilerleme: {overall_done}/{overall_total} adım tamamlandı")
st.markdown("---")

if not artists:
    st.info("Liste boş. Soldan sanatçı ekleyebilirsin.")
    st.stop()

# Artist cards
for ap in artists:
    done, total = calc_done_total(ap)
    pct = 0 if total == 0 else done / total
    artist_id = ap.id

    with st.container(border=True):
        top_l, top_m, top_r = st.columns([3, 2, 2])

        with top_l:
            st.subheader(ap.label)

        with top_m:
            st.progress(pct)
            st.caption(f"{int(pct*100)}% ({done}/{total})")

        with top_r:
            b1, b2, b3, b4 = st.columns([1, 1, 1, 1])

            with b1:
                if st.button("Hepsi ✅", key=f"btn_all_{artist_id}"):
                    ap.global_steps = {k: True for k, _ in GLOBAL_STEPS}
                    for vk, _ in VARIANTS:
                        ap.variants[vk] = {sk: True for sk, _ in COLUMN_STEPS}
                    data[artist_id] = ap
                    save_data(data)
                    set_artist_all_session_state(artist_id, True)
                    force_rerun()

            with b2:
                if st.button("Hepsi ⬜", key=f"btn_none_{artist_id}"):
                    ap.global_steps = {k: False for k, _ in GLOBAL_STEPS}
                    for vk, _ in VARIANTS:
                        ap.variants[vk] = {sk: False for sk, _ in COLUMN_STEPS}
                    data[artist_id] = ap
                    save_data(data)
                    set_artist_all_session_state(artist_id, False)
                    force_rerun()

            with b3:
                if st.button("Sıfırla", key=f"btn_reset_{artist_id}"):
                    ap.global_steps = {k: False for k, _ in GLOBAL_STEPS}
                    for vk, _ in VARIANTS:
                        ap.variants[vk] = {sk: False for sk, _ in COLUMN_STEPS}
                    data[artist_id] = ap
                    save_data(data)
                    set_artist_all_session_state(artist_id, False)
                    force_rerun()

            with b4:
                del_flag = st.session_state.get(f"del_confirm_{artist_id}", False)
                if not del_flag:
                    if st.button("🗑", key=f"btn_del_{artist_id}"):
                        st.session_state[f"del_confirm_{artist_id}"] = True
                        force_rerun()
                else:
                    if st.button("Onayla", key=f"btn_del_ok_{artist_id}"):
                        # checkbox state cleanup
                        for gk, _ in GLOBAL_STEPS:
                            st.session_state.pop(checkbox_key(artist_id, None, gk), None)
                        for vk, _ in VARIANTS:
                            for sk, _ in COLUMN_STEPS:
                                st.session_state.pop(checkbox_key(artist_id, vk, sk), None)

                        # delete
                        data.pop(artist_id, None)
                        save_data(data)

                        # ✅ dragdrop listesi anında güncellensin
                        bump_sort_key()

                        st.session_state.pop(f"del_confirm_{artist_id}", None)
                        toast("Silindi 🗑️")
                        force_rerun()

                    if st.button("Vazgeç", key=f"btn_del_cancel_{artist_id}"):
                        st.session_state.pop(f"del_confirm_{artist_id}", None)
                        force_rerun()

        # ---- Global ----
        st.markdown("**Genel (sanatçı için tek seferlik):**")
        gcols = st.columns(3)
        changed = False

        for i, (gk, glabel) in enumerate(GLOBAL_STEPS):
            with gcols[i % 3]:
                k = checkbox_key(artist_id, None, gk)
                ensure_checkbox_state(k, ap.global_steps.get(gk, False))
                nv = st.checkbox(glabel, key=k)
                if nv != ap.global_steps.get(gk, False):
                    ap.global_steps[gk] = nv
                    changed = True

        st.markdown("---")

        # ---- Variants ----
        st.markdown("**Varyantlar:**")
        vcols = st.columns(len(VARIANTS))

        for idx, (vk, vlabel) in enumerate(VARIANTS):
            with vcols[idx]:
                st.markdown(f"### {vlabel}")
                for sk, slabel in COLUMN_STEPS:
                    k = checkbox_key(artist_id, vk, sk)
                    ensure_checkbox_state(k, ap.variants.get(vk, {}).get(sk, False))
                    nv = st.checkbox(slabel, key=k)
                    if nv != ap.variants[vk].get(sk, False):
                        ap.variants[vk][sk] = nv
                        changed = True

        if changed:
            data[artist_id] = ap
            save_data(data)
