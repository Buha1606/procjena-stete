import streamlit as st
import pandas as pd
import plotly.express as px
import matplotlib.pyplot as plt
import os
import io
import urllib.request
import datetime

import gspread
from google.oauth2.service_account import Credentials

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ---------------------------------------------------------
# Подешавање фонта са подршком за ћирилицу у ReportLab-у
# ---------------------------------------------------------
def get_cyrillic_font():
    """
    Региструје и враћа назив фонта који подржава ћирилично писмо у ReportLab-у.
    Прво покушава системске фонтове (Windows/Linux), а затим преузима DejaVuSans као fallback.
    """
    font_name = "CyrillicFont"
    bold_font_name = "CyrillicFont-Bold"

    try:
        pdfmetrics.getFont(font_name)
        return font_name, bold_font_name
    except KeyError:
        pass

    candidate_fonts = [
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("C:/Windows/Fonts/calibri.ttf", "C:/Windows/Fonts/calibrib.ttf"),
        ("C:/Windows/Fonts/times.ttf", "C:/Windows/Fonts/timesbd.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")
    ]

    for reg_path, bold_path in candidate_fonts:
        if os.path.exists(reg_path) and os.path.exists(bold_path):
            pdfmetrics.registerFont(TTFont(font_name, reg_path))
            pdfmetrics.registerFont(TTFont(bold_font_name, bold_path))
            return font_name, bold_font_name

    # Fallback: Преузимање DejaVuSans фонтова уколико нису пронађени у систему
    font_dir = os.path.join(os.path.dirname(__file__), ".fonts")
    os.makedirs(font_dir, exist_ok=True)
    reg_path = os.path.join(font_dir, "DejaVuSans.ttf")
    bold_path = os.path.join(font_dir, "DejaVuSans-Bold.ttf")

    if not os.path.exists(reg_path):
        url = "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans.ttf"
        urllib.request.urlretrieve(url, reg_path)
    if not os.path.exists(bold_path):
        url = "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans-Bold.ttf"
        urllib.request.urlretrieve(url, bold_path)

    pdfmetrics.registerFont(TTFont(font_name, reg_path))
    pdfmetrics.registerFont(TTFont(bold_font_name, bold_path))
    return font_name, bold_font_name

# ---------------------------------------------------------
# Конфигурација Streamlit странице
# ---------------------------------------------------------
st.set_page_config(
    page_title="Обрачун штете од града (Невесиње)",
    page_icon="🌾",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ---------------------------------------------------------
# Google Sheets Подешавања
# ---------------------------------------------------------
SPREADSHEET_NAME = "Evidencija Steta"

def get_gspread_sheet(show_error=True):
    """
    Покушава аутентификацију на Google Sheets користећи st.secrets, 
    директне секрет фајлове или променљиве окружења за Render / Cloud деплојмент.
    Враћа (sheet, error_message).
    """
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    creds_dict = None
    
    # 1. Сигурно учитавање из st.secrets (хвата све типове изузетака укључујући StreamlitSecretNotFoundError)
    try:
        sec = getattr(st, "secrets", None)
        if sec is not None:
            try:
                if "gcp_service_account" in sec:
                    creds_dict = dict(sec["gcp_service_account"])
                elif "type" in sec and sec.get("type") == "service_account":
                    creds_dict = dict(sec)
            except BaseException:
                pass
    except BaseException:
        pass

    # 2. Покушај читања secrets.toml са познатих путања на диску (за Render)
    if not creds_dict:
        possible_paths = [
            os.path.join(os.path.dirname(__file__), ".streamlit", "secrets.toml"),
            os.path.join(os.getcwd(), ".streamlit", "secrets.toml"),
            "/opt/render/project/src/.streamlit/secrets.toml",
            "/opt/render/.streamlit/secrets.toml"
        ]
        for p in possible_paths:
            if os.path.exists(p):
                try:
                    import toml
                    tdata = toml.load(p)
                    if "gcp_service_account" in tdata:
                        creds_dict = dict(tdata["gcp_service_account"])
                    elif "type" in tdata and tdata.get("type") == "service_account":
                        creds_dict = dict(tdata)
                    if creds_dict:
                        break
                except Exception:
                    pass

    # 3. Покушај из OS Environment Variables (Render Environment Variables)
    if not creds_dict:
        import json
        env_json = os.environ.get("GCP_SERVICE_ACCOUNT") or os.environ.get("GCP_SERVICE_ACCOUNT_JSON")
        if env_json:
            try:
                creds_dict = json.loads(env_json)
            except Exception:
                pass

    if not creds_dict:
        msg = "Nisu pronađeni Google Service Account kredencijali u secrets.toml нити у окружењу."
        if show_error:
            st.error(f"⚠️ {msg}")
        return None, msg

    try:
        if "private_key" in creds_dict and isinstance(creds_dict["private_key"], str):
            creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        client = gspread.authorize(credentials)
        
        # Отварање табеле и првог радног листа
        sheet = client.open(SPREADSHEET_NAME).sheet1
        return sheet, None
    except Exception as e:
        msg = f"Greška sa Google Sheets konekcijom: {e}"
        if show_error:
            st.error(f"❌ {msg}")
        return None, msg

def load_records_dataframe():
    """Учитава све сачуване уносе искључиво из Google Sheets табеле."""
    headers = ["Датум_и_Време", "Службеник", "Име_и_Презиме", "Насеље", "Коефицијент", "Основица_КМ", "Коначна_Штета_КМ", "Спецификација"]
    
    sheet, _ = get_gspread_sheet(show_error=False)
    if sheet:
        try:
            data = sheet.get_all_records()
            if data:
                df = pd.DataFrame(data)
                col_map = {
                    "Datum": "Датум_и_Време",
                    "Proizvodjac": "Име_и_Презиме",
                    "Naselje": "Насеље",
                    "UkupnaSteta": "Коначна_Штета_КМ",
                    "Sluzbenik": "Службеник",
                    "Osnovica": "Основица_КМ",
                    "Koeficijent": "Коефицијент",
                    "Specifikacija": "Спецификација"
                }
                df = df.rename(columns=col_map)
                for col in ["Основица_КМ", "Коначна_Штета_КМ"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
                return df
        except Exception:
            pass

    return pd.DataFrame(columns=headers)

def save_new_record(officer, name, settlement, coeff, osnova, steta, crops_spec):
    """Чува нови обрачун искључиво у Google Sheets."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    row = [
        timestamp,
        officer,
        name,
        settlement,
        f"{int(coeff*100)}%",
        float(osnova),
        round(float(steta), 2),
        crops_spec
    ]
    
    saved_gs = False
    sheet, err = get_gspread_sheet(show_error=True)
    if sheet:
        try:
            all_vals = sheet.get_all_values()
            if not all_vals:
                headers = ["Datum", "Proizvodjac", "Naselje", "UkupnaSteta", "Sluzbenik", "Osnovica_KM", "Koeficijent", "Specifikacija"]
                sheet.append_row(headers)
                sheet.append_row(row)
            else:
                first_row = all_vals[0]
                if len(first_row) == 5 and ("Datum" in first_row[0] or "Датум" in first_row[0]):
                    sheet.append_row([timestamp, name, settlement, round(float(steta), 2), officer])
                else:
                    sheet.append_row(row)
            
            saved_gs = True
            st.info("Podaci su uspješno zabilježeni u Google Sheets tabelu!")
        except Exception as e:
            st.error(f"Došlo je do greške prilikom upisa u red tabele: {e}")
    else:
        st.warning(f"⚠️ Podaci nisu upisani u Google Sheets: {err}")

    return saved_gs

# ---------------------------------------------------------
# Службеници и систем пријаве (Аутентификација)
# ---------------------------------------------------------
SLUZBENICI = {
    "Миљан Буха": ["mbnevesinje", "stetaodgradaobracunnevesinje", "mb2026"],
    "Милан Говедарица": ["mgnevesinje", "stetaodgradaobracunnevesinje", "mg2026"],
    "Вељо Радовановић": ["vrnevesinje", "stetaodgradaobracunnevesinje", "vr2026"],
    "Немања Ристић": ["nrnevesinje", "stetaodgradaobracunnevesinje", "nr2026"],
    "Којо Пикула": ["kpnevesinje", "stetaodgradaobracunnevesinje", "kp2026"]
}

def check_password():
    """Проверава и пријављује службеника са лозинком."""
    if st.session_state.get("authenticated", False):
        return True

    st.markdown("""
    <div style="max-width: 520px; margin: 40px auto 20px auto; padding: 30px; background: white; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1); border-top: 6px solid #1b4332; text-align: center;">
        <h2 style="color: #1b4332; margin-bottom: 8px;">🔒 Службена пријава</h2>
        <p style="color: #495057; font-size: 0.95rem; margin-bottom: 15px;">
            Општина Невесиње — Одјељење за пољопривреду и рурални развој.<br/>
            Молимо одаберите ваше име и унесите службену лозинку за приступ.
        </p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.6, 1])
    with col2:
        selected_officer = st.selectbox(
            "👤 Одаберите службеника:",
            list(SLUZBENICI.keys()),
            key="login_officer_select"
        )
        password_input = st.text_input("🔑 Унесите вашу лозинку:", type="password", key="pwd_input", placeholder="Лозинка...")

        if st.button("🔓 Пријави се у систем", type="primary", use_container_width=True):
            allowed_pwds = SLUZBENICI.get(selected_officer, [])
            if password_input in allowed_pwds or password_input == "stetaodgradaobracunnevesinje":
                st.session_state["authenticated"] = True
                st.session_state["officer_name"] = selected_officer
                st.success(f"✅ Успешна пријава! Добродошли, {selected_officer}.")
                st.rerun()
            else:
                st.error(f"❌ Неисправна лозинка за службеника {selected_officer}!")
    return False

if not check_password():
    st.stop()

# ---------------------------------------------------------
# Стилизовање главне апликације
# ---------------------------------------------------------
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #1b4332 0%, #2d6a4f 50%, #40916c 100%);
        padding: 24px;
        border-radius: 12px;
        color: white;
        margin-bottom: 20px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }
    .main-header h1 {
        color: #ffffff !important;
        margin-bottom: 5px;
        font-size: 2.2rem;
    }
    .main-header p {
        color: #d8f3dc !important;
        font-size: 1.05rem;
        margin-bottom: 0;
    }
    .stButton>button {
        border-radius: 8px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------
# Референтни подаци за насеља и културе
# ---------------------------------------------------------
НАСЕЉА = {
    "Бојишта": 0.80,
    "Лапчевине": 0.80,
    "Луг": 0.50,
    "Миљевац": 0.20,
    "Батковићи": 0.50,
    "Братач": 0.50,
    "Остало / Друго насеље": 1.00
}

# Заглавље апликације
st.markdown("""
<div class="main-header">
    <h1>🌾 Обрачун штете од града на пољопривредним културама</h1>
    <p>Методологија обрачуна штета на пољопривредним културама погођеним градом на територији општине Невесиње. За потребе Одјељења за пољопривреду и рурални развој Општине Невесиње.</p>
</div>
""", unsafe_allow_html=True)

# Бочна трака - Референтни нормативи и статус Google Sheets
with st.sidebar:
    officer_name = st.session_state.get("officer_name", "Службеник")
    st.header("🔑 Статус пријаве")
    st.success(f"🟢 Пријављени службеник:\n\n**{officer_name}**")
    
    # Статус Google Sheets везе
    gs_client, gs_err = get_gspread_sheet(show_error=False)
    if gs_client:
        st.info("☁️ Google Sheets: **Повезано (`Evidencija Steta`)**")
    else:
        st.caption(f"ℹ️ Google Sheets: Није повезано ({gs_err})")

    if st.button("🔒 Одјави се", type="secondary", use_container_width=True):
        st.session_state["authenticated"] = False
        st.session_state["officer_name"] = ""
        st.rerun()

    st.divider()
    st.header("🏔️ Референтни нормативи")
    st.markdown("""
    **Приноси и цијене култура:**
    
    🍎 **Воће & Винова лоза:**
    - **Јабука**: 30 – 50 кг/стаблу (1,20 КМ)
    - **Крушка**: 30 – 60 кг/стаблу (1,50 КМ)
    - **Шљива**: 25 – 45 кг/стаблу (1,00 КМ)
    - **Орах**: 30 – 50 кг/стаблу (25,00 КМ)
    - **Лоза**: м² (цијиена: 2,00 КМ/кг)
    - **Шипурак**: м² (цијиена: 4,00 КМ/кг)
    
    🥬 **Поврће & Плантаже:**
    - **Кромпир**: 4.000 КМ / дулуму
    - **Лук**: 400 кг/дулуму (3,00 КМ/кг)
    - **Купус**: 200 КМ / 100 м²
    
    🌾 **Житарице:**
    - **Жито**: по дулуму (0,70 КМ/кг)
    - **Силажни кукуруз**: по дулуму (0,12 КМ/кг)
    """)
    st.divider()
    st.markdown("""
    **Коефицијенти по насељима:**
    - **Бојишта**: 80% (0.80)
    - **Лапчевине**: 80% (0.80)
    - **Луг**: 50% (0.50)
    - **Миљевац**: 20% (0.20)
    - **Батковићи**: 50% (0.50)
    - **Братач**: 50% (0.50)
    - **Остало**: 100% (1.00)
    """)
    st.divider()
    st.caption("Општина Невесиње / Република Српска")

# Главни табуси за навигацију
tab1, tab2 = st.tabs(["📝 Обрачун и Унос Штете", "📊 Збирни Дашборд (База Уноса)"])

# ---------------------------------------------------------
# TAB 1: Обрачун и Унос Штете
# ---------------------------------------------------------
with tab1:
    col_left, col_right = st.columns([1, 1], gap="large")

    with col_left:
        st.subheader("1. Подаци о произвођачу и локацији")
        
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            име_презиме = st.text_input("Име и презиме произвођача", placeholder="нпр. Марко Марковић")
        with col_p2:
            насеље = st.selectbox("Насеље / Локација", list(НАСЕЉА.keys()))

        коефицијент = НАСЕЉА[насеље]
        проценат_текст = f"{int(коефицијент * 100)}%"
        
        st.info(f"📍 Насеље: **{насеље}** | Коефицијент штете: **{проценат_текст} ({коефицијент:.2f})** | Пријављени службеник: **{officer_name}**")

        st.subheader("2. Унос оштећених култура")

        # Воћке
        with st.expander("🍎 Воћке, Винова лоза и Шипурак", expanded=True):
            st.caption("Унесите податке за оне врсте воћа/култура које су претрпјеле штету. Предефинисане цијене се могу по потреби мијењати.")

            # Јабука
            st.markdown("**🍎 Јабука** *(референтни принос: 30–50 кг/стаблу)*")
            col_j1, col_j2, col_j3 = st.columns(3)
            with col_j1:
                st_jabuka = st.number_input("Број стабала", min_value=0, value=0, step=1, key="st_jab")
            with col_j2:
                pr_jabuka = st.number_input("Принос (кг/стаблу)", min_value=0.0, value=40.0, step=1.0, key="pr_jab")
            with col_j3:
                ci_jabuka = st.number_input("Цијена (КМ/кг)", min_value=0.0, value=1.20, step=0.10, key="ci_jab")

            # Крушка
            st.markdown("**🍐 Крушка** *(референтни принос: 30–60 кг/стаблу)*")
            col_k1, col_k2, col_k3 = st.columns(3)
            with col_k1:
                st_kruska = st.number_input("Број стабала", min_value=0, value=0, step=1, key="st_kru")
            with col_k2:
                pr_kruska = st.number_input("Принос (кг/стаблу)", min_value=0.0, value=45.0, step=1.0, key="pr_kru")
            with col_k3:
                ci_kruska = st.number_input("Цијена (КМ/кг)", min_value=0.0, value=1.50, step=0.10, key="ci_kru")

            # Шљива
            st.markdown("**🍑 Шљива** *(референтни принос: 25–45 кг/стаблу)*")
            col_s1, col_s2, col_s3 = st.columns(3)
            with col_s1:
                st_sljiva = st.number_input("Број стабала", min_value=0, value=0, step=1, key="st_slj")
            with col_s2:
                pr_sljiva = st.number_input("Принос (кг/стаблу)", min_value=0.0, value=35.0, step=1.0, key="pr_slj")
            with col_s3:
                ci_sljiva = st.number_input("Цијена (КМ/кг)", min_value=0.0, value=1.00, step=0.10, key="ci_slj")

            # Орах
            st.markdown("**🌰 Орах** *(референтни принос: 30–50 кг/стаблу | предефинисана цијена: 25,00 КМ/кг)*")
            col_o1, col_o2, col_o3 = st.columns(3)
            with col_o1:
                st_orah = st.number_input("Број стабала", min_value=0, value=0, step=1, key="st_ora")
            with col_o2:
                pr_orah = st.number_input("Принос (кг/стаблу)", min_value=0.0, value=40.0, step=1.0, key="pr_ora")
            with col_o3:
                ci_orah = st.number_input("Цијена (КМ/кг)", min_value=0.0, value=25.00, step=1.00, key="ci_ora")

            # Лоза
            st.markdown("**🍇 Лоза (Виноград)** *(обрачун у м² | предефинисана цијена: 2,00 КМ/кг)*")
            col_l1, col_l2, col_l3 = st.columns(3)
            with col_l1:
                povrsina_loza = st.number_input("Површина лозе (м²)", min_value=0.0, value=0.0, step=10.0, key="p_loza")
            with col_l2:
                pr_loza = st.number_input("Принос (кг/м²)", min_value=0.0, value=1.5, step=0.1, key="pr_loza")
            with col_l3:
                ci_loza = st.number_input("Цијена лозе (КМ/кг)", min_value=0.0, value=2.00, step=0.10, key="ci_loza")

            # Шипурак
            st.markdown("**🌹 Шипурак** *(обрачун у м² | предефинисана цијена: 4,00 КМ/кг)*")
            col_sip1, col_sip2, col_sip3 = st.columns(3)
            with col_sip1:
                povrsina_sipurak = st.number_input("Површина шипурка (м²)", min_value=0.0, value=0.0, step=10.0, key="p_sipurak")
            with col_sip2:
                pr_sipurak = st.number_input("Принос (кг/м²)", min_value=0.0, value=1.0, step=0.1, key="pr_sipurak")
            with col_sip3:
                ci_sipurak = st.number_input("Цијена шипурка (КМ/кг)", min_value=0.0, value=4.00, step=0.20, key="ci_sipurak")

            # Обрачуни за поједине воћке и културе
            osnova_jabuka = st_jabuka * pr_jabuka * ci_jabuka
            steta_jabuka = osnova_jabuka * коефицијент

            osnova_kruska = st_kruska * pr_kruska * ci_kruska
            steta_kruska = osnova_kruska * коефицијент

            osnova_sljiva = st_sljiva * pr_sljiva * ci_sljiva
            steta_sljiva = osnova_sljiva * коефицијент

            osnova_orah = st_orah * pr_orah * ci_orah
            steta_orah = osnova_orah * коефицијент

            osnova_loza = povrsina_loza * pr_loza * ci_loza
            steta_loza = osnova_loza * коефицијент

            osnova_sipurak = povrsina_sipurak * pr_sipurak * ci_sipurak
            steta_sipurak = osnova_sipurak * коефицијент

            укупна_стабла_воће = st_jabuka + st_kruska + st_sljiva + st_orah
            укупни_принос_воћа_кг = (st_jabuka * pr_jabuka) + (st_kruska * pr_kruska) + (st_sljiva * pr_sljiva) + (st_orah * pr_orah) + (povrsina_loza * pr_loza) + (povrsina_sipurak * pr_sipurak)
            основа_воћке = osnova_jabuka + osnova_kruska + osnova_sljiva + osnova_orah + osnova_loza + osnova_sipurak
            штета_воћке = steta_jabuka + steta_kruska + steta_sljiva + steta_orah + steta_loza + steta_sipurak

            st.caption(
                f"📊 Укупно воће, лоза и шипурак: род **{укупни_принос_воћа_кг:,.0f} кг** | "
                f"Основа: {основа_воћке:,.2f} КМ | Штета: **{штета_воћке:,.2f} КМ**"
            )

        # Житарице
        with st.expander("🌾 Житарице (Жито, Силажни кукуруз)", expanded=True):
            st.caption("Унесите оштећене површине житарица у дулумима (1 дулум = 1.000 м²).")

            # Жито
            st.markdown("**🌾 Жито**")
            col_z1, col_z2, col_z3 = st.columns(3)
            with col_z1:
                dulumi_zito = st.number_input("Површина жита (дулуми)", min_value=0.0, value=0.0, step=0.1, key="d_zito")
            with col_z2:
                pr_zito = st.number_input("Принос (кг/дулуму)", min_value=0.0, value=500.0, step=50.0, key="pr_zito")
            with col_z3:
                ci_zito = st.number_input("Цијена (КМ/кг)", min_value=0.0, value=0.70, step=0.05, key="ci_zito")

            # Силажни кукуруз
            st.markdown("**🌽 Силажни кукуруз**")
            col_c1, col_c2, col_c3 = st.columns(3)
            with col_c1:
                dulumi_kukuruz = st.number_input("Површина кукуруза (дулуми)", min_value=0.0, value=0.0, step=0.1, key="d_kukuruz")
            with col_c2:
                pr_kukuruz = st.number_input("Принос (кг/дулуму)", min_value=0.0, value=4000.0, step=200.0, key="pr_kukuruz")
            with col_c3:
                ci_kukuruz = st.number_input("Цијена (КМ/кг)", min_value=0.0, value=0.12, step=0.01, key="ci_kukuruz")

            osnova_zito = dulumi_zito * pr_zito * ci_zito
            steta_zito = osnova_zito * коефицијент

            osnova_kukuruz = dulumi_kukuruz * pr_kukuruz * ci_kukuruz
            steta_kukuruz = osnova_kukuruz * коефицијент

            osnova_zitarice = osnova_zito + osnova_kukuruz
            steta_zitarice = steta_zito + steta_kukuruz
            prinos_zitarice_kg = (dulumi_zito * pr_zito) + (dulumi_kukuruz * pr_kukuruz)

            st.caption(
                f"📊 Укупно житарице: **{prinos_zitarice_kg:,.0f} кг** | "
                f"Основа: {osnova_zitarice:,.2f} КМ | Штета: **{steta_zitarice:,.2f} КМ**"
            )

        # Баште
        with st.expander("🌱 Баште (паприка, парадајз, остало поврће)", expanded=True):
            површина_баште = st.number_input("Површина баште (м²)", min_value=0.0, value=0.0, step=10.0, key="b_p")
            
            основа_баште = (површина_баште / 100.0) * 300.0
            штета_баште = основа_баште * коефицијент
            st.caption(f"Основа (300 КМ / 100 м²): {основа_баште:,.2f} КМ | Штета: **{штета_баште:,.2f} КМ**")

        # Кромпир
        with st.expander("🥔 Кромпир (обрачун: 4.000 КМ по дулуму)", expanded=True):
            број_дулума_кромпир = st.number_input("Површина кромпира (дулуми / 1.000 м²)", min_value=0.0, value=0.0, step=0.1, key="k_d")
            
            процењени_принос_кромпир_кг = број_дулума_кромпир * 4000.0
            
            основа_кромпир = број_дулума_кромпир * 4000.0
            штета_кромпир = основа_кромпир * коефицијент
            
            st.caption(
                f"ℹ️ Процијењени изгубљени род кромпира: **{процењени_принос_кромпир_кг:,.0f} кг** ({процењени_принос_кромпир_кг/1000.0:.2f} т) | "
                f"Основа: {основа_кромпир:,.2f} КМ | Штета: **{штета_кромпир:,.2f} КМ**"
            )

        # Лук
        with st.expander("🧅 Лук (плантажно: 400 кг/дулуму × 3,00 КМ/кг = 1.200 КМ/дулуму)", expanded=True):
            broj_duluma_luk = st.number_input("Површина лука (дулуми / 1.000 м²)", min_value=0.0, value=0.0, step=0.1, key="l_d")
            col_l1, col_l2 = st.columns(2)
            with col_l1:
                pr_luk = st.number_input("Принос лука (кг/дулуму)", min_value=0.0, value=400.0, step=50.0, key="pr_luk")
            with col_l2:
                ci_luk = st.number_input("Цијена лука (КМ/кг)", min_value=0.0, value=3.00, step=0.10, key="ci_luk")

            osnova_luk = broj_duluma_luk * pr_luk * ci_luk
            steta_luk = osnova_luk * коефицијент
            procenjeni_prinos_luk_kg = broj_duluma_luk * pr_luk

            st.caption(
                f"ℹ️ Процијењени изгубљени род лука: **{procenjeni_prinos_luk_kg:,.0f} кг** | "
                f"Основа: {osnova_luk:,.2f} КМ | Штета: **{steta_luk:,.2f} КМ**"
            )

        # Купус
        with st.expander("🥬 Купус (обрачун: 200 КМ на 100 м²)", expanded=True):
            површина_купуса = st.number_input("Површина купуса (м²)", min_value=0.0, value=0.0, step=10.0, key="kup_p")
            
            процењени_принос_купус_кг = (површина_купуса / 100.0) * 550.0
            
            основа_купус = (површина_купуса / 100.0) * 200.0
            штета_купус = основа_купус * коефицијент
            
            st.caption(
                f"ℹ️ Процијењени изгубљени род купуса: **{процењени_принос_купус_кг:,.0f} кг** | "
                f"Основа: {основа_купус:,.2f} КМ | Штета: **{штета_купус:,.2f} КМ**"
            )

    # Укупни обрачуни
    укупна_основа = основа_воћке + osnova_zitarice + основа_баште + основа_кромпир + osnova_luk + основа_купус
    укупна_штета = штета_воћке + steta_zitarice + штета_баште + штета_кромпир + steta_luk + штета_купус

    with col_right:
        st.subheader("3. Преглед обрачуна штете")
        
        # Кључне метрике
        m1, m2 = st.columns(2)
        with m1:
            st.metric(label="Укупна основица прије коефицијента", value=f"{укупна_основа:,.2f} КМ")
        with m2:
            st.metric(label="Укупна штета за исплату", value=f"{укупна_штета:,.2f} КМ", delta=f"{проценат_текст} од основице")

        # Грађење редова за табелу (ставке)
        tabela_rows = []
        spec_list = []

        if st_jabuka > 0:
            tabela_rows.append({
                "Култура": "Воће - Јабука",
                "Параметри": f"{st_jabuka} ст. × {pr_jabuka}кг × {ci_jabuka:.2f}КМ",
                "Референтни род": f"{st_jabuka * pr_jabuka:,.0f} кг",
                "Основа (КМ)": osnova_jabuka,
                "Коефицијент": проценат_текст,
                "Коначна штета (КМ)": steta_jabuka
            })
            spec_list.append(f"Јабука: {steta_jabuka:.2f} КМ")

        if st_kruska > 0:
            tabela_rows.append({
                "Култура": "Воће - Крушка",
                "Параметри": f"{st_kruska} ст. × {pr_kruska}кг × {ci_kruska:.2f}КМ",
                "Референтни род": f"{st_kruska * pr_kruska:,.0f} кг",
                "Основа (КМ)": osnova_kruska,
                "Коефицијент": проценат_текст,
                "Коначна штета (КМ)": steta_kruska
            })
            spec_list.append(f"Крушка: {steta_kruska:.2f} КМ")

        if st_sljiva > 0:
            tabela_rows.append({
                "Култура": "Воће - Шљива",
                "Параметри": f"{st_sljiva} ст. × {pr_sljiva}кг × {ci_sljiva:.2f}КМ",
                "Референтни род": f"{st_sljiva * pr_sljiva:,.0f} кг",
                "Основа (КМ)": osnova_sljiva,
                "Коефицијент": проценат_текст,
                "Коначна штета (КМ)": steta_sljiva
            })
            spec_list.append(f"Шљива: {steta_sljiva:.2f} КМ")

        if st_orah > 0:
            tabela_rows.append({
                "Култура": "Воће - Орах",
                "Параметри": f"{st_orah} ст. × {pr_orah}кг × {ci_orah:.2f}КМ",
                "Референтни род": f"{st_orah * pr_orah:,.0f} кг",
                "Основа (КМ)": osnova_orah,
                "Коефицијент": проценат_текст,
                "Коначна штета (КМ)": steta_orah
            })
            spec_list.append(f"Орах: {steta_orah:.2f} КМ")

        if povrsina_loza > 0:
            tabela_rows.append({
                "Култура": "Воће - Лоза",
                "Параметри": f"{povrsina_loza:.1f} м² × {pr_loza}кг × {ci_loza:.2f}КМ",
                "Референтни род": f"{povrsina_loza * pr_loza:,.0f} кг",
                "Основа (КМ)": osnova_loza,
                "Коефицијент": проценат_текст,
                "Коначна штета (КМ)": steta_loza
            })
            spec_list.append(f"Лоза: {steta_loza:.2f} КМ")

        if povrsina_sipurak > 0:
            tabela_rows.append({
                "Култура": "Воће - Шипурак",
                "Параметри": f"{povrsina_sipurak:.1f} м² × {pr_sipurak}кг × {ci_sipurak:.2f}КМ",
                "Референтни род": f"{povrsina_sipurak * pr_sipurak:,.0f} кг",
                "Основа (КМ)": osnova_sipurak,
                "Коефицијент": проценат_текст,
                "Коначна штета (КМ)": steta_sipurak
            })
            spec_list.append(f"Шипурак: {steta_sipurak:.2f} КМ")

        if dulumi_zito > 0:
            tabela_rows.append({
                "Култура": "Житарице - Жито",
                "Параметри": f"{dulumi_zito:.2f} дул. × {pr_zito}кг × {ci_zito:.2f}КМ",
                "Референтни род": f"{dulumi_zito * pr_zito:,.0f} кг",
                "Основа (КМ)": osnova_zito,
                "Коефицијент": проценат_текст,
                "Коначна штета (КМ)": steta_zito
            })
            spec_list.append(f"Жито: {steta_zito:.2f} КМ")

        if dulumi_kukuruz > 0:
            tabela_rows.append({
                "Култура": "Житарице - Кукуруз",
                "Параметри": f"{dulumi_kukuruz:.2f} дул. × {pr_kukuruz}кг × {ci_kukuruz:.2f}КМ",
                "Референтни род": f"{dulumi_kukuruz * pr_kukuruz:,.0f} кг",
                "Основа (КМ)": osnova_kukuruz,
                "Коефицијент": проценат_текст,
                "Коначна штета (КМ)": steta_kukuruz
            })
            spec_list.append(f"Кукуруз: {steta_kukuruz:.2f} КМ")

        if укупна_стабла_воће == 0 and povrsina_loza == 0 and povrsina_sipurak == 0:
            tabela_rows.append({
                "Култура": "Воћке и плантаже",
                "Параметри": "0 м²/стабала",
                "Референтни род": "0 кг",
                "Основа (КМ)": 0.0,
                "Коефицијент": проценат_текст,
                "Коначна штета (КМ)": 0.0
            })

        tabela_rows.append({
            "Култура": "Баште",
            "Параметри": f"{површина_баште:.1f} м² (300 КМ / 100 м²)",
            "Референтни род": "-",
            "Основа (КМ)": основа_баште,
            "Коефицијент": проценат_текст,
            "Коначна штета (КМ)": штета_баште
        })
        if штета_баште > 0:
            spec_list.append(f"Баште: {штета_баште:.2f} КМ")

        tabela_rows.append({
            "Култура": "Кромпир",
            "Параметри": f"{број_дулума_кромпир:.2f} дул. (4.000 КМ / дулуму)",
            "Референтни род": f"~{процењени_принос_кромпир_кг:,.0f} кг",
            "Основа (КМ)": основа_кромпир,
            "Коефицијент": проценат_текст,
            "Коначна штета (КМ)": штета_кромпир
        })
        if штета_кромпир > 0:
            spec_list.append(f"Кромпир: {штета_кромпир:.2f} КМ")

        if broj_duluma_luk > 0:
            tabela_rows.append({
                "Култура": "Поврће - Лук",
                "Параметри": f"{broj_duluma_luk:.2f} дул. × {pr_luk}кг × {ci_luk:.2f}КМ",
                "Референтни род": f"~{procenjeni_prinos_luk_kg:,.0f} кг",
                "Основа (КМ)": osnova_luk,
                "Коефицијент": проценат_текст,
                "Коначна штета (КМ)": steta_luk
            })
            spec_list.append(f"Лук: {steta_luk:.2f} КМ")

        tabela_rows.append({
            "Култура": "Купус",
            "Параметри": f"{површина_купуса:.1f} м² (200 КМ / 100 м²)",
            "Референтни род": f"~{процењени_принос_купус_кг:,.0f} кг",
            "Основа (КМ)": основа_купус,
            "Коефицијент": проценат_текст,
            "Коначна штета (КМ)": штета_купус
        })
        if штета_купус > 0:
            spec_list.append(f"Купус: {штета_купус:.2f} КМ")

        df = pd.DataFrame(tabela_rows)

        # Приказ табеле са форматираним бројевима
        df_display = df.copy()
        df_display["Основа (КМ)"] = df_display["Основа (КМ)"].apply(lambda x: f"{x:,.2f} КМ")
        df_display["Коначна штета (КМ)"] = df_display["Коначна штета (КМ)"].apply(lambda x: f"{x:,.2f} КМ")

        st.dataframe(df_display, use_container_width=True, hide_index=True)

        # Графички приказ расподјеле штете по културама
        if укупна_штета > 0:
            st.subheader("📊 Графички приказ штете по културама")
            fig = px.bar(
                df[df["Коначна штета (КМ)"] > 0],
                x="Култура",
                y="Коначна штета (КМ)",
                color="Култура",
                text="Коначна штета (КМ)",
                color_discrete_sequence=px.colors.qualitative.Set2,
                title="Износ процијењене штете по културама (КМ)"
            )
            fig.update_traces(texttemplate='%{text:.2f} КМ', textposition='outside')
            fig.update_layout(showlegend=False, height=330, margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig, use_container_width=True)

        # Хелпер функција за креирање слике графикона за ПДФ
        def generate_chart_image(df_active):
            plt.rcParams['font.sans-serif'] = ['DejaVu Sans', 'Arial', 'Liberation Sans']
            plt.rcParams['axes.unicode_minus'] = False
            
            fig, ax = plt.subplots(figsize=(6.5, 2.8), dpi=200)
            
            labels = df_active["Култура"].tolist()
            values = df_active["Коначна штета (КМ)"].tolist()
            
            bar_colors = ['#1b4332', '#2d6a4f', '#40916c', '#52b788', '#74c69d', '#95d5b2', '#b7e4c7', '#d8f3dc']
            bars = ax.bar(labels, values, color=bar_colors[:len(labels)], edgecolor='#1b4332', linewidth=0.8)
            
            ax.set_ylabel('Коначна штета (КМ)', fontsize=8, fontweight='bold', color='#1b4332')
            ax.set_title('Графички приказ штете по пољопривредним културама (КМ)', fontsize=9.5, fontweight='bold', color='#1b4332', pad=10)
            ax.grid(axis='y', linestyle='--', alpha=0.4)
            ax.set_axisbelow(True)
            
            max_val = max(values) if values and max(values) > 0 else 1.0
            for bar in bars:
                yval = bar.get_height()
                ax.text(
                    bar.get_x() + bar.get_width()/2.0,
                    yval + (max_val * 0.02),
                    f"{yval:,.2f} КМ",
                    ha='center', va='bottom', fontsize=7.5, fontweight='bold', color='#1b4332'
                )
                
            ax.set_ylim(0, max_val * 1.18)
            plt.xticks(rotation=12, ha='right', fontsize=8)
            plt.tight_layout()
            
            img_buf = io.BytesIO()
            plt.savefig(img_buf, format='png', dpi=200, bbox_inches='tight')
            plt.close(fig)
            img_buf.seek(0)
            return img_buf

        # ---------------------------------------------------------
        # Генерисање ПДФ Записника
        # ---------------------------------------------------------
        def generate_pdf():
            font_reg, font_bold = get_cyrillic_font()

            buffer = io.BytesIO()
            doc = SimpleDocTemplate(
                buffer,
                pagesize=A4,
                rightMargin=25,
                leftMargin=25,
                topMargin=25,
                bottomMargin=25
            )
            elements = []

            styles = getSampleStyleSheet()

            title_style = ParagraphStyle(
                'CyrTitle',
                parent=styles['Heading1'],
                fontName=font_bold,
                fontSize=11.5,
                leading=14,
                alignment=1,
                textColor=colors.HexColor('#1B4332'),
                spaceAfter=8
            )

            subtitle_style = ParagraphStyle(
                'CyrSubtitle',
                parent=styles['Heading2'],
                fontName=font_bold,
                fontSize=9,
                leading=11,
                alignment=1,
                textColor=colors.HexColor('#2D6A4F'),
                spaceAfter=12
            )

            normal_style = ParagraphStyle(
                'CyrNormal',
                parent=styles['Normal'],
                fontName=font_reg,
                fontSize=8,
                leading=10.5,
                spaceAfter=3
            )

            table_header_style = ParagraphStyle(
                'CyrTableHeader',
                parent=styles['Normal'],
                fontName=font_bold,
                fontSize=7.5,
                leading=9.5,
                alignment=1,
                textColor=colors.whitesmoke
            )

            table_body_style = ParagraphStyle(
                'CyrTableBody',
                parent=styles['Normal'],
                fontName=font_reg,
                fontSize=7.5,
                leading=9.5,
                alignment=1
            )

            table_bold_style = ParagraphStyle(
                'CyrTableBold',
                parent=styles['Normal'],
                fontName=font_bold,
                fontSize=7.5,
                leading=9.5,
                alignment=1
            )

            # Наслов документа
            elements.append(Paragraph("КОМИСИЈА ЗА ПРОЦЈЕНУ ШТЕТА ОД ЕЛЕМЕНТАРНИХ НЕПОГОДА ОПШТИНЕ НЕВЕСИЊЕ", title_style))
            elements.append(Paragraph("ЗАПИСНИК О ПРОЦЈЕНИ ШТЕТЕ НА ПОЉОПРИВРЕДНИМ КУЛТУРАМА ОД ГРАДА", subtitle_style))
            elements.append(Paragraph("<b>(Одјељење за пољопривреду и рурални развој Општине Невесиње)</b>", subtitle_style))
            elements.append(Spacer(1, 4))

            # Подаци о произвођачу и пријављеном службенику
            danas_datum = datetime.date.today().strftime("%d.%m.%Y.")
            elements.append(Paragraph(f"<b>Име и презиме произвођача:</b> {име_презиме}", normal_style))
            elements.append(Paragraph(f"<b>Насеље / Локација:</b> {насеље}", normal_style))
            elements.append(Paragraph(f"<b>Проценат штете (коефицијент насеља):</b> {проценат_текст} ({коефицијент:.2f})", normal_style))
            elements.append(Paragraph(f"<b>Службеник који је обрадио податке:</b> {officer_name}", normal_style))
            elements.append(Paragraph(f"<b>Датум обрачуна и протоколисања:</b> {danas_datum}", normal_style))
            elements.append(Spacer(1, 8))

            # Табела у ПДФ-у
            pdf_table_data = [
                [
                    Paragraph("Култура", table_header_style),
                    Paragraph("Унесени параметри", table_header_style),
                    Paragraph("Проц. род", table_header_style),
                    Paragraph("Основа (КМ)", table_header_style),
                    Paragraph("Коеф.", table_header_style),
                    Paragraph("Коначна штета (КМ)", table_header_style)
                ]
            ]

            for row in tabela_rows:
                pdf_table_data.append([
                    Paragraph(str(row["Култура"]), table_body_style),
                    Paragraph(str(row["Параметри"]), table_body_style),
                    Paragraph(str(row["Референтни род"]), table_body_style),
                    Paragraph(f"{row['Основа (КМ)']:,.2f}", table_body_style),
                    Paragraph(str(row["Коефицијент"]), table_body_style),
                    Paragraph(f"{row['Коначна штета (КМ)']:,.2f}", table_body_style)
                ])

            # Збирни ред
            pdf_table_data.append([
                Paragraph("УКУПНО ЗА ИСПЛАТУ", table_bold_style),
                Paragraph("-", table_bold_style),
                Paragraph(f"{укупни_принос_воћа_кг + prinos_zitarice_kg + процењени_принос_кромпир_кг + procenjeni_prinos_luk_kg + процењени_принос_купус_кг:,.0f} кг", table_bold_style),
                Paragraph(f"{укупна_основа:,.2f} КМ", table_bold_style),
                Paragraph(f"{проценат_текст}", table_bold_style),
                Paragraph(f"{укупна_штета:,.2f} КМ", table_bold_style)
            ])

            t = Table(pdf_table_data, colWidths=[95, 145, 85, 75, 45, 85])
            t.setStyle(TableStyle([
                ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#1B4332')),
                ('ALIGN', (0,0), (-1,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('BOTTOMPADDING', (0,0), (-1,0), 5),
                ('TOPPADDING', (0,0), (-1,0), 5),
                ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#B7E4C7')),
                ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#D8F3DC')),
                ('BOTTOMPADDING', (0,-1), (-1,-1), 5),
                ('TOPPADDING', (0,-1), (-1,-1), 5),
            ]))

            elements.append(t)
            elements.append(Spacer(1, 10))

            # Уградња графичког приказа у ПДФ
            df_active = df[df["Коначна штета (КМ)"] > 0]
            if not df_active.empty:
                chart_buf = generate_chart_image(df_active)
                chart_img = Image(chart_buf, width=470, height=180)
                elements.append(chart_img)
                elements.append(Spacer(1, 12))

            # Мјесто за потписе
            col_sig1 = Paragraph(
                "<b>Потпис чланова комисије:</b><br/><br/>"
                "1. ___________________________<br/><br/>"
                "2. ___________________________<br/><br/>"
                "3. ___________________________",
                normal_style
            )
            col_sig2 = Paragraph(
                f"<b>Податке обрадио службеник:</b><br/>{officer_name}<br/><br/>"
                "<b>Потпис пољопривредног произвођача:</b><br/><br/>"
                "___________________________",
                normal_style
            )

            sig_table = Table([[col_sig1, col_sig2]], colWidths=[260, 260])
            sig_table.setStyle(TableStyle([
                ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ]))
            elements.append(sig_table)

            doc.build(elements)
            buffer.seek(0)
            return buffer

        col_b1, col_b2 = st.columns(2)
        with col_b1:
            if st.button("💾 Сачувај обрачун у базу (Google Sheets)", type="primary", use_container_width=True):
                if not име_презиме.strip():
                    st.warning("⚠️ Унесите име и презиме произвођача пре чувања.")
                elif укупна_штета <= 0:
                    st.warning("⚠️ Обрачун мора имати процијењену штету већу од 0 КМ.")
                else:
                    spec_text = ", ".join(spec_list) if spec_list else "Штета обрачуната"
                    saved_gs = save_new_record(officer_name, име_презиме, насеље, коефицијент, укупна_основа, укупна_штета, spec_text)
                    if saved_gs:
                        st.success("✅ Обрачун је успешно сачуван у Google Sheets!")

        with col_b2:
            if st.button("📄 Генериши службени образац у ПДФ", type="secondary", use_container_width=True):
                if not име_презиме.strip():
                    st.warning("⚠️ Молимо унесите име и презиме произвођача прије генерисања ПДФ записника.")
                else:
                    with st.spinner("Генерисање ПДФ записника у току..."):
                        pdf_data = generate_pdf()
                        чисто_име = име_презиме.replace(' ', '_')
                        st.download_button(
                            label=f"⬇️ Преузми ПДФ Записник (обрадио: {officer_name})",
                            data=pdf_data,
                            file_name=f"Записник_штете_Nevesinje_{чисто_име}.pdf",
                            mime="application/pdf",
                            use_container_width=True
                        )

# ---------------------------------------------------------
# TAB 2: Збирни Дашборд (База Уноса)
# ---------------------------------------------------------
with tab2:
    st.subheader("📊 Збирни Дашборд обрађених штета у Општини Невесиње")
    st.caption("Аутоматски синхронизована база података са Google Sheets и аналитички преглед.")

    df_db = load_records_dataframe()

    if df_db.empty:
        st.info("ℹ️ Тек нема сачуваних обрачуна у бази. Искористите форму на првом табу за унос и сачувајте податке.")
    else:
        # KPI Метрике
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        total_farmers = len(df_db)
        total_damage = df_db["Коначна_Штета_КМ"].sum() if "Коначна_Штета_КМ" in df_db.columns else 0.0
        avg_damage = total_damage / total_farmers if total_farmers > 0 else 0.0
        top_settlement = df_db.groupby("Насеље")["Коначна_Штета_КМ"].sum().idxmax() if ("Насеље" in df_db.columns and not df_db.empty) else "-"

        with kpi1:
            st.metric("Укупно произвођача", f"{total_farmers}")
        with kpi2:
            st.metric("Укупна процијењена штета", f"{total_damage:,.2f} КМ")
        with kpi3:
            st.metric("Просјечна штета по произвођачу", f"{avg_damage:,.2f} КМ")
        with kpi4:
            st.metric("Највише погођено насеље", f"{top_settlement}")

        st.divider()

        # Аналитички дијаграми
        col_chart1, col_chart2 = st.columns(2)

        with col_chart1:
            if "Насеље" in df_db.columns and "Коначна_Штета_КМ" in df_db.columns:
                df_settlement = df_db.groupby("Насеље")["Коначна_Штета_КМ"].sum().reset_index()
                fig_s = px.bar(
                    df_settlement,
                    x="Насеље",
                    y="Коначна_Штета_КМ",
                    color="Насеље",
                    title="Укупна изгубљена штета по насељима (КМ)",
                    text_auto='.2f',
                    color_discrete_sequence=px.colors.qualitative.Bold
                )
                fig_s.update_layout(showlegend=False, height=330)
                st.plotly_chart(fig_s, use_container_width=True)

        with col_chart2:
            if "Службеник" in df_db.columns:
                df_officer = df_db["Службеник"].value_counts().reset_index()
                df_officer.columns = ["Службеник", "Број Записника"]
                fig_o = px.pie(
                    df_officer,
                    names="Службеник",
                    values="Број Записника",
                    title="Удио обрађених записника по службеницима",
                    color_discrete_sequence=px.colors.qualitative.Pastel
                )
                fig_o.update_layout(height=330)
                st.plotly_chart(fig_o, use_container_width=True)

        st.subheader("📋 Табеларни преглед сачуваних записника у бази")
        
        # Филтрирање
        f_col1, f_col2 = st.columns(2)
        with f_col1:
            filter_naselje = st.multiselect("Филтрирај по насељу:", options=list(df_db["Насеље"].unique()) if "Насеље" in df_db.columns else [])
        with f_col2:
            filter_sluzbenik = st.multiselect("Филтрирај по службенику:", options=list(df_db["Службеник"].unique()) if "Службеник" in df_db.columns else [])

        df_filtered = df_db.copy()
        if filter_naselje:
            df_filtered = df_filtered[df_filtered["Насеље"].isin(filter_naselje)]
        if filter_sluzbenik:
            df_filtered = df_filtered[df_filtered["Службеник"].isin(filter_sluzbenik)]

        st.dataframe(df_filtered, use_container_width=True, hide_index=True)

        # Преузимање CSV
        csv_data = df_filtered.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Експортуј комплетну базу у CSV",
            data=csv_data,
            file_name=f"База_штета_Невесиње_{datetime.date.today().strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True
        )
