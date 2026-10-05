"""ICDRA 2026 – Attestati di partecipazione: generazione e invio."""
import io
import re
import smtplib
import ssl
import time
import unicodedata
import zipfile
from datetime import datetime
from email.message import EmailMessage

import pandas as pd
import streamlit as st

from certificate import make_certificate, render_preview

st.set_page_config(page_title="ICDRA 2026 – Attestati", page_icon="🌿", layout="wide")



def secret(key, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:  # noqa: BLE001 – nessun secrets.toml
        return default


# ---------------------------------------------------------------- accesso
if secret("APP_PASSWORD") and not st.session_state.get("auth"):
    pw = st.text_input("Password", type="password")
    if pw and pw == secret("APP_PASSWORD"):
        st.session_state.auth = True
        st.rerun()
    st.stop()

st.title("🌿 ICDRA 2026 – Attestati di partecipazione")

PLACEHOLDER = re.compile(r"\[[A-ZÀ-Ü ]{3,}\]")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
NONE = "— nessuna —"


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")


def clean(v):
    if pd.isna(v):
        return ""
    return str(v).strip()


# ---------------------------------------------------------------- 1. dati
st.header("1 · Partecipanti")
up = st.file_uploader("File di check-in (xlsx o csv)", type=["xlsx", "xls", "csv"])
if not up:
    st.info("Carica il file del desk con la colonna di check-in compilata.")
    st.stop()



def detect_header(raw):
    """Prima riga che contiene una cella 'email': è l'intestazione."""
    for i, row in raw.head(20).iterrows():
        if any("mail" in str(v).lower() for v in row.values):
            return i + 1
    return 1


c1, c2 = st.columns(2)
if up.name.endswith("csv"):
    raw = pd.read_csv(up, header=None)
    header_row = c2.number_input("Riga d'intestazione", 1, 20, detect_header(raw))
    df = raw.iloc[header_row:].set_axis(raw.iloc[header_row - 1].values, axis=1)
else:
    xl = pd.ExcelFile(up)
    default_sheet = next((i for i, n in enumerate(xl.sheet_names) if "check" in n.lower()), 0)
    sheet = c1.selectbox("Foglio", xl.sheet_names, index=default_sheet)
    raw = xl.parse(sheet, header=None)
    header_row = c2.number_input("Riga d'intestazione", 1, 20, detect_header(raw), key=f"hdr_{sheet}")
    df = xl.parse(sheet, header=header_row - 1)
df = df.dropna(how="all")
df.columns = [str(c).strip() for c in df.columns]
cols = list(df.columns)


def guess(*keys):
    for k in keys:
        for c in cols:
            if k in c.lower():
                return c
    return NONE


opt = [NONE] + cols
m1, m2, m3 = st.columns(3)
name_mode = m1.radio("Nome", ["Colonna unica", "Nome + Cognome"], horizontal=True)
if name_mode == "Colonna unica":
    col_full = m1.selectbox("Nome completo", opt, index=opt.index(guess("nome sul badge", "partecipante", "nominativo", "full name", "name", "nome")))
else:
    col_first = m1.selectbox("Nome", opt, index=opt.index(guess("first", "nome")))
    col_last = m1.selectbox("Cognome", opt, index=opt.index(guess("last", "cognome", "surname")))
col_email = m2.selectbox("Email", opt, index=opt.index(guess("mail")))
col_aff = m2.selectbox("Affiliazione (opz.)", opt, index=opt.index(guess("affil", "institution", "ente")))
col_ck = m3.selectbox("Colonna check-in", opt, index=opt.index(guess("check")))
col_title = m3.selectbox(
    "Titolo contributo (opz.)", opt,
    index=opt.index(guess("titolo contributo", "titolo del contributo", "contribution title", "abstract title")),
    help="Non la colonna Dr./Prof.: serve il titolo dell'abstract.",
)

with st.expander("Colonne per l'attestato specifico (ruolo)", expanded=True):
    r1, r2, r3, r4 = st.columns(4)
    col_role = r1.selectbox("Ruolo (Orale/Poster)", opt, index=opt.index(guess("ruolo", "role")))
    col_type = r2.selectbox("Tipo intervento (Orale/Invited/Keynote)", opt, index=opt.index(guess("tipo", "type")))
    col_sess = r3.selectbox("Sessione", opt, index=opt.index(guess("sessione", "session")))
    col_chair = r4.selectbox("Chair / convener", opt, index=opt.index(guess("chair", "convener")))

if col_ck == NONE or col_email == NONE:
    st.warning("Indica almeno la colonna email e la colonna di check-in.")
    st.stop()

values = sorted({clean(v) for v in df[col_ck]})
default_yes = [v for v in values if v.lower() in {"sì", "si", "yes", "x", "ok", "✓", "✔", "presente", "true", "1"}]
yes_vals = st.multiselect("Valori che indicano check-in effettuato", values, default=default_yes)

def role_fields(r):
    role = clean(r[col_role]).lower() if col_role != NONE else ""
    typ = clean(r[col_type]) if col_type != NONE else ""
    if not typ and "oral" in role:
        typ = "Orale"
    return {
        "talk": typ if typ in TALK_TYPES else ("Orale" if typ else ""),
        "session": clean(r[col_sess]) if col_sess != NONE and typ else "",
        "poster": "poster" in role,
        "chair": chairs_session(clean(r[col_chair])) if col_chair != NONE else False,
        "custom": "",
    }


def chairs_session(v):
    """Vero solo se tra gli incarichi c'è una sessione scientifica
    (esclusi General Assembly, Award Ceremony, Round Table, segni generici)."""
    for seg in v.split(";"):
        seg = seg.strip().lower()
        if len(seg) > 3 and not re.search(r"general assembly|award|round table|closing", seg):
            return True
    return False


TALK_TYPES = ["", "Orale", "Invited", "Keynote"]
rows = []
for _, r in df.iterrows():
    if clean(r[col_ck]) not in yes_vals:
        continue
    if name_mode == "Colonna unica":
        name = clean(r[col_full]) if col_full != NONE else ""
    else:
        name = f"{clean(r[col_first]) if col_first != NONE else ''} {clean(r[col_last]) if col_last != NONE else ''}".strip()
    rows.append({
        "Invia": True,
        "name": re.sub(r"\s+", " ", name),
        "email": clean(r[col_email]).lower(),
        "affiliation": clean(r[col_aff]) if col_aff != NONE else "",
        "contribution": clean(r[col_title]) if col_title != NONE else "",
        **role_fields(r),
    })
COLS = ["Invia", "name", "email", "affiliation", "talk", "session", "poster", "chair", "custom", "contribution"]
people = pd.DataFrame(rows, columns=COLS)


def kind(r):
    k = [{"Orale": "Orale", "Invited": "Invited", "Keynote": "Keynote"}.get(r["talk"], "")]
    k += ["Poster" if r["poster"] else "", "Chair" if r["chair"] else ""]
    if r.get("custom"):
        return "Personalizzato"
    return " + ".join(x for x in k if x) or "Partecipazione"

# controlli
problems = []
for i, r in people.iterrows():
    if not r["name"]:
        problems.append(f"Riga senza nome (email: {r['email'] or '—'})")
    if not EMAIL_RE.match(r["email"]):
        problems.append(f"Email non valida o mancante: {r['name']} → «{r['email']}»")
dups = people[people.duplicated("email", keep=False) & people["email"].ne("")]
for e in dups["email"].unique():
    problems.append(f"Email duplicata (riceverebbe più attestati): {e}")

st.write(f"**{len(people)}** partecipanti con check-in su {len(df)} righe.")
if len(people):
    st.caption("Attestati per tipo: " + " · ".join(f"{k} {v}" for k, v in people.apply(kind, axis=1).value_counts().items()))
if problems:
    with st.expander(f"⚠️ {len(problems)} anomalie da verificare", expanded=True):
        for p in problems:
            st.write("• " + p)

people = st.data_editor(
    people, width="stretch", hide_index=True, num_rows="dynamic",
    column_config={
        "Invia": st.column_config.CheckboxColumn(width="small"),
        "name": "Nome sull'attestato", "email": "Email",
        "affiliation": "Affiliazione", "contribution": "Titolo contributo",
        "talk": st.column_config.SelectboxColumn("Intervento", options=TALK_TYPES),
        "session": "Sessione", "poster": st.column_config.CheckboxColumn("Poster"),
        "chair": st.column_config.CheckboxColumn("Chair"),
        "custom": st.column_config.TextColumn(
            "Frase ruolo personalizzata",
            help="Se compilata sostituisce la frase automatica, es.: moderated the Round Table",
        ),
    },
    key="editor",
)
selected = people[people["Invia"] & people["name"].ne("")].reset_index(drop=True)

# ---------------------------------------------------------------- 2. attestato
def role_phrases_ui():
    with st.expander("Frasi per ruolo ({session} = sessione)", expanded=False):
        return {
            "Orale": st.text_input("Orale", "delivered an oral presentation in the session “{session}”"),
            "Invited": st.text_input("Invited", "delivered an invited lecture in the session “{session}”"),
            "Keynote": st.text_input("Keynote", "delivered the keynote lecture in the {session}"),
            "poster": st.text_input("Poster", "presented a poster"),
            "chair": st.text_input("Chair", "served as session chair"),
        }


st.header("2 · Attestato")
s1, s2 = st.columns([3, 2])
with s1:
    cfg = {
        "title": st.text_input("Titolo", "Certificate of Attendance"),
        "intro": st.text_input("Formula d'apertura", "This is to certify that"),
        "body": st.text_area(
            "Corpo (segnaposto: {hours_sentence}, {role_sentence}, {contribution_sentence})",
            "attended the <b>8th International Conference on Duckweed Research and Applications (8th ICDRA)</b>, "
            "held at the Royal Palace of Portici, Department of Agricultural Sciences, University of Naples "
            "Federico II, Portici (Italy), from 28&nbsp;September to 2&nbsp;October&nbsp;2026{hours_sentence}{role_sentence}{contribution_sentence}.",
            height=120,
        ),
        "hours": st.text_input("Ore di partecipazione (vuoto = non indicate)", ""),
        "hours_sentence": st.text_input("Frase ore ({hours})", ", for a total of {hours} hours"),
        "include_contribution": st.checkbox("Indica il titolo del contributo, se presente", value=True),
        "contribution_sentence": st.text_input(
            "Frase contributo ({title})", ", and presented the contribution entitled “{title}”"
        ),
        "role_phrases": role_phrases_ui(),
        "place_date": st.text_input("Luogo e data", "Portici, [DATA DI EMISSIONE]"),
        "signatory_name": st.text_input("Firmatario", "[NOME FIRMATARIO]"),
        "signatory_role": st.text_area("Qualifica (una riga per riga)", "[QUALIFICA]\n8th ICDRA 2026", height=70),
    }
with s2:
    tpl = st.file_uploader("Carta intestata PDF (opz., 1 pagina)", type=["pdf"])
    logo = st.file_uploader("Logo (opz., PNG trasparente)", type=["png", "jpg", "jpeg"])
    sig = st.file_uploader("Firma scansionata (opz., PNG trasparente)", type=["png", "jpg", "jpeg"])
    cfg["color"] = st.color_picker("Colore", "#2E6B3A")
    cfg["center_body"] = st.checkbox("Corpo centrato", value=True)
    a, b, c = st.columns(3)
    cfg["margin_top_mm"] = a.number_input("Margine sup. mm", 5, 120, 55 if tpl else 25)
    cfg["margin_bottom_mm"] = b.number_input("Margine inf. mm", 5, 120, 35 if tpl else 25)
    cfg["margin_side_mm"] = c.number_input("Margine lat. mm", 10, 80, 30)
    cfg["logo_height_mm"] = st.number_input("Altezza logo mm", 10, 60, 22)

tpl_b = tpl.getvalue() if tpl else None
logo_b = logo.getvalue() if logo else None
sig_b = sig.getvalue() if sig else None

leftover = [f"{k}: {m}" for k, v in cfg.items() if isinstance(v, str) for m in PLACEHOLDER.findall(v)]
if leftover:
    st.error("Segnaposto da compilare prima dell'invio: " + ", ".join(leftover))

if len(selected):
    idx = st.selectbox("Anteprima", range(len(selected)), format_func=lambda i: selected.loc[i, "name"])
    pdf, overflow = make_certificate(selected.loc[idx].to_dict(), cfg, tpl_b, logo_b, sig_b)
    if overflow:
        st.warning("Il testo invade l'area firma: riduci i margini o accorcia il corpo.")
    st.image(render_preview(pdf), width="stretch")
    longest = selected.loc[selected["contribution"].str.len().idxmax()] if selected["contribution"].str.len().max() else None
    if longest is not None and longest.name != idx:
        if st.button(f"Controlla il caso con il titolo più lungo ({longest['name']})"):
            p2, ov2 = make_certificate(longest.to_dict(), cfg, tpl_b, logo_b, sig_b)
            st.image(render_preview(p2), width="stretch")
            if ov2:
                st.warning("Questo attestato sfora nell'area firma.")


def all_pdfs():
    out, over = {}, []
    for _, r in selected.iterrows():
        pdf, ov = make_certificate(r.to_dict(), cfg, tpl_b, logo_b, sig_b)
        out[r["email"] + "|" + r["name"]] = (f"Certificate_8thICDRA2026_{slug(r['name'])}.pdf", pdf)
        if ov:
            over.append(r["name"])
    return out, over


if st.button("Genera ZIP di tutti gli attestati", disabled=not len(selected)):
    with st.spinner("Generazione…"):
        pdfs, over = all_pdfs()
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for fn, pdf in pdfs.values():
                zf.writestr(fn, pdf)
    if over:
        st.warning("Testo nell'area firma per: " + ", ".join(over))
    st.download_button("Scarica ZIP", z.getvalue(), "Attestati_8thICDRA2026.zip", "application/zip")

# ---------------------------------------------------------------- 3. invio
st.header("3 · Invio")
if not secret("SMTP_USER") or not secret("SMTP_PASSWORD"):
    st.warning("Configura SMTP_USER e SMTP_PASSWORD nei Secrets dell'app per abilitare l'invio.")
    st.stop()

sender = secret("SMTP_USER")
e1, e2 = st.columns([3, 2])
with e1:
    from_name = st.text_input("Nome mittente", "8th ICDRA 2026")
    subject = st.text_input("Oggetto", "8th ICDRA 2026 – Certificate of attendance")
    body_mail = st.text_area(
        "Testo email (segnaposto: {name})",
        "Dear {name},\n\nThank you for taking part in the 8th International Conference on Duckweed Research "
        "and Applications, held in Portici from 28 September to 2 October 2026.\n\n"
        "Please find attached your certificate of attendance.\n\n"
        "With kind regards,\n\nOn behalf of the Organising Committee\n8th ICDRA 2026",
        height=230,
    )
with e2:
    reply_to = st.text_input("Reply-To (opz.)", "")
    cc = st.text_input("Cc fisso (opz.)", "")
    delay = st.slider("Pausa tra invii (s)", 1, 15, 4)
    prev_log = st.file_uploader("Log di un invio precedente (per non reinviare)", type=["csv"])

already = set()
if prev_log:
    lg = pd.read_csv(prev_log)
    already = set(lg.loc[lg["esito"] == "inviato", "email"].str.lower())
    st.write(f"{len(already)} indirizzi già serviti verranno saltati.")


def send(to, name, fn, pdf):
    msg = EmailMessage()
    msg["From"] = f"{from_name} <{sender}>"
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    if reply_to:
        msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg.set_content(body_mail.format(name=name))
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=fn)
    return msg


def smtp():
    s = smtplib.SMTP_SSL(secret("SMTP_HOST", "smtp.gmail.com"), int(secret("SMTP_PORT", 465)),
                         context=ssl.create_default_context())
    s.login(sender, secret("SMTP_PASSWORD"))
    return s


blocked = bool(leftover) or not len(selected)

st.subheader("Prova")
test_to = st.text_input("Invia un attestato di prova a", sender)
if st.button("Invia prova", disabled=blocked):
    r = selected.loc[idx]
    pdf, _ = make_certificate(r.to_dict(), cfg, tpl_b, logo_b, sig_b)
    with smtp() as s:
        s.send_message(send(test_to, r["name"], f"Certificate_8thICDRA2026_{slug(r['name'])}.pdf", pdf))
    st.success(f"Prova inviata a {test_to} (attestato di {r['name']}).")

st.subheader("Invio a tutti")
todo = selected[~selected["email"].isin(already) & selected["email"].str.match(EMAIL_RE.pattern)]
todo = todo.drop_duplicates("email")
st.write(f"Verranno inviati **{len(todo)}** attestati da `{sender}`.")
confirm = st.checkbox(f"Confermo l'invio di {len(todo)} email ai partecipanti")
if st.button("Invia a tutti", type="primary", disabled=blocked or not confirm or not len(todo)):
    log, bar = [], st.progress(0.0)
    status = st.empty()
    s = smtp()
    for i, (_, r) in enumerate(todo.iterrows(), 1):
        fn = f"Certificate_8thICDRA2026_{slug(r['name'])}.pdf"
        try:
            pdf, _ = make_certificate(r.to_dict(), cfg, tpl_b, logo_b, sig_b)
            try:
                s.send_message(send(r["email"], r["name"], fn, pdf))
            except smtplib.SMTPServerDisconnected:
                s = smtp()
                s.send_message(send(r["email"], r["name"], fn, pdf))
            esito, err = "inviato", ""
        except Exception as ex:  # noqa: BLE001
            esito, err = "errore", str(ex)
        log.append({"ora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "name": r["name"],
                    "email": r["email"], "esito": esito, "dettaglio": err})
        bar.progress(i / len(todo))
        status.write(f"{i}/{len(todo)} – {r['name']}: {esito}")
        time.sleep(delay)
    try:
        s.quit()
    except Exception:  # noqa: BLE001
        pass
    logdf = pd.DataFrame(log)
    st.session_state.log = logdf
    n_ok = (logdf["esito"] == "inviato").sum()
    (st.success if n_ok == len(logdf) else st.warning)(f"Inviati {n_ok}/{len(logdf)}.")

if "log" in st.session_state:
    st.dataframe(st.session_state.log, width="stretch", hide_index=True)
    st.download_button("Scarica log CSV", st.session_state.log.to_csv(index=False).encode(),
                       f"log_attestati_{datetime.now():%Y%m%d_%H%M}.csv", "text/csv")
