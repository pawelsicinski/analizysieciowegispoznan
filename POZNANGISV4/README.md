# 🗺️ Poznań GIS — System Wspomagania Decyzji, Infrastruktury Krytycznej i Mobilności (v4.0)

Kompleksowy system **Inżynieryjnych Analiz Sieciowych GIS (Network Analysis)** dla **Miasta Poznania** (w oficjalnych granicach administracyjnych z zaciemnieniem obszaru zewnętrznego), wyposażony w **Panel Powiadomień i Log Zdarzeń (Event Log)**, **Suwak Czasu (Time-Slider `t = 1..60 min`)**, **Tryb Porównywania Scenariuszy (Split-Screen A vs B)**, **Inteligentnego Asystenta Doradczego AI dla Sztabu Kryzysowego**, dokładne torowiska tramwajowe z OpenStreetMap (`railway=tram`) oraz linie autobusowe ZTM Poznań wytrasowane po rzeczywistych ulicach miasta.

---

## 🌟 Nowości w Wersji 4.0 (Sztab Kryzysowy, Dynamika Czasowa, Split-Screen i ZTM)

### 1. 🔔 Panel Powiadomień i Log Zdarzeń (System Alarmowy / Event Log)
- Wysuwany panel po prawej stronie ekranu (otwierany przyciskiem **`🔔 Log`** w pasku górnym).
- Automatycznie rejestruje każde wywołane zdarzenie kryzysowe wraz z sygnaturą czasową, poziomem priorytetu (`CRITICAL`, `WARNING`, `INFO`) i szczegółowym podsumowaniem (np. *`10:42:15 — Awaria magistrali Chwaliszewo (DN300), odciętych 34 budynki | Zasuwy: WZ-13, WZ-14`*).
- Przycisk **`🔄 Przywróć ten scenariusz`** przy każdym wpisie pozwala jednym kliknięciem wrócić do dowolnego wcześniejszego incydentu i porównać skutki awarii w różnych częściach Poznania.

### 2. ⏱️ Animacja i Propagacja Zjawisk w Czasie (Time-Slider `t = 1..60 min`)
- Interaktywny suwak czasu oraz przycisk **`▶️ Animuj czas`** w dolnym doku:
  - **Wodociągi (`💧 Wodociągi`)**: animuje spadek ciśnienia w rurach od **4.8 bar do 0.0 bar**, narastającą objętość wycieku wody (`m³`) oraz propagację strefy odcięcia po zamknięciu zasuw (np. `WZ-13` i `WZ-14`).
  - **Energetyka (`⚡ Prąd Enea`)**: animuje kaskadowy zanik napięcia oraz załączenie automatyki **SZR** w pierwszych minutach po zwarciu.
  - **Kanalizacja (`🌧️ Ulewa`)**: pokazuje postępującą falę spływu nawalnego i przeciążenie kolektorów w kolejnych minutach burzy.
  - **Transport i ZTM (`🚗 Trasa`, `🚧 Objazdy`, `🚋 Multimodal`, `🚚 TSP`)**: animuje w czasie rzeczywistym ruch pojazdu / karetki / tramwaju wzdłuż wyznaczonej trasy na mapie Poznania.
  - **Izochrony (`🟣 Izochrony`)**: dynamicznie rozszerza zasięg izochrony minuta po minucie.

### 3. 🔀 Multi-Scenariuszowość / Porównywanie Awarii (Split-Screen A vs B)
- Przycisk **`🔀 A vs B`** włącza **Tryb Podzielonego Ekranu (Dual Synchronized Maps)**:
  - **Lewa mapa (Scenariusz A)**: pełna interaktywna analiza sterowana z głównego panelu.
  - **Prawa mapa (Scenariusz B)**: niezależny scenariusz porównawczy z natychmiastowym zestawieniem delty skutków (np. *Awaria prądu GPZ Grunwald vs GPZ Wilda*, *Pęknięcie magistrali Chwaliszewo vs Jeżyce*, *Zamknięcie Mostu Św. Rocha vs Mostu Lecha*, *Ekstremalna ulewa 200%*).
  - Obie mapy są zsynchronizowane przestrzennie (przesunięcie lub przybliżenie jednej mapy automatycznie ustawia ten sam kadr na drugiej mapie).

### 4. 🤖 Inteligentne Podpowiedzi AI / Asystent Doradczy dla Sztabu Kryzysowego
- W panelu wyników każdej z 12 analiz znajduje się sekcja **`🤖 Rekomendacja AI dla Sztabu Kryzysowego`**, która na podstawie bieżącej analizy grafowej generuje konkretne kroki operacyjne:
  - Wskazuje dokładne oznaczenia zasuw do zamknięcia (np. *„Skierować brygadę pogotowia wodociągowego Aquanet do zasuw **WZ-13** i **WZ-14**”*).
  - Rekomenduje podstawienie **beczkowozu** przy odcięciu większej liczby budynków (np. **34 budynki** w rejonie ul. Chwaliszewo / Śródka).
  - Podaje zalecenia dla dyspozytorów Enea Operator (przełączenia SZR 15 kV), Zarządu Dróg Miejskich (tablice VMS na obwodnicach) oraz dyspozytorni medycznej.

### 5. 🚋🚌 Dokładna Geometria Tras Tramwajowych OSM (`railway=tram`) i Autobusowych ZTM Poznań
- **Tramwaje ZTM Poznań**: pobrane bezpośrednio z bazy **OpenStreetMap (`railway=tram`)** — ponad **800 odcinków torowisk** w granicach Poznania (Trasa Kórnicka, PST, Naramowice, Górczyn, Miłostowo, Junikowo, Franowo, Starołęka, Ogrody, Piątkowska) i wytrasowane topologicznie po grafie szyn.
- **Autobusy ZTM Poznań**: **27 połączeń autobusowych** (linie `151, 163, 164, 168, 169, 174, 176, 179, 182, 184, 191, 193, 198`) wytrasowanych wierzchołek po wierzchołku po prawdziwych ulicach Poznania (m.in. na **Lotnisko Ławica**, **Kampus UAM Morasko**, **Podolany**, **Szczepankowo**, **Antoninek**, **Termy Maltańskie / Nowe Zoo**), w pełni włączonych w router **Multimodalny (Rower/Pieszo + Autobus + Tramwaj)**.

### 6. 🌑 Zaciemnienie Obszaru Poza Granicami Poznania
- Zewnętrzna maska poligonowa z wyciętym otworem (*hole*) w kształcie oficjalnej granicy administracyjnej Miasta Poznania estetycznie przyciemnia tereny podmiejskie i skupia wzrok na obszarze miasta (intensywność maski można regulować lub wyłączyć w **`⚙️ Ustawienia`**).

---

## 🚀 Jak uruchomić projekt?

```bash
python analiza_sieciowa.py
```

Skrypt wczyta lokalnie zapisane bazy danych przestrzennych Poznania (`osm_poznan_full_roads.json`, `poznan_boundary.json`, `poznan_tram_osm.json`), wygeneruje plik **`index.html`** i automatycznie otworzy go w przeglądarce.
