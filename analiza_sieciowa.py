"""
================================================================================
ANALIZY SIECIOWE W GIS - DEMONSTRATOR PYTHON: POZNAŃ (NetworkX + Folium)
Autor: Paweł Siciński
================================================================================
Program prezentuje praktyczne działanie analiz sieciowych opisanych w prezentacji
na rzeczywistych współrzędnych geograficznych miasta POZNANIA:

1. ZAŁOŻENIA BAZY DANYCH (Slajd 2):
   - Dane wektorowe: węzły (nodes) ze współrzędnymi GPS Poznania oraz krawędzie (edges).
   - Topologia: spójny graf połączeń (NetworkX).
   - Atrybuty (wagi): długość odcinka [km], czas przejazdu [min], rzędna [m n.p.m.].

2. SIEĆ TRANSPORTOWA POZNANIA (Slajdy 5-6) - Graf nieskierowany (nx.Graph):
   - Najkrótsza trasa (kryterium: dystans w km przez Rondo Kaponiera i Centrum)
     vs Najszybsza trasa (kryterium: czas w min II Ramą - ul. Hetmańską).
   - Symulacja zamknięcia drogi (remont Mostu Przemysła I) i wyznaczenie objazdu.
   - Obszar dostępności (Izochrona): gdzie dojedzie karetka w 10 minut ze Szpitala
     Klinicznego przy ul. Przybyszewskiego.

3. SIEĆ INFRASTRUKTURALNA POZNANIA (Slajdy 7-8) - Graf skierowany (nx.DiGraph):
   - Sieć kanalizacyjna (grawitacyjna): spływ z wyżej położonych dzielnic (Górczyn,
     Grunwald, Jeżyce ~85 m n.p.m.) ku dolinie Warty do Centralnej Oczyszczalni
     Ścieków (COŚ Koziegłowy ~54 m n.p.m.).
   - Śledzenie "w górę sieci" (Upstream Trace): wykrycie fabryki, z której spłynęły
     toksyczne ścieki do Kolektora Lewobrzeżnego przy Garbarach.
   - Symulacja ulewy (wykrycie zbyt wąskiej rury na Jeżycach, gdzie wybije studzienka).
   - Analiza awarii: wskazanie zasuwy odcinającej Z-2, którą należy zakręcić.

Uruchomienie:
    python analiza_sieciowa.py
================================================================================
"""

import json
import os
import sys
import networkx as nx
import folium


# ==============================================================================
# 1. BAZA DANYCH: SIEĆ TRANSPORTOWA POZNANIA (WĘZŁY I KRAWĘDZIE Z WAGAMI)
# ==============================================================================
TRANSPORT_NODES = {
    "Szpital_Grunwald":     {"name": "Szpital Kliniczny (ul. Przybyszewskiego - START)", "coords": [52.4055, 16.8845], "type": "start"},
    "Ogrody":               {"name": "Węzeł Ogrody (ul. Dąbrowskiego)",                  "coords": [52.4165, 16.8790], "type": "junction"},
    "Niestachowska":        {"name": "Węzeł Niestachowska / św. Wawrzyńca",              "coords": [52.4235, 16.8950], "type": "highway"},
    "Rondo_Kaponiera":      {"name": "Rondo Kaponiera (Centrum - Korki!)",               "coords": [52.4078, 16.9125], "type": "center"},
    "Stary_Rynek_Garbary":  {"name": "Centrum / Pl. Wolności - Garbary (Korki!)",        "coords": [52.4068, 16.9355], "type": "center"},
    "Most_Rocha":           {"name": "Most św. Rocha",                                   "coords": [52.4025, 16.9465], "type": "center"},
    "Rondo_Srodka":         {"name": "Rondo Śródka",                                     "coords": [52.4105, 16.9555], "type": "junction"},
    "Rondo_Rataje":         {"name": "Rondo Rataje",                                     "coords": [52.3965, 16.9505], "type": "junction"},
    "Glogowska_Hetmanska":  {"name": "Skrzyżowanie Głogowska / Hetmańska",               "coords": [52.3885, 16.8985], "type": "highway"},
    "Górczyn":              {"name": "Węzeł Górczyn",                                    "coords": [52.3785, 16.8810], "type": "junction"},
    "Wilda_Hetmanska":      {"name": "Wilda (ul. Hetmańska / Rolna)",                    "coords": [52.3845, 16.9200], "type": "highway"},
    "Rondo_Staroleka":      {"name": "Rondo Starołęka (II Rama)",                        "coords": [52.3805, 16.9380], "type": "highway"},
    "Most_Przemysla":       {"name": "Most Przemysła I (ul. Hetmańska)",                 "coords": [52.3855, 16.9445], "type": "highway"},
    "Krzywoustego_Chartowo":{"name": "ul. Krzywoustego / Chartowo",                      "coords": [52.3920, 16.9740], "type": "highway"},
    "Nowe_Zawady":          {"name": "Węzeł Zawady / Podwale",                           "coords": [52.4200, 16.9530], "type": "junction"},
    "Szpital_Szwajcarska":  {"name": "Szpital Miejski (ul. Szwajcarska - CEL)",          "coords": [52.3885, 16.9945], "type": "target"},
}

# Krawędzie (ulice Poznania) z dwoma kluczowymi atrybutami (wagami):
# - dist_km: długość geometryczna w kilometrach
# - time_min: rzeczywisty czas przejazdu w minutach (uwzględniający korki i sygnalizację)
TRANSPORT_EDGES = [
    # Trasa 1: Przez Centrum (Rondo Kaponiera -> Most św. Rocha) - NAJKRÓTSZA w km, ale wolna przez korki!
    ("Szpital_Grunwald",      "Rondo_Kaponiera",       {"dist_km": 2.0, "time_min": 7.5, "road": "ul. Bukowska (Korek)"}),
    ("Rondo_Kaponiera",       "Stary_Rynek_Garbary",   {"dist_km": 1.6, "time_min": 9.0, "road": "ul. Św. Marcin / Al. Marcinkowskiego (Korek)"}),
    ("Stary_Rynek_Garbary",   "Most_Rocha",            {"dist_km": 0.9, "time_min": 4.5, "road": "ul. Mostowa / Most św. Rocha"}),
    ("Most_Rocha",            "Rondo_Rataje",          {"dist_km": 0.8, "time_min": 4.0, "road": "ul. Kórnicka / Jana Pawła II"}),
    ("Rondo_Rataje",          "Krzywoustego_Chartowo", {"dist_km": 1.7, "time_min": 3.5, "road": "ul. Bolesława Krzywoustego"}),
    ("Krzywoustego_Chartowo", "Szpital_Szwajcarska",   {"dist_km": 1.5, "time_min": 2.5, "road": "ul. Kurlandzka / Szwajcarska"}),

    # Trasa 2: II Ramą Komunikacyjną (ul. Reymonta -> ul. Hetmańska -> Most Przemysła I) - NAJSZYBSZA czasowo!
    ("Szpital_Grunwald",      "Glogowska_Hetmanska",   {"dist_km": 2.1, "time_min": 3.5, "road": "ul. Stanisława Przybyszewskiego / Reymonta"}),
    ("Glogowska_Hetmanska",   "Wilda_Hetmanska",       {"dist_km": 1.6, "time_min": 2.0, "road": "ul. Hetmańska (II Rama)"}),
    ("Wilda_Hetmanska",       "Rondo_Staroleka",       {"dist_km": 1.4, "time_min": 2.0, "road": "ul. Hetmańska"}),
    ("Rondo_Staroleka",       "Most_Przemysla",        {"dist_km": 0.8, "time_min": 1.5, "road": "Most Przemysła I (70 km/h)"}),
    ("Most_Przemysla",        "Rondo_Rataje",          {"dist_km": 1.3, "time_min": 2.0, "road": "ul. Hetmańska (Żegrze)"}),

    # Dodatkowe ulice w sieci Poznania (do izochrony karetki i wyznaczania objazdów)
    ("Szpital_Grunwald",      "Ogrody",                {"dist_km": 1.3, "time_min": 2.5, "road": "ul. Szpitalna"}),
    ("Ogrody",                "Niestachowska",         {"dist_km": 1.4, "time_min": 2.5, "road": "ul. Niestachowska"}),
    ("Ogrody",                "Rondo_Kaponiera",       {"dist_km": 2.4, "time_min": 6.5, "road": "ul. Dąbrowskiego"}),
    ("Glogowska_Hetmanska",   "Górczyn",               {"dist_km": 1.6, "time_min": 3.0, "road": "ul. Głogowska"}),
    ("Glogowska_Hetmanska",   "Rondo_Kaponiera",       {"dist_km": 2.3, "time_min": 6.5, "road": "ul. Głogowska (Targi)"}),
    ("Wilda_Hetmanska",       "Most_Rocha",            {"dist_km": 2.5, "time_min": 6.0, "road": "ul. Wierzbięcice / Królowej Jadwigi"}),
    ("Niestachowska",         "Nowe_Zawady",           {"dist_km": 4.2, "time_min": 7.0, "road": "Al. Armii Poznań / Most Mieszka I"}),
    ("Nowe_Zawady",           "Rondo_Srodka",          {"dist_km": 1.1, "time_min": 2.5, "road": "ul. Podwale"}),
    ("Stary_Rynek_Garbary",   "Rondo_Srodka",          {"dist_km": 1.5, "time_min": 4.0, "road": "Most Chrobrego"}),
    ("Rondo_Srodka",          "Rondo_Rataje",          {"dist_km": 1.6, "time_min": 3.0, "road": "ul. Jana Pawła II"}),
    ("Rondo_Srodka",          "Szpital_Szwajcarska",   {"dist_km": 3.6, "time_min": 6.5, "road": "ul. Warszawska / abpa Baraniaka"}),
]


# ==============================================================================
# 2. BAZA DANYCH: SIEĆ INFRASTRUKTURALNA POZNANIA (KANALIZACJA I WODOCIĄGI)
# ==============================================================================
# W sieci grawitacyjnej ścieki spływają z wyżej położonych dzielnic Poznania
# (Górczyn, Grunwald, Jeżyce ~80-86 m n.p.m.) ku dolinie Warty i na północ
# do Centralnej Oczyszczalni Ścieków (COŚ Koziegłowy ~54 m n.p.m.).
INFRA_NODES = {
    "Zaklad_Chemiczny_Gorczyn": {"name": "Zakład Chemiczny 'ChemPoz' (Górczyn - Źródło A)",     "coords": [52.3780, 16.8750], "elev_m": 86.0, "role": "factory"},
    "Fabryka_Farb_Jezyce":      {"name": "Fabryka Farb 'KolorPoznań' (Wola - Źródło B SKAŻENIE!)", "coords": [52.4180, 16.8820], "elev_m": 83.5, "role": "factory_polluter"},
    "Zaklad_Spozywczy_Wschod":  {"name": "Zakład Spożywczy 'EkoMalta' (Antoninek - Źródło C)", "coords": [52.4040, 17.0100], "elev_m": 79.0, "role": "factory"},
    "Osiedle_Grunwald":         {"name": "Osiedle Kopernika (Grunwald)",                       "coords": [52.3950, 16.8680], "elev_m": 84.0, "role": "housing"},
    "Zawor_Z2_Grunwald":        {"name": "Zasuwa Odcinająca Z-2 (ul. Reymonta)",               "coords": [52.3980, 16.8920], "elev_m": 79.5, "role": "valve"},
    "Zawor_Z1_Ogrody":          {"name": "Zasuwa Odcinająca Z-1 (Węzeł Ogrody)",               "coords": [52.4125, 16.8980], "elev_m": 75.5, "role": "valve"},
    "Studzienka_S1_Jezyce":     {"name": "Studzienka Zbiorcza S-1 (Jeżyce)",                   "coords": [52.4165, 16.9120], "elev_m": 70.0, "role": "manhole_narrow"},
    "Studzienka_S2_Garbary":    {"name": "Studzienka Pomiarowa S-2 (Garbary - Czujnik Toksyn!)","coords": [52.4175, 16.9380], "elev_m": 62.0, "role": "sensor_alert"},
    "Kolektor_Prawobrzezny":    {"name": "Kolektor Prawobrzeżny S-3 (Śródka / Zawady)",        "coords": [52.4160, 16.9680], "elev_m": 61.0, "role": "manhole"},
    "Przepompownia_Szelagowska":{"name": "Kolektor Nadrzeczny S-4 (ul. Szelągowska)",          "coords": [52.4330, 16.9550], "elev_m": 57.5, "role": "manhole"},
    "Oczyszczalnia_Kozieglowy": {"name": "Centralna Oczyszczalnia Ścieków Koziegłowy (Ujście)","coords": [52.4500, 16.9760], "elev_m": 54.0, "role": "sink"},
}

INFRA_EDGES = [
    ("Osiedle_Grunwald",          "Zawor_Z2_Grunwald",         {"diam_mm": 400, "cap_ls": 180, "storm_ls": 140, "pipe": "Kolektor Grunwaldzki DN400"}),
    ("Zaklad_Chemiczny_Gorczyn",  "Zawor_Z2_Grunwald",         {"diam_mm": 300, "cap_ls": 120, "storm_ls": 95,  "pipe": "Przyłącze Górczyn DN300"}),
    ("Zawor_Z2_Grunwald",         "Zawor_Z1_Ogrody",           {"diam_mm": 500, "cap_ls": 260, "storm_ls": 210, "pipe": "Magistrala Zachodnia DN500 (PĘKNIĘCIE!)"}),
    ("Fabryka_Farb_Jezyce",       "Zawor_Z1_Ogrody",           {"diam_mm": 300, "cap_ls": 110, "storm_ls": 90,  "pipe": "Przyłącze Wola/Ogrody DN300"}),
    ("Zawor_Z1_Ogrody",           "Studzienka_S1_Jezyce",      {"diam_mm": 250, "cap_ls": 150, "storm_ls": 295, "pipe": "Kolektor Jeżycki DN250 (ZA WĄSKA RURA!)"}),
    ("Studzienka_S1_Jezyce",      "Studzienka_S2_Garbary",     {"diam_mm": 600, "cap_ls": 400, "storm_ls": 310, "pipe": "Kolektor Bogdanki DN600"}),
    ("Studzienka_S2_Garbary",     "Przepompownia_Szelagowska", {"diam_mm": 800, "cap_ls": 650, "storm_ls": 490, "pipe": "Kolektor Lewobrzeżny Warty DN800"}),
    ("Zaklad_Spozywczy_Wschod",   "Kolektor_Prawobrzezny",     {"diam_mm": 400, "cap_ls": 200, "storm_ls": 150, "pipe": "Kolektor Maltański DN400"}),
    ("Kolektor_Prawobrzezny",     "Przepompownia_Szelagowska", {"diam_mm": 600, "cap_ls": 420, "storm_ls": 320, "pipe": "Syfon pod Wartą DN600"}),
    ("Przepompownia_Szelagowska", "Oczyszczalnia_Kozieglowy",  {"diam_mm": 1200,"cap_ls": 1200,"storm_ls": 850, "pipe": "Główny Kolektor Dopływowy COŚ Koziegłowy DN1200"}),
]


# ==============================================================================
# 3. SILNIK ANALIZ SIECIOWYCH (NetworkX)
# ==============================================================================
def run_network_analyses():
    print("=" * 78)
    print(" URUCHAMIANIE ANALIZ SIECIOWYCH W GIS - POZNAŃ (Python + NetworkX)")
    print("=" * 78)

    # --- A. Budowa grafu sieci transportowej Poznania (nx.Graph) ---
    G_trans = nx.Graph()
    for node_id, data in TRANSPORT_NODES.items():
        G_trans.add_node(node_id, **data)
    for u, v, attr in TRANSPORT_EDGES:
        G_trans.add_edge(u, v, **attr)

    start_node = "Szpital_Grunwald"
    target_node = "Szpital_Szwajcarska"

    # 1. Trasa NAJKRÓTSZA (waga: dist_km)
    shortest_path = nx.shortest_path(G_trans, source=start_node, target=target_node, weight="dist_km")
    shortest_dist = nx.path_weight(G_trans, shortest_path, weight="dist_km")
    shortest_time = nx.path_weight(G_trans, shortest_path, weight="time_min")

    # 2. Trasa NAJSZYBSZA (waga: time_min)
    fastest_path = nx.shortest_path(G_trans, source=start_node, target=target_node, weight="time_min")
    fastest_dist = nx.path_weight(G_trans, fastest_path, weight="dist_km")
    fastest_time = nx.path_weight(G_trans, fastest_path, weight="time_min")

    # 3. Symulacja AWARII / REMONTU (zamknięcie Mostu Przemysła I na ul. Hetmańskiej)
    G_detour = G_trans.copy()
    closed_edge = ("Rondo_Staroleka", "Most_Przemysla")
    G_detour.remove_edge(*closed_edge)
    detour_path = nx.shortest_path(G_detour, source=start_node, target=target_node, weight="time_min")
    detour_dist = nx.path_weight(G_detour, detour_path, weight="dist_km")
    detour_time = nx.path_weight(G_detour, detour_path, weight="time_min")

    # 4. Izochrona (Obszar dostępności karetki w 10 minut ze Szpitala przy Przybyszewskiego)
    isochrone_minutes = 10.0
    subgraph_iso = nx.ego_graph(G_trans, start_node, radius=isochrone_minutes, distance="time_min")
    iso_nodes = list(subgraph_iso.nodes())
    iso_edges = [[u, v] for u, v in subgraph_iso.edges()]

    print("\n[1] ANALIZA SIECI TRANSPORTOWEJ POZNANIA (Szpital Przybyszewskiego -> Szpital Szwajcarska):")
    print(f"  -> Trasa NAJKRÓTSZA (waga: km)  : {shortest_dist:.1f} km | czas: {shortest_time:.1f} min (przez Rondo Kaponiera i Centrum)")
    print(f"  -> Trasa NAJSZYBSZA (waga: min) : {fastest_dist:.1f} km | czas: {fastest_time:.1f} min (ul. Hetmańską - oszczędność {shortest_time - fastest_time:.1f} min!)")
    print(f"  -> Symulacja REMONTU (zamknięty Most Przemysła I): Nowy objazd = {detour_dist:.1f} km | czas: {detour_time:.1f} min")
    print(f"  -> Izochrona karetki ({isochrone_minutes:.0f} min)   : Obejmuje {len(iso_nodes)} węzłów sieci drogowej Poznania")

    # --- B. Budowa grafu skierowanego sieci infrastrukturalnej Poznania (nx.DiGraph) ---
    G_infra = nx.DiGraph()
    for node_id, data in INFRA_NODES.items():
        G_infra.add_node(node_id, **data)
    for u, v, attr in INFRA_EDGES:
        G_infra.add_edge(u, v, **attr)

    # 1. Śledzenie "w górę sieci" (Upstream Trace) od studzienki z czujnikiem S-2 (Garbary)
    sensor_node = "Studzienka_S2_Garbary"
    upstream_nodes = list(nx.ancestors(G_infra, sensor_node)) + [sensor_node]
    upstream_subgraph = G_infra.subgraph(upstream_nodes)
    upstream_edges = [[u, v] for u, v in upstream_subgraph.edges()]

    suspect_factories = [
        INFRA_NODES[n]["name"] for n in upstream_nodes
        if "factory" in INFRA_NODES[n]["role"]
    ]
    innocent_factories = [
        INFRA_NODES[n]["name"] for n in INFRA_NODES
        if "factory" in INFRA_NODES[n]["role"] and n not in upstream_nodes
    ]

    # 2. Symulacja ulewy (Wykrywanie wąskich gardeł: gdzie przepływ > przepustowość)
    overloaded_pipes = []
    for u, v, attr in G_infra.edges(data=True):
        if attr["storm_ls"] > attr["cap_ls"]:
            overloaded_pipes.append({
                "u": u, "v": v,
                "pipe": attr["pipe"],
                "diam_mm": attr["diam_mm"],
                "cap_ls": attr["cap_ls"],
                "storm_ls": attr["storm_ls"],
                "overflow_manhole": INFRA_NODES[v]["name"]
            })

    # 3. Analiza odcięcia awarii (Isolation Trace): pęknięcie na odcinku Zawor_Z2_Grunwald -> Zawor_Z1_Ogrody
    broken_pipe = ("Zawor_Z2_Grunwald", "Zawor_Z1_Ogrody")
    valve_to_close = broken_pipe[0]
    cut_off_customers = [
        INFRA_NODES[n]["name"] for n in nx.ancestors(G_infra, valve_to_close)
    ]

    print("\n[2] ANALIZA SIECI INFRASTRUKTURALNEJ POZNANIA (Kanalizacja i Wodociągi - Graf Skierowany):")
    print(f"  -> Śledzenie 'w górę sieci' od [{INFRA_NODES[sensor_node]['name']}]:")
    print(f"     Podejrzane źródła zrzutu: {', '.join(suspect_factories)}")
    print(f"     Wykluczone zakłady (inna gałąź sieci): {', '.join(innocent_factories)}")
    print(f"  -> Symulacja ulewy (wąskie gardło): Przepełnienie na '{overloaded_pipes[0]['pipe']}' "
          f"({overloaded_pipes[0]['storm_ls']} l/s > {overloaded_pipes[0]['cap_ls']} l/s) -> Wybije: {overloaded_pipes[0]['overflow_manhole']}")
    print(f"  -> Analiza awarii rury: Należy zamknąć [{INFRA_NODES[valve_to_close]['name']}]")
    print("=" * 78)

    return {
        "shortest": {"path": shortest_path, "dist_km": round(shortest_dist, 1), "time_min": round(shortest_time, 1)},
        "fastest":  {"path": fastest_path,  "dist_km": round(fastest_dist, 1),  "time_min": round(fastest_time, 1)},
        "detour":   {"path": detour_path,   "dist_km": round(detour_dist, 1),   "time_min": round(detour_time, 1), "closed_edge": list(closed_edge)},
        "isochrone": {"nodes": iso_nodes, "edges": iso_edges, "minutes": isochrone_minutes},
        "upstream": {
            "sensor": sensor_node,
            "nodes": upstream_nodes,
            "edges": upstream_edges,
            "suspects": suspect_factories,
            "innocent": innocent_factories
        },
        "storm": overloaded_pipes[0],
        "isolation": {
            "broken_edge": list(broken_pipe),
            "valve_node": valve_to_close,
            "valve_name": INFRA_NODES[valve_to_close]["name"],
            "affected": cut_off_customers
        }
    }


# ==============================================================================
# 4. GENERATOR INTERAKTYWNEJ APLIKACJI WEBOWEJ (Folium + Leaflet JS)
# ==============================================================================
def build_interactive_web_map(results, output_html="index.html"):
    center_poznan = [52.4060, 16.9350]
    m = folium.Map(
        location=center_poznan,
        zoom_start=12,
        max_zoom=19,
        tiles=None,
        control_scale=True
    )

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr="&copy; OpenStreetMap &copy; CARTO",
        name="Mapa Jasna (CartoDB Positron - Wektor/Podkład)",
        max_zoom=19
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="&copy; OpenStreetMap contributors",
        name="OpenStreetMap Standard",
        max_zoom=19
    ).add_to(m)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="&copy; Esri",
        name="Ortofotomapa (Zdjęcia Satelitarne - Raster)"
    ).add_to(m)
    folium.LayerControl(position="bottomleft").add_to(m)

    payload = {
        "transport_nodes": TRANSPORT_NODES,
        "transport_edges": [{"u": u, "v": v, **attr} for u, v, attr in TRANSPORT_EDGES],
        "infra_nodes": INFRA_NODES,
        "infra_edges": [{"u": u, "v": v, **attr} for u, v, attr in INFRA_EDGES],
        "results": results
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    custom_ui = f"""
    <style>
        #gis-panel {{
            position: fixed;
            top: 16px;
            right: 16px;
            width: 395px;
            max-height: calc(100vh - 32px);
            overflow-y: auto;
            background: rgba(15, 23, 42, 0.95);
            color: #f8fafc;
            font-family: 'Segoe UI', system-ui, sans-serif;
            border-radius: 14px;
            padding: 18px;
            z-index: 9999;
            box-shadow: 0 12px 32px rgba(0,0,0,0.45);
            border: 1px solid rgba(148, 163, 184, 0.25);
        }}
        #gis-panel h2 {{
            margin: 0 0 4px 0;
            font-size: 17px;
            color: #38bdf8;
            letter-spacing: 0.3px;
        }}
        #gis-panel .subtitle {{
            font-size: 12px;
            color: #94a3b8;
            margin-bottom: 14px;
            padding-bottom: 10px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.2);
        }}
        .section-label {{
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: #cbd5e1;
            margin: 12px 0 6px 0;
            font-weight: 700;
        }}
        .mode-btn {{
            display: block;
            width: 100%;
            text-align: left;
            background: #1e293b;
            color: #e2e8f0;
            border: 1px solid #334155;
            padding: 10px 12px;
            margin-bottom: 7px;
            border-radius: 8px;
            cursor: pointer;
            font-size: 13px;
            font-weight: 600;
            transition: all 0.18s ease;
        }}
        .mode-btn:hover {{
            background: #334155;
            border-color: #38bdf8;
        }}
        .mode-btn.active {{
            background: #0284c7;
            color: #ffffff;
            border-color: #38bdf8;
            box-shadow: 0 0 12px rgba(56, 189, 248, 0.4);
        }}
        #result-box {{
            margin-top: 12px;
            background: #0f172a;
            border: 1px solid #334155;
            border-radius: 10px;
            padding: 12px;
            font-size: 12.5px;
            line-height: 1.5;
        }}
        .badge {{
            display: inline-block;
            padding: 2px 7px;
            border-radius: 4px;
            font-weight: 700;
            font-size: 11px;
            margin-right: 4px;
        }}
        .b-blue {{ background: #2563eb; color: #fff; }}
        .b-green {{ background: #16a34a; color: #fff; }}
        .b-red {{ background: #dc2626; color: #fff; }}
        .b-amber {{ background: #d97706; color: #fff; }}
        .stat-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 8px;
            margin: 8px 0;
        }}
        .stat-card {{
            background: #1e293b;
            padding: 8px;
            border-radius: 6px;
            border-left: 3px solid #38bdf8;
        }}
        .stat-card b {{ display: block; font-size: 14px; color: #fff; }}
        .stat-card span {{ font-size: 11px; color: #94a3b8; }}
    </style>

    <div id="gis-panel">
        <h2>🌐 Analizy Sieciowe w GIS — Poznań</h2>
        <div class="subtitle">Autor: <b>Paweł Siciński</b> | Silnik: <code>Python (NetworkX + Folium)</code></div>

        <div class="section-label">1. Sieć Transportowa Poznania (Slajdy 5–6)</div>
        <button class="mode-btn active" id="btn-routes" onclick="setScenario('routes')">
            🚗 Najkrótsza vs. Najszybsza trasa (Wagi)
        </button>
        <button class="mode-btn" id="btn-detour" onclick="setScenario('detour')">
            🚧 Zamknięcie Mostu Przemysła I i objazd (Topologia)
        </button>
        <button class="mode-btn" id="btn-iso" onclick="setScenario('isochrone')">
            🚑 Izochrona karetki: zasięg 10 min (Dostępność)
        </button>

        <div class="section-label">2. Sieć Infrastrukturalna Poznania (Slajdy 7–8)</div>
        <button class="mode-btn" id="btn-upstream" onclick="setScenario('upstream')">
            🧪 Kanalizacja: Śledzenie skażenia "w górę sieci"
        </button>
        <button class="mode-btn" id="btn-storm" onclick="setScenario('storm')">
            🌧️ Symulacja ulewy (Jeżyce) + Odcięcie zaworu
        </button>

        <div id="result-box"></div>
    </div>

    <script>
        const DATA = {payload_json};
        let layerGroup = null;

        function getMapInstance() {{
            for (let key in window) {{
                if (key.startsWith('map_') && window[key] instanceof L.Map) {{
                    return window[key];
                }}
            }}
            return null;
        }}

        function clearLayers(map) {{
            if (layerGroup) {{
                map.removeLayer(layerGroup);
            }}
            layerGroup = L.layerGroup().addTo(map);
        }}

        function drawTransportBase(map, highlightEdges = [], closedEdge = null) {{
            DATA.transport_edges.forEach(e => {{
                const c1 = DATA.transport_nodes[e.u].coords;
                const c2 = DATA.transport_nodes[e.v].coords;
                const isClosed = closedEdge && (
                    (e.u === closedEdge[0] && e.v === closedEdge[1]) ||
                    (e.u === closedEdge[1] && e.v === closedEdge[0])
                );
                const line = L.polyline([c1, c2], {{
                    color: isClosed ? '#ef4444' : '#64748b',
                    weight: isClosed ? 5 : 3,
                    opacity: isClosed ? 0.95 : 0.45,
                    dashArray: isClosed ? '8, 8' : null
                }}).addTo(layerGroup);

                line.bindTooltip(
                    `<b>Krawędź (ulica):</b> ${{e.road}}<br>` +
                    `<b>Atrybut długość:</b> ${{e.dist_km}} km<br>` +
                    `<b>Atrybut czas:</b> ${{e.time_min}} min` +
                    (isClosed ? `<br><b style="color:#dc2626">⛔ MOST ZAMKNIĘTY (REMONT)</b>` : ``),
                    {{sticky: true}}
                );
            }});

            Object.entries(DATA.transport_nodes).forEach(([id, n]) => {{
                let color = '#475569';
                let radius = 6;
                if (n.type === 'start') {{ color = '#16a34a'; radius = 9; }}
                if (n.type === 'target') {{ color = '#dc2626'; radius = 9; }}

                L.circleMarker(n.coords, {{
                    radius: radius,
                    fillColor: color,
                    color: '#ffffff',
                    weight: 2,
                    fillOpacity: 0.95
                }}).bindTooltip(`<b>Węzeł (Node):</b> ${{n.name}}`).addTo(layerGroup);
            }});
        }}

        function drawPathLine(pathNodes, color, weight, dashArray, label) {{
            const latlngs = pathNodes.map(id => DATA.transport_nodes[id].coords);
            L.polyline(latlngs, {{
                color: color,
                weight: weight,
                opacity: 0.9,
                dashArray: dashArray
            }}).bindTooltip(label, {{sticky: true}}).addTo(layerGroup);
        }}

        function drawInfraBase(map) {{
            DATA.infra_edges.forEach(e => {{
                const n1 = DATA.infra_nodes[e.u];
                const n2 = DATA.infra_nodes[e.v];
                L.polyline([n1.coords, n2.coords], {{
                    color: '#64748b',
                    weight: 4,
                    opacity: 0.55
                }}).bindTooltip(
                    `<b>Przewód:</b> ${{e.pipe}}<br>` +
                    `<b>Kierunek grawitacyjny:</b> ${{n1.elev_m}} m n.p.m. &rarr; ${{n2.elev_m}} m n.p.m.<br>` +
                    `<b>Średnica:</b> DN${{e.diam_mm}} mm | <b>Przepustowość:</b> ${{e.cap_ls}} l/s`,
                    {{sticky: true}}
                ).addTo(layerGroup);
            }});

            Object.entries(DATA.infra_nodes).forEach(([id, n]) => {{
                let color = '#0284c7';
                let radius = 7;
                if (n.role.includes('factory')) {{ color = '#9333ea'; radius = 9; }}
                if (n.role === 'valve') {{ color = '#f59e0b'; radius = 8; }}
                if (n.role === 'sink') {{ color = '#0f766e'; radius = 10; }}

                L.circleMarker(n.coords, {{
                    radius: radius,
                    fillColor: color,
                    color: '#fff',
                    weight: 2,
                    fillOpacity: 0.95
                }}).bindTooltip(
                    `<b>Węzeł:</b> ${{n.name}}<br><b>Wysokość:</b> ${{n.elev_m}} m n.p.m.`
                ).addTo(layerGroup);
            }});
        }}

        function setScenario(mode) {{
            document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
            const map = getMapInstance();
            if (!map) return;
            clearLayers(map);
            const box = document.getElementById('result-box');

            if (mode === 'routes') {{
                document.getElementById('btn-routes').classList.add('active');
                drawTransportBase(map);
                drawPathLine(DATA.results.shortest.path, '#3b82f6', 6, '10, 8', 'Trasa NAJKRÓTSZA (przez Rondo Kaponiera i Centrum)');
                drawPathLine(DATA.results.fastest.path, '#22c55e', 6, null, 'Trasa NAJSZYBSZA (II Ramą - ul. Hetmańską)');

                box.innerHTML = `
                    <div><span class="badge b-blue">NIEBIESKA (Przerywana)</span> <b>Trasa Najkrótsza (waga: km)</b></div>
                    <div class="stat-grid">
                        <div class="stat-card"><b>${{DATA.results.shortest.dist_km}} km</b><span>Przez Kaponierę i Rynek</span></div>
                        <div class="stat-card"><b>${{DATA.results.shortest.time_min}} min</b><span>Czas (korki w centrum)</span></div>
                    </div>
                    <div style="margin-top:8px;"><span class="badge b-green">ZIELONA (Ciągła)</span> <b>Trasa Najszybsza (waga: czas)</b></div>
                    <div class="stat-grid">
                        <div class="stat-card" style="border-color:#22c55e"><b>${{DATA.results.fastest.dist_km}} km</b><span>II Ramą (ul. Hetmańską)</span></div>
                        <div class="stat-card" style="border-color:#22c55e"><b>${{DATA.results.fastest.time_min}} min</b><span>Zysk: <b>${{(DATA.results.shortest.time_min - DATA.results.fastest.time_min).toFixed(1)}} min szybciej!</b></span></div>
                    </div>
                    <div style="color:#94a3b8; font-size:11.5px; margin-top:6px;">
                        💡 <b>Wniosek (Slajd 2 i 6):</b> Trasa przez ul. Hetmańską jest o 1,9 km dłuższa, ale omija zakorkowane Rondo Kaponiera i oszczędza aż 14 minut.
                    </div>
                `;
            }}
            else if (mode === 'detour') {{
                document.getElementById('btn-detour').classList.add('active');
                const closed = DATA.results.detour.closed_edge;
                drawTransportBase(map, [], closed);
                drawPathLine(DATA.results.detour.path, '#f59e0b', 6, null, 'Nowa trasa objazdowa po zamknięciu Mostu Przemysła I');

                box.innerHTML = `
                    <div><span class="badge b-red">AWARIA / REMONT</span> <b>Zamknięto: Most Przemysła I</b></div>
                    <p style="margin:6px 0;">Usunięto krawędź z topologii grafu (<code>G.remove_edge</code>). Algorytm automatycznie przekierował ruch przez Most św. Rocha:</p>
                    <div class="stat-grid">
                        <div class="stat-card" style="border-color:#f59e0b"><b>${{DATA.results.detour.dist_km}} km</b><span>Nowa długość</span></div>
                        <div class="stat-card" style="border-color:#f59e0b"><b>${{DATA.results.detour.time_min}} min</b><span>Nowy czas przejazdu</span></div>
                    </div>
                    <div style="color:#94a3b8; font-size:11.5px;">
                        💡 <b>Wniosek (Slajd 2 pkt 2 i 4):</b> Zmiana topologii sieci lub zamknięcie odcinka w bazie danych natychmiast zmienia wynik analizy.
                    </div>
                `;
            }}
            else if (mode === 'isochrone') {{
                document.getElementById('btn-iso').classList.add('active');
                drawTransportBase(map);
                DATA.results.isochrone.edges.forEach(pair => {{
                    const c1 = DATA.transport_nodes[pair[0]].coords;
                    const c2 = DATA.transport_nodes[pair[1]].coords;
                    L.polyline([c1, c2], {{ color: '#a855f7', weight: 7, opacity: 0.9 }}).addTo(layerGroup);
                }});
                DATA.results.isochrone.nodes.forEach(id => {{
                    const n = DATA.transport_nodes[id];
                    L.circle(n.coords, {{ radius: 600, color: '#a855f7', fillColor: '#a855f7', fillOpacity: 0.18, weight: 1 }}).addTo(layerGroup);
                }});

                box.innerHTML = `
                    <div><span class="badge" style="background:#9333ea;color:#fff;">IZOCHRONA 10 MIN</span> <b>Obszar dostępności karetki</b></div>
                    <p style="margin:6px 0;">Funkcja <code>nx.ego_graph</code> oblicza po rzeczywistych ulicach Poznania, dokąd karetka ze Szpitala Klinicznego (ul. Przybyszewskiego) dojedzie w <b>maksymalnie 10 minut</b>:</p>
                    <div class="stat-grid">
                        <div class="stat-card" style="border-color:#a855f7"><b>${{DATA.results.isochrone.nodes.length}} węzłów</b><span>W zasięgu 10 minut</span></div>
                        <div class="stat-card" style="border-color:#a855f7"><b>Po drogach</b><span>Nie w linii prostej!</span></div>
                    </div>
                `;
            }}
            else if (mode === 'upstream') {{
                document.getElementById('btn-upstream').classList.add('active');
                drawInfraBase(map);
                DATA.results.upstream.edges.forEach(pair => {{
                    const c1 = DATA.infra_nodes[pair[0]].coords;
                    const c2 = DATA.infra_nodes[pair[1]].coords;
                    L.polyline([c1, c2], {{ color: '#ef4444', weight: 7, opacity: 0.95 }}).addTo(layerGroup);
                }});

                box.innerHTML = `
                    <div><span class="badge b-red">ŚLEDZENIE W GÓRĘ SIECI</span> <b>Wykryto toksyny w S-2 (Garbary)!</b></div>
                    <p style="margin:6px 0;">W studzience pomiarowej <b>S-2 (Garbary)</b> wykryto skażenie. Ścieki spływają grawitacyjnie z wyżej położonych dzielnic (~85 m n.p.m.) ku Warcie (~54 m n.p.m.). Algorytm <code>nx.ancestors()</code> bada sieć "pod prąd":</p>
                    <div style="background:#1e293b; padding:8px; border-radius:6px; border-left:3px solid #ef4444; margin-bottom:6px;">
                        <b>🚨 Podejrzane zakłady (połączone w górę sieci):</b><br>
                        • ${{DATA.results.upstream.suspects.join('<br>• ')}}
                    </div>
                    <div style="background:#1e293b; padding:8px; border-radius:6px; border-left:3px solid #22c55e;">
                        <b>✅ Wykluczony zakład (prawy brzeg Warty):</b><br>
                        • ${{DATA.results.upstream.innocent.join(', ')}}
                    </div>
                `;
            }}
            else if (mode === 'storm') {{
                document.getElementById('btn-storm').classList.add('active');
                drawInfraBase(map);

                const st = DATA.results.storm;
                L.polyline([DATA.infra_nodes[st.u].coords, DATA.infra_nodes[st.v].coords], {{
                    color: '#f97316', weight: 8, opacity: 0.95
                }}).addTo(layerGroup);
                L.circleMarker(DATA.infra_nodes[st.v].coords, {{
                    radius: 15, color: '#ef4444', fillColor: '#ef4444', fillOpacity: 0.45, weight: 3
                }}).bindTooltip("🚨 WYBICIE STUDZIENKI NA JEŻYCACH (Przepływ > Przepustowość)").addTo(layerGroup);

                const iso = DATA.results.isolation;
                L.polyline([DATA.infra_nodes[iso.broken_edge[0]].coords, DATA.infra_nodes[iso.broken_edge[1]].coords], {{
                    color: '#dc2626', weight: 7, dashArray: '8, 8'
                }}).addTo(layerGroup);
                L.circleMarker(DATA.infra_nodes[iso.valve_node].coords, {{
                    radius: 14, color: '#eab308', fillColor: '#eab308', fillOpacity: 0.9, weight: 3
                }}).bindTooltip("🔧 ZAKRĘĆ TEN ZAWÓR: " + iso.valve_name).addTo(layerGroup);

                box.innerHTML = `
                    <div><span class="badge b-amber">1. SYMULACJA ULEWY</span> <b>Wąskie gardło (Slajd 8)</b></div>
                    <p style="margin:4px 0 8px 0;">Na odcinku <b>${{st.pipe}}</b> przepływ deszczówki (<b>${{st.storm_ls}} l/s</b>) przekracza przepustowość rury (<b>${{st.cap_ls}} l/s</b>) &rarr; wybije <b>${{st.overflow_manhole}}</b>.</p>
                    <div><span class="badge b-red">2. ANALIZA AWARII</span> <b>Odcięcie zaworu</b></div>
                    <p style="margin:4px 0;">Pęknięcie magistrali <b>Z-2 &rarr; Z-1</b>. Program wskazuje najbliższy zawór zasilający od strony źródła: należy zakręcić <b>${{iso.valve_name}}</b>.</p>
                `;
            }}
        }}

        window.addEventListener('load', () => {{
            setTimeout(() => setScenario('routes'), 300);
        }});
    </script>
    """

    m.get_root().html.add_child(folium.Element(custom_ui))
    m.save(output_html)
    return output_html


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_file = os.path.join(script_dir, "index.html")

    analysis_results = run_network_analyses()
    saved_path = build_interactive_web_map(analysis_results, out_file)
    print(f"\n[OK] Wygenerowano interaktywną aplikację webową dla Poznania: {saved_path}")
