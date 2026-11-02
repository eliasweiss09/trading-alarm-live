# Trading-Alarm: Was das System macht

Eine Erklärung zum Nachprüfen. Stand: 2. November 2026.

## Kurz gesagt

Ein kleines Programm läuft an jedem US-Börsentag automatisch auf einem Server von GitHub. Es liest **öffentliche, kostenlose** Börsen- und Firmendaten, filtert sie nach festen Regeln und schickt **Textnachrichten** aufs Handy: drei Übersichten pro Tag und dazu Verkaufs-Hinweise nach dem eigenen Plan, **jede Minute geprüft**.

**Das Programm kauft und verkauft nichts.** Es ist mit keinem Broker (auch nicht mit Trade Republic) und keinem Bankkonto verbunden, hat keinen Zugriff auf Geld und kennt keine Passwörter. Gekauft und verkauft wird nur von Hand in der Trade-Republic-App.

**Der Laptop muss nicht eingeschaltet sein.** Alles läuft in der Cloud bei GitHub.

---

## 1. Wie es technisch funktioniert

```
GitHub (Cloud-Server, kostenlos)
   │  startet das Programm automatisch zu festen Zeiten
   ▼
alert.py  ──liest──►  Yahoo Finance   (Kurse, Schlagzeilen, Euro-Dollar-Kurs)
          ──liest──►  Nasdaq Trader   (Liste aller US-Aktien)
          ──liest──►  SEC / EDGAR     (offizielle Firmenmeldungen: Gewinn, Umsatz)
   │
   ▼  filtert nach den Regeln in trader/config.json
ntfy.sh  ──Push──►  ntfy-App am Handy
```

| Teil | Was es ist |
|---|---|
| **GitHub** | Speichert das Programm und führt es zu festen Uhrzeiten aus („GitHub Actions“). Das Repository ist **öffentlich**, weil GitHub Actions für öffentliche Repositories unbegrenzt kostenlos ist. Nur so ist die Prüfung jede Minute gratis möglich. |
| **Geheime Einstellungen (Secrets)** | Das ntfy-Thema und die Kontakt-E-Mail für die SEC stehen **nicht** im Code, sondern als verschlüsselte „Secrets“ bei GitHub. Die sieht niemand, auch nicht in den Protokollen. |
| **alert.py, tools/scan.py, tools/news.py, growth.py** | Die Python-Programme. Sie lesen nur Daten und rechnen. |
| **trader/config.json** | Die Regeln: Budget, maximaler Verlust, Gebühren usw. |
| **ntfy.sh / ntfy-App** | Ein kostenloser Dienst für Push-Nachrichten. Das „Thema“ ist ein langer, zufälliger Kanal-Name, den nur Elias kennt. |
| **GitHub Desktop** | Programm am Laptop, um Änderungen hochzuladen. |

## 2. Die Nachrichten pro Börsentag

Uhrzeiten in österreichischer Zeit (Winter und Sommer gleich). In den zwei Wochen, in denen nur die USA die Uhr umgestellt haben (zum Beispiel 15. bis 26. März 2027), kommt alles eine Stunde früher. GitHub startet die Läufe oft 5 bis 60 Minuten später. Das Programm wartet dann bzw. holt nach, und die Nachmittags-Prüfung startet direkt nach der Vormittags-Prüfung. An Wochenenden und US-Feiertagen kommt nichts.

Alle Preise stehen **in Dollar und umgerechnet in Euro** (Tageskurs Euro/Dollar). Jede Nachricht erklärt am Anfang, was sie bedeutet, und sagt Schritt für Schritt, was in Trade Republic zu tun ist.

### a) ca. 14:15: „Vorbörse“ (Day Trading, zum Beobachten)
- Prüft **rund 900 große US-Firmen** (S&P 500 und S&P MidCap 400) anhand der Schlusskurse. Nur diese, weil es sie sicher bei Trade Republic gibt. Das gilt auch für die Ausbrüche und die Wachstums-Liste.
- Behält nur Aktien **von 6 bis 500 € pro Stück**, die viel gehandelt werden (mehr als 1 Million Stück am Tag).
- Sucht **Muster**, zum Beispiel „höchster Schlusskurs seit 20 Tagen“ oder „stark gefallen“ (RSI unter 30).
- Schickt **6 Aktien** mit Kurs, Tagesbewegung und einer Schlagzeile, in der die Firma vorkommt. Das ist noch kein Kaufsignal.

### b) ca. 14:20: „Wachstums-Liste“ (langfristig)
- Liest die **offiziellen Pflichtmeldungen aller US-Firmen bei der SEC** (US-Börsenaufsicht).
- Behält Firmen, deren **Gewinn 6 Jahre in Folge positiv** war, in **mindestens 4 von 5 Jahren gestiegen** ist, um **mindestens 10 % pro Jahr** gewachsen ist, deren Umsatz um mindestens 5 % pro Jahr gewachsen ist und die **mindestens 1 Mrd. $ Umsatz** haben.
- Schickt die **10 mit dem stärksten und stetigsten Wachstum** von 6 bis 500 € pro Aktie, mit letztem Quartal, nächstem Zahlen-Termin und Schlagzeile.

### c) ca. 15:55: „Eröffnung“ (Day Trading)
- Die Börse öffnet um 15:30. Die ersten 15 Minuten ergeben eine **Spanne** (höchster und tiefster Kurs).
- Gemeldet werden Aktien, die danach **über diese Spanne gestiegen** sind („Ausbruch“).
- Zu jeder Aktie:
  - **Stückzahl** und wie viel Euro das sind
  - **Stop-Loss** = Mitte der Spanne. Das ist der Kurs, zu dem man aussteigt, wenn es schiefgeht. Man soll ihn **sofort in Trade Republic setzen**.
  - **Ziel** = Einstieg + 2 × Abstand zum Stop (Gewinn doppelt so groß wie das Risiko)
  - **Höchstmöglicher Verlust inklusive 2 € Gebühren**, höchstens 20 €, und der Gewinn beim Ziel
  - Höchstens 750 € pro Position
- Darunter eine Schritt-für-Schritt-Anleitung zum Kaufen und Stop-Loss-Setzen in Trade Republic.

### d) Verkaufs-Hinweise (von ca. 15:55 bis 22:00, jede Minute geprüft)
Die Aktien aus der 15:55-Nachricht bleiben **bis Handelsschluss unter Beobachtung**. **Jede Minute** prüft das Programm die Kurse (Minuten-Kerzen, Hoch und Tief, dazu der aktuelle Kurs):
- **„VERKAUFEN: … Stop erreicht“**: Der Kurs ist auf den Stop gefallen. Raus, damit der Verlust klein bleibt.
- **„VERKAUFEN: … Ziel erreicht“**: Der geplante Gewinn (2 × Risiko) ist da.
- **„VERKAUFEN: … Gewinn sichern“**: Die Aktie war schon mindestens **1 × Risiko im Plus** und fällt danach so weit zurück, dass **die Hälfte des besten Gewinns wieder weg ist**. So wird aus einem Gewinn kein Verlust. Beispiel: Einstieg 100 $, Stop 98 $, Hoch 102,60 $, Rückfall unter 101,30 $ → Hinweis.
- **ca. 21:30: „Bis 21:55 Uhr alles verkaufen“**: Erinnerung, dass beim Day Trading nichts über Nacht gehalten wird, mit dem Stand jeder offenen Aktie.

Jeder Hinweis erklärt, **was passiert ist**, **warum verkaufen**, das **aktuelle Ergebnis in Euro** und **was in Trade Republic zu tun ist**. Er kommt nur **einmal**. Das Programm weiß nicht, was tatsächlich gekauft wurde: Die Hinweise gelten nur für Aktien, die man an dem Tag wirklich gekauft hat.

**Wie schnell?** In der Regel **1 bis 3 Minuten** nach dem Ereignis. Trotzdem gilt: **Der Stop-Loss in Trade Republic ist schneller** (er verkauft sofort und automatisch) und sollte immer gesetzt sein. Die Nachricht ist die zweite Absicherung.

## 3. Die eingestellten Regeln (trader/config.json)

| Regel | Wert |
|---|---|
| Broker | Trade Republic (von Hand) |
| Budget | 1.000 € |
| Höchster Verlust pro Trade, inkl. Gebühren | 20 € |
| Gebühr pro Order | 1 € (Kauf + Verkauf = 2 €) |
| Höchster Verlust pro Tag | 100 € |
| Höchstens Trades pro Tag | 3 |
| Preis pro Aktie | 6 € bis 500 € |
| Größte Position | 750 € |
| Ziel | 2 × Risiko |
| Gewinn sichern | ab +1 × Risiko, Hinweis wenn die Hälfte des besten Gewinns weg ist |
| Spätestens verkaufen | 21:55 (5 Minuten vor US-Börsenschluss) |
| Optionen, Kredit (Margin), Leerverkauf | aus |

## 4. Was das System NICHT macht

- **Es kauft und verkauft keine Aktien** und hat keinen Zugang zu Trade Republic, einem Broker oder Bankkonto.
- **Es gibt keine Anlageberatung** und sagt keine Kurse voraus. Die Verkaufs-Hinweise wenden nur die eigenen, vorher festgelegten Regeln an (Stop, Ziel, Gewinn sichern, Schlusszeit).
- Eine gute Vergangenheit (Gewinnwachstum, Ausbruch) **garantiert nicht**, dass eine Aktie steigt.

## 5. Trade Republic: Was anders ist

- **Euro statt Dollar:** Die Euro-Werte in den Nachrichten sind mit dem Tageskurs umgerechnet und nur ein Richtwert. In der App gilt der Kurs, den Trade Republic anzeigt. Den Stop-Loss in Euro dort prüfen.
- **Andere Börse:** Trade Republic handelt US-Aktien über eine deutsche Börse (LS Exchange). Die Kurse folgen New York, können aber leicht abweichen.
- **Suche:** In der App nach dem Firmennamen suchen.

## 6. Daten, Kosten, Datenschutz

- **Kosten:** keine. GitHub Actions ist für öffentliche Repositories kostenlos. ntfy, Yahoo und SEC sind ebenfalls kostenlos.
- **Was öffentlich sichtbar ist:** nur der Programmcode, diese Erklärung und die Regeln (zum Beispiel „20 € Maximalverlust“). Kein Name, keine E-Mail, kein ntfy-Thema, kein Kontostand und keine Käufe.
- **Weitergegebene persönliche Daten:** Name und E-Mail gehen als Kontaktangabe an die SEC, weil die SEC das für automatische Abfragen verlangt. Sie liegen als Secret bei GitHub, nicht im Code.
- **ntfy:** Die Nachrichten laufen über den öffentlichen Server ntfy.sh. Sie enthalten nur Börsendaten, nichts Persönliches.
- **Datenqualität:** Kurse von Yahoo sind kostenlos und inoffiziell und können leicht verzögert sein.

## 7. Risiken beim Day Trading (ehrlich)

- Studien zeigen: **Die meisten privaten Day Trader verlieren Geld.**
- Ein **Stop-Loss ist keine Garantie.** In schnellen Märkten wird oft unter dem Stop-Kurs verkauft.
- 3 Trades mit je 20 € Verlust kosten an einem schlechten Tag 60 €, also 6 % des Budgets.
- Zuerst mit kleinen Beträgen anfangen und nur Geld einsetzen, dessen Verlust nicht weh tut.

## 8. Abschalten oder Ändern

- **Pausieren:** Repository auf github.com → Actions → „Trading alerts“ → „…“ → **Disable workflow**.
- **Ganz löschen:** Settings → ganz unten „Delete this repository“.
- **Regeln ändern:** `trader/config.json` bearbeiten, dann in GitHub Desktop „Commit“ und „Push origin“ klicken.

## 9. Dateien

| Datei | Zweck |
|---|---|
| `alert.py` | Hauptprogramm: Nachrichten, Euro-Umrechnung, Stückzahl, Verkaufs-Prüfung jede Minute |
| `tools/scan.py` | Aktien-Scanner (Muster, Ausbrüche, Börsenkalender bis Ende 2027) |
| `tools/news.py` | Schlagzeilen, Firmenmeldungen und Kursbewegung |
| `growth.py` | Wachstums-Liste aus den SEC-Daten |
| `trader/tradable.txt` | Die rund 900 geprüften Aktien (S&P 500 und MidCap 400, alle bei Trade Republic) |
| `tools/indexlist.py` | Erstellt `trader/tradable.txt` neu aus den Wikipedia-Listen |
| `trader/config.json` | Regeln |
| `.github/workflows/alerts.yml` | Zeitplan für GitHub |
