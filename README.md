# ICDRA 2026 – Attestati di partecipazione

App Streamlit: carica il file del check-in, filtra chi si è presentato, genera gli attestati PDF e li invia per email.

## Pubblicazione
1. Carica questa cartella in un repository GitHub (privato va bene).
2. share.streamlit.io → *Create app* → repository, branch, file `app.py`.
3. *Settings → Secrets*: incolla il contenuto di `.streamlit/secrets.toml.example` con i valori reali.

## Password per le app (Gmail)
Account Google di 8icdra@gmail.com → Sicurezza → Verifica in due passaggi (deve essere attiva) → Password per le app → genera una password per "Mail". Va in `SMTP_PASSWORD`.

## Uso
1. **Partecipanti** – carica l'xlsx del desk, scegli foglio e riga d'intestazione, associa le colonne, indica quali valori significano "check-in fatto". Controlla le anomalie e correggi i nomi direttamente in tabella.
2. **Attestato** – compila testo, data, firmatario; carica eventualmente carta intestata PDF, logo e firma. L'invio resta bloccato finché ci sono segnaposto tra parentesi quadre.
3. **Invio** – manda prima una prova a te stesso, poi l'invio a tutti. Scarica il log CSV a fine invio: se l'invio si interrompe, ricaricalo per non reinviare a chi l'ha già ricevuto.

Gmail consente circa 500 destinatari al giorno da un account personale.
