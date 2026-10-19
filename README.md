# 🌐 Analizy Sieciowe w GIS — Poznań (`NetworkX` + `Folium`)

**Autor:** Paweł Siciński

Projekt stworzony na potrzeby prezentacji **„Analizy sieciowe w GIS”**. Pokazuje w praktyce, jak w języku Python przeprowadza się analizy przestrzenne na **sieciach transportowych** (graf nieskierowany `nx.Graph`) oraz **sieciach infrastrukturalnych** (graf skierowany `nx.DiGraph`), a następnie wizualizuje wyniki jako interaktywną aplikację webową na mapie **Poznania** (`Folium` / `Leaflet.js`).

---

## 🚀 Jak uruchomić program?

### Sposób 1: Przez GitHub Pages (w przeglądarce na dowolnym komputerze)
Po włączeniu **GitHub Pages** w ustawieniach repozytorium (*Settings -> Pages -> Branch: main*), plik [`index.html`](./index.html) działa jako gotowa, interaktywna aplikacja webowa pod linkiem:
`https://<twoj-login>.github.io/<nazwa-repozytorium>/`

### Sposób 2: Uruchomienie skryptu Python lokalnie lub w GitHub Codespaces
1. Zainstaluj biblioteki:
   ```bash
   pip install -r requirements.txt
   ```
2. Uruchom skrypt:
   ```bash
   python analiza_sieciowa.py
   ```
   Skrypt wykona obliczenia w konsoli i wygeneruje interaktywną mapę [`index.html`](./index.html).

---

## 🧠 Co dokładnie liczy ten program? (Powiązanie ze slajdami prezentacji)

1. **Założenia bazy danych (Slajd 2):**
   - **Dane wektorowe:** Węzły (`nodes`) ze współrzędnymi GPS Poznania `(lat, lon)` oraz krawędzie (`edges`) reprezentujące odcinki ulic i rurociągów.
   - **Topologia:** Połączenia węzłowo-krawędziowe w bibliotece `NetworkX`.
   - **Atrybuty (wagi):** Każdy odcinek drogi ma przypisaną długość (`dist_km`) oraz czas przejazdu z uwzględnieniem korków (`time_min`), a każdy węzeł kanalizacyjny – wysokość n.p.m. (`elev_m`).

2. **Analizy sieci transportowej Poznania (Slajdy 5–6):**
   - **Najkrótsza vs. Najszybsza trasa (`nx.shortest_path` – Algorytm Dijkstry):** Porównanie przejazdu ze *Szpitala Klinicznego (ul. Przybyszewskiego)* do *Szpitala przy ul. Szwajcarskiej*:
     - **Trasa najkrótsza (waga = km):** Przez Rondo Kaponiera, Centrum i Most św. Rocha (`8.5 km`, ale aż `31.0 min` przez korki).
     - **Trasa najszybsza (waga = czas):** II Ramą Komunikacyjną – ul. Hetmańską przez Most Przemysła I (`10.4 km`, ale tylko `17.0 min` – **zysk 14.0 min!**).
   - **Symulacja remontu / awarii drogi (`G.remove_edge`):** Zamknięcie Mostu Przemysła I i automatyczne przeliczenie objazdu przez Most św. Rocha (`24.5 min`).
   - **Obszar dostępności / Izochrona karetki (`nx.ego_graph`):** Wyznaczenie wszystkich węzłów osiągalnych po drogach w maksymalnie 10 minut ze Szpitala przy ul. Przybyszewskiego.

3. **Analizy sieci infrastrukturalnej Poznania – Kanalizacja i Wodociągi (Slajdy 7–8):**
   - **Śledzenie „w górę sieci” (*Upstream Trace* – `nx.ancestors`):** Ścieki spływają grawitacyjnie z wyżej położonych dzielnic (Górczyn, Grunwald, Jeżyce ~85 m n.p.m.) ku dolinie Warty do Centralnej Oczyszczalni Ścieków w Koziegłowach (~54 m n.p.m.). Po wykryciu toksyn w studzience pomiarowej `S-2 (Garbary)`, algorytm bada graf „pod prąd” i wskazuje podejrzane fabryki z lewego brzegu Warty, wykluczając zakład z Antoninka.
   - **Symulacja ulewy (wąskie gardło) i Analiza odcięcia zaworu:** Wykrycie odcinka na Jeżycach (`DN250`), gdzie przepływ deszczówki przekracza przepustowość (`295 l/s > 150 l/s` -> wybicie studzienki `S-1`) oraz wskazanie zasuwy `Z-2 (ul. Reymonta)`, którą należy zamknąć w razie pęknięcia magistrali.
