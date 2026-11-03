# Trading-Alarm: Was das System macht

Eine Erklärung zum Nachprüfen. Stand: 3. November 2026.

## Kurz gesagt

Ein kleines Programm läuft an jedem Börsentag automatisch auf einem Server von GitHub, für **europäische Aktien** (DAX, MDAX, Euro Stoxx 50, Börse 9:00–17:30) und für **US-Aktien** (S&P 500 und MidCap 400, Börse 15:30–22:00). Es liest **öffentliche, kostenlose** Börsen- und Firmendaten, filtert sie nach festen Regeln und schickt **Textnachrichten** aufs Handy: Übersichten am Morgen und Nachmittag, Kauf-Pläne mit Stückzahl, Stop-Loss und Ziel, und Verkaufs-Hinweise nach dem eigenen Plan, **jede Minute geprüft**. Dazu kommen neue Aktien-Meldungen von Nancy Pelosi.

**Das Programm kauft und verkauft nichts.** Es ist mit keinem Broker (auch nicht mit Trade Republic) und keinem Bankkonto verbunden, hat keinen Zugriff auf Geld und kennt keine Passwörter. Gekauft und verkauft wird nur von Hand in der Trade-Republic-App.

**Der Laptop muss nicht eingeschaltet sein.** Alles läuft in der Cloud bei GitHub.

---

## 1. Wie es technisch funktioniert

```
GitHub (Cloud-Server, kostenlos)
   │  startet das Programm automatisch zu festen Zeiten
   ▼
alert.py (USA)    ──liest──►  Yahoo Finance   (Kurse, Schlagzeilen, Euro-Dollar-Kurs)
europe.py (Europa)──liest──►  Nasdaq Trader   (Liste aller US-Aktien)
                  ──liest──►  SEC / EDGAR     (offizielle Firmenmeldungen: Gewinn, Umsatz)
                  ──liest──►  US-Repräsentantenhaus (Aktien-Meldungen von Nancy Pelosi)
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

### Europa (DAX, MDAX, Euro Stoxx 50), Preise direkt in Euro
- **ca. 7:20 „Europa Vorbörse“** (Trade Republic öffnet um 7:30): rund 120 große europäische Firmen nach Mustern vom Vortag geprüft, 6 zum Beobachten, mit Branche, Land und News. Die Kauf-Hinweise kommen erst nach 9:00, weil vorher wenig gehandelt wird und die Preise oft ungünstig sind.
- **ca. 9:20 „Europa Eröffnung“:** Aktien, die über ihren Höchstkurs der ersten 15 Minuten (9:00–9:15) gestiegen sind. Mit Euro-Betrag pro Konto, Stop-Loss, Ziel und Zeitmaschine, genau wie bei den US-Aktien unten.
- **bis 17:30:** Verkaufs-Hinweise jede Minute, um ca. 17:00 die Erinnerung „Bis 17:25 Uhr alles verkaufen“.
- An Xetra-Feiertagen (z. B. 24., 25. und 31. Dezember, Karfreitag, Ostermontag, 1. Mai) kommt nichts.

### USA
Uhrzeiten in österreichischer Zeit (Winter und Sommer gleich). In den zwei Wochen, in denen nur die USA die Uhr umgestellt haben (zum Beispiel 15. bis 26. März 2027), kommt alles eine Stunde früher. GitHub startet die Läufe oft 5 bis 60 Minuten später. Das Programm wartet dann bzw. holt nach, und die Nachmittags-Prüfung startet direkt nach der Vormittags-Prüfung. An Wochenenden und US-Feiertagen kommt nichts.

Alle Preise stehen **in Dollar und umgerechnet in Euro** (Tageskurs Euro/Dollar). Jede Nachricht erklärt am Anfang, was sie bedeutet, und sagt Schritt für Schritt, was in Trade Republic zu tun ist.

### a) ca. 14:15: „Vorbörse“ (Day Trading, zum Beobachten)
- Prüft **rund 900 große US-Firmen** (S&P 500 und S&P MidCap 400) anhand der Schlusskurse. Nur diese, weil es sie sicher bei Trade Republic gibt. Das gilt auch für die Ausbrüche und die Wachstums-Liste.
- Behält Aktien **ab 6 € pro Stück** (ohne Obergrenze, weil nach Euro-Betrag gekauft wird), die viel gehandelt werden (mehr als 1 Million Stück am Tag).
- Sucht **Muster**, zum Beispiel „höchster Schlusskurs seit 20 Tagen“ oder „stark gefallen“ (RSI unter 30).
- Schickt **6 Aktien** mit Kurs, Tagesbewegung und einer Schlagzeile, in der die Firma vorkommt. Das ist noch kein Kaufsignal.

### b) ca. 14:20: „Wachstums-Liste“ (langfristig)
- Liest die **offiziellen Pflichtmeldungen aller US-Firmen bei der SEC** (US-Börsenaufsicht).
- Behält Firmen, deren **Gewinn 6 Jahre in Folge positiv** war, in **mindestens 4 von 5 Jahren gestiegen** ist, um **mindestens 10 % pro Jahr** gewachsen ist, deren Umsatz um mindestens 5 % pro Jahr gewachsen ist und die **mindestens 1 Mrd. $ Umsatz** haben.
- Schickt die **10 mit dem stärksten und stetigsten Wachstum** ab 6 € pro Aktie, mit letztem Quartal, nächstem Zahlen-Termin und Schlagzeile.

### c) ca. 15:55: „Eröffnung“ (Day Trading)
- Die Börse öffnet um 15:30. Die ersten 15 Minuten ergeben eine **Spanne** (höchster und tiefster Kurs).
- Gemeldet werden Aktien, die danach **über diese Spanne gestiegen** sind („Ausbruch“).
- Immer **5 Aktien** (passen weniger zu den Regeln, steht das da). Alle 11 Werkzeuge prüfen im Hintergrund mit; in der Nachricht steht nur, wenn etwas auffällt (z. B. eine wichtige Pflichtmeldung oder ein Pelosi-Handel).
- Zu jeder Aktie:
  - **„Die Firma“**: ein Satz aus der deutschen Wikipedia, was die Firma macht
  - **„Warum sie sich bewegt“**: die Gründe aus den aktuellen Schlagzeilen, auf Deutsch zusammengefasst (z. B. „Zahlen besser als erwartet“, „Kursziel erhöht“, „Übernahme“), dazu die Schlagzeilen selbst auf Englisch. Die Gründe werden aus Stichwörtern erkannt und können danebenliegen; die Schlagzeile zum Nachlesen steht immer dabei.
  - **„1 Aktie kostet“**: der aktuelle Preis für ein Stück
  - **„Kaufen für“: der Euro-Betrag für jedes Konto**, keine Stückzahl. Der Betrag ist so gewählt, dass beim Stop höchstens der eingestellte Maximalverlust dieses Kontos verloren geht (inkl. 2 € Gebühren) und höchstens 75 % des Kontos in einer Aktie stecken. Kontostände und Verlust-Grenzen pro Konto stehen nur im geheimen GitHub-Secret `ACCOUNTS`, nicht in diesem öffentlichen Repository; in den Handy-Nachrichten stehen sie dabei.
  - **Stop-Loss** = Mitte der Spanne. Das ist der Kurs, zu dem man aussteigt, wenn es schiefgeht. Man soll ihn **sofort in Trade Republic setzen**.
  - **Ziel** = Einstieg + 2 × Abstand zum Stop (Gewinn doppelt so groß wie das Risiko)
  - **Höchstmöglicher Verlust und Gewinn beim Ziel**, für beide Konten, nach Gebühren. Wo die Gebühren den Gewinn auffressen, steht ein Hinweis.
  - **Zeitmaschine** (aus dem Time-Machine-Werkzeug): Der Rest des Tages wird 3.000-mal mit den echten 5-Minuten-Kursbewegungen der Aktie aus den letzten rund 60 Börsentagen nachgespielt. Ergebnis: wie oft zuerst das Ziel, wie oft zuerst der Stop, wie oft keins von beiden bis Schluss. Dazu der echte Rückblick: wie dieselbe Regel an jedem dieser Tage wirklich ausgegangen ist. Weil das Ziel doppelt so weit weg ist wie der Stop, reicht es, wenn das Ziel öfter als in 1 von 3 Fällen zuerst kommt. Das ist ein Rückblick, **keine Vorhersage**.
  - Die **Branche** auf Deutsch (z. B. „Software“, „Banken“)
  - **Sortierung nach Auszahlung:** Die Liste ist danach sortiert, welche Aktie sich in der Probe im Schnitt am meisten ausgezahlt hat (Euro pro Trade nach Gebühren). Die beste steht mit ★ oben. Bringt laut Probe keine einen Gewinn, steht das ehrlich da.
- Darunter die Erklärung, warum das Sicherheitsnetz (Stop-Loss) wichtig ist, wann welche Verkaufs-Nachricht kommt, und die Tagesregeln (höchstens 3 Käufe und der Tages-Maximalverlust pro Konto, Europa und USA zusammen). Eine Kauf-Anleitung gibt es nicht mehr.
- Lange Nachrichten kommen in zwei Teilen („(1/2)“, „(2/2)“), weil eine Push-Nachricht nur rund 4.000 Zeichen fasst.

### Pelosi-Meldungen (bei der Vorbörse um ca. 14:15, nur wenn es etwas Neues gibt)
- US-Abgeordnete müssen ihre Aktiengeschäfte veröffentlichen (STOCK Act). Das Programm liest die Meldungen von **Nancy Pelosi** beim US-Repräsentantenhaus.
- Neue Meldung → eine Nachricht: was gekauft oder verkauft wurde, wie viele Aktien oder Optionen, Wertspanne, Handelstag, Link zum Original.
- Gemeldet wird bis zu 45 Tage nach dem Handel. Das ist **kein Signal zum Nachmachen**.

### d) Verkaufs-Hinweise (von ca. 15:55 bis 22:00, jede Minute geprüft)
Die Aktien aus der 15:55-Nachricht bleiben **bis Handelsschluss unter Beobachtung**. **Jede Minute** prüft das Programm die Kurse (Minuten-Kerzen, Hoch und Tief, dazu der aktuelle Kurs):
- **„VERKAUFEN: … Stop erreicht“**: Der Kurs ist auf den Stop gefallen. Raus, damit der Verlust klein bleibt.
- **„VERKAUFEN: … Ziel erreicht“**: Der geplante Gewinn (2 × Risiko) ist da.
- **„VERKAUFEN: … Gewinn sichern“**: Die Aktie war schon mindestens **1 × Risiko im Plus** und fällt danach so weit zurück, dass **die Hälfte des besten Gewinns wieder weg ist**. So wird aus einem Gewinn kein Verlust. Beispiel: Einstieg 100 $, Stop 98 $, Hoch 102,60 $, Rückfall unter 101,30 $ → Hinweis.
- **ca. 21:30: „Bis 21:55 Uhr alles verkaufen“**: Erinnerung, dass beim Day Trading nichts über Nacht gehalten wird, mit dem Stand jeder offenen Aktie.

Jeder Hinweis erklärt, **was passiert ist**, **warum verkaufen**, das **aktuelle Ergebnis in Euro** und **was in Trade Republic zu tun ist**. Er kommt nur **einmal**. Das Programm weiß nicht, was tatsächlich gekauft wurde: Die Hinweise gelten nur für Aktien, die man an dem Tag wirklich gekauft hat.

**Wie schnell?** In der Regel **1 bis 3 Minuten** nach dem Ereignis. Trotzdem gilt: **Der Stop-Loss in Trade Republic ist schneller** (er verkauft sofort und automatisch) und sollte immer gesetzt sein. Die Nachricht ist die zweite Absicherung.

## 2b. Die 11 Werkzeuge (Skills) und was sie hier machen

| Werkzeug | Was es macht | Im automatischen System |
|---|---|---|
| **Chart Scanner** (trader-scanner) | Durchsucht rund 1.000 Aktien nach Mustern und Ausbrüchen | aktiv: Vorbörse und Eröffnung |
| **News Monitor** (trader-news) | Schlagzeilen und Pflichtmeldungen zu jeder Aktie | aktiv: „In den News“ und die „Achtung“-Warnung |
| **Entry Timer** (trader-entry) | Erkennt den Moment, in dem eine Aktie über ihre erste Viertelstunde steigt | aktiv: „Was passiert ist“ |
| **Exit Planner** (trader-exit) | Legt Sicherheitsnetz (Mitte der ersten Viertelstunde) und Gewinnziel (2 × Risiko) fest | aktiv |
| **Budget Manager** (trader-budget) | Rechnet den Euro-Betrag pro Konto: höchstens der Maximalverlust des Kontos inkl. Gebühren, höchstens 75 % des Kontos pro Aktie | aktiv |
| **Risk Manager** (trader-risk) | Prüft die Regeln: ab 6 € pro Aktie, Maximalverlust pro Trade und pro Tag je Konto, 3 Trades | teilweise: Preis und Verlust pro Trade automatisch. Tageslimit und 3 Trades selbst zählen, weil das System die Käufe nicht sieht |
| **Price Alerts** (trader-alerts) | Prüft jede Minute die Kurse und schickt die Verkaufs-Nachrichten | aktiv |
| **Time Machine** (trader-timemachine) | Spielt den Tag 3.000-mal nach, zeigt den echten Rückblick und sortiert nach Auszahlung | aktiv |
| **Earnings Analyst** (trader-analyst) | Liest die Firmenzahlen bei der US-Börsenaufsicht SEC (Umsatz, Gewinn, Quartale) | aktiv: Wachstums-Liste (nur USA, für Europa gibt es keine kostenlose Quelle) |
| **Pelosi Tracker** (trader-pelosi) | Meldet neue Aktiengeschäfte von Nancy Pelosi | aktiv |
| **Order Executor** (trader-broker) | Würde Kauf- und Verkaufsaufträge an einen Broker schicken | **nicht möglich**: Trade Republic hat keinen Zugang für Programme. Gekauft und verkauft wird immer von Hand |

## 3. Die eingestellten Regeln (trader/config.json)

| Regel | Wert |
|---|---|
| Broker | Trade Republic (von Hand) |
| Konten | im Secret `ACCOUNTS` (Format `Kontostand:Verlust pro Trade:Verlust pro Tag`, mehrere Konten mit Komma); jedes Konto wird in jeder Nachricht berechnet |
| Höchster Verlust pro Trade, inkl. Gebühren | pro Konto im Secret; ohne Secret 20 € |
| Gewinn beim Ziel | etwa doppelt so viel wie der Verlust beim Stop |
| Gebühr pro Order | 1 € (Kauf + Verkauf = 2 €) |
| Höchster Verlust pro Tag | pro Konto im Secret; ohne Secret 100 € |
| Höchstens Trades pro Tag | 3 |
| Preis pro Aktie | ab 6 €, keine Obergrenze (Kauf nach Euro-Betrag) |
| Größte Position | 75 % des Kontos |
| Ziel | 2 × Risiko |
| Gewinn sichern | ab +1 × Risiko, Hinweis wenn die Hälfte des besten Gewinns weg ist |
| Spätestens verkaufen | USA 21:55, Europa 17:25 (5 Minuten vor Börsenschluss) |
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
- 3 Trades, die alle beim Stop enden, kosten an einem schlechten Tag das Dreifache des Maximalverlusts pro Trade.
- Bei einem großen Konto stecken bei engem Stop mehrere Tausend Euro in einer Aktie. Springt der Kurs plötzlich (z. B. nach einer Nachricht) unter den Stop, kann der Verlust größer als der eingestellte Maximalverlust sein.
- Zuerst mit kleinen Beträgen anfangen und nur Geld einsetzen, dessen Verlust nicht weh tut.

## 8. Abschalten oder Ändern

- **Pausieren:** Repository auf github.com → Actions → „Trading alerts“ → „…“ → **Disable workflow**.
- **Ganz löschen:** Settings → ganz unten „Delete this repository“.
- **Regeln ändern:** `trader/config.json` bearbeiten, dann in GitHub Desktop „Commit“ und „Push origin“ klicken.

## 9. Dateien

| Datei | Zweck |
|---|---|
| `alert.py` | Hauptprogramm: Nachrichten, Euro-Umrechnung, Euro-Betrag pro Konto, Verkaufs-Prüfung jede Minute |
| `tools/scan.py` | Aktien-Scanner (Muster, Ausbrüche, Börsenkalender bis Ende 2027) |
| `tools/news.py` | Schlagzeilen, Firmenmeldungen und Kursbewegung |
| `growth.py` | Wachstums-Liste aus den SEC-Daten |
| `europe.py` | Europa: Vorbörse, Ausbrüche, Verkaufs-Prüfung (Xetra-Zeiten) |
| `tools/timemachine.py`, `tools/tmachine.py` | Zeitmaschine (Probeläufe und Rückblick) für USA und Europa |
| `tools/ptr.py` | Liest die Pelosi-Meldungen |
| `trader/europe.txt` | Die rund 120 europäischen Aktien (DAX, MDAX, Euro Stoxx 50) |
| `.github/workflows/alerts-eu.yml` | Zeitplan für Europa |
| `trader/tradable.txt` | Die rund 900 geprüften Aktien (S&P 500 und MidCap 400, alle bei Trade Republic) |
| `tools/indexlist.py` | Erstellt `trader/tradable.txt` neu aus den Wikipedia-Listen |
| `trader/config.json` | Regeln |
| `.github/workflows/alerts.yml` | Zeitplan für GitHub |
