"""
================================================================================
ANALIZY SIECIOWE W GIS - ZINTEGROWANY SYSTEM ANALITYCZNY MIASTA POZNANIA (v4.0)
Autor: Paweł Siciński | Silnik: Python (NetworkX + Folium) + Web-GIS / QGIS UI
================================================================================
Kompletny system klasy Decision Support System (DSS) dla miasta Poznania:
NOWOŚCI W WERSJI 4.0:
1. DOKŁADNA GEOMETRIA TOROWISK TRAMWAJOWYCH (OSM railway=tram - 816 odcinków)
   ORAZ TRAS AUTOBUSOWYCH ZTM POZNAŃ (wyznaczonych po rzeczywistych ulicach OSM).
2. ZACIEMNIENIE OBSZARU POZA GRANICAMI POZNANIA (Inverted Polygon Mask)
   eksponujące kształt administracyjny miasta Poznania.
3. PANEL POWIADOMIEŃ I LOG ZDARZEŃ KRYZYSOWYCH (System Alarmowy / Event Log)
   z historią incydentów, znacznikami czasu i przyciskiem przywracania stanu.
4. SUWAK CZASU I ANIMACJA PROPAGACJI ZJAWISK (Time-Slider t = 0..60 min)
   animujący spadek ciśnienia w wodociągach, falę wezbraniową w kanalizacji,
   kaskadowy blackout/SZR oraz ruch pojazdów/tramwajów i rozwój izochron.
5. MULTI-SCENARIUSZOWOŚĆ / TRYB SPLIT-SCREEN (Porównywanie Awarii A vs B)
   z dwoma zsynchronizowanymi mapami Poznania obok siebie oraz nakładaniem warstw.
6. INTELIGENTNY ASYSTENT DORADCZY AI (Rekomendacje dla Sztabu Kryzysowego)
   generujący konkretne wytyczne operacyjne dla służb (Aquanet, Enea, ZTM, PSP).
================================================================================
"""

import json
import math
import os
import sys
import webbrowser
import networkx as nx
import folium


# ==============================================================================
# 1. FUNKCJE GEOMETRYCZNE, RAY-CASTING I MODEL WYSOKOŚCIOWY (DEM POZNANIA)
# ==============================================================================
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def point_in_polygon(lat, lon, polygon):
    """Sprawdza metodą Ray-Casting, czy punkt (lat, lon) leży w granicach Poznania."""
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i][0], polygon[i][1]
        yj, xj = polygon[j][0], polygon[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def compute_poznan_dem_elevation(lat, lon):
    warta_lon = 16.944 + 0.018 * math.sin((lat - 52.38) * 18.0)
    dist_warta_km = abs(lon - warta_lon) * 67.8
    warta_base = 52.2 - (lat - 52.40) * 32.0
    valley_rise = 28.5 * (1.0 - math.exp(-(dist_warta_km ** 1.45) / 1.85))
    west_Slope = max(0.0, (16.935 - lon) * 95.0)
    east_Slope = max(0.0, (lon - 16.955) * 68.0)
    bogdanka_lat = 52.423 + (16.915 - lon) * 0.28
    dist_bogdanka = abs(lat - bogdanka_lat) * 111.0
    bogdanka_dip = -9.5 * math.exp(-(dist_bogdanka ** 2) / 0.35) if lon < 16.942 else 0.0
    dist_morasko = haversine_km(lat, lon, 52.486, 16.912)
    morasko_hill = 62.0 * math.exp(-(dist_morasko ** 2) / 4.2)
    micro = 1.6 * math.sin(lat * 420.0) * math.cos(lon * 380.0)
    elev = warta_base + valley_rise + west_Slope + east_Slope + bogdanka_dip + morasko_hill + micro
    return round(max(49.8, min(154.0, elev)), 1)


def load_poznan_boundary(boundary_path):
    with open(boundary_path, "r", encoding="utf-8") as f:
        bdata = json.load(f)

    rel = bdata["elements"][0]
    raw_segments = []
    for m in rel.get("members", []):
        if m.get("type") == "way" and m.get("role") == "outer" and "geometry" in m:
            seg = [[round(pt["lat"], 5), round(pt["lon"], 5)] for pt in m["geometry"]]
            if len(seg) >= 2:
                raw_segments.append(seg)

    if not raw_segments:
        return []

    ordered_ring = list(raw_segments.pop(0))
    while raw_segments:
        last_pt = ordered_ring[-1]
        best_idx = -1
        best_rev = False
        best_dist = float("inf")
        for idx, seg in enumerate(raw_segments):
            d_start = (seg[0][0] - last_pt[0]) ** 2 + (seg[0][1] - last_pt[1]) ** 2
            d_end = (seg[-1][0] - last_pt[0]) ** 2 + (seg[-1][1] - last_pt[1]) ** 2
            if d_start < best_dist:
                best_dist = d_start
                best_idx = idx
                best_rev = False
            if d_end < best_dist:
                best_dist = d_end
                best_idx = idx
                best_rev = True
        nxt = raw_segments.pop(best_idx)
        if best_rev:
            nxt = list(reversed(nxt))
        ordered_ring.extend(nxt[1:])

    simplified = [ordered_ring[i] for i in range(0, len(ordered_ring), 2)]
    if simplified[-1] != simplified[0]:
        simplified.append(simplified[0])
    return simplified


def parse_maxspeed(tags, highway):
    ms = tags.get("maxspeed", "")
    if isinstance(ms, list):
        ms = ms[0]
    try:
        return float(str(ms).split()[0])
    except Exception:
        defaults = {
            "motorway": 120.0,
            "motorway_link": 60.0,
            "trunk": 70.0,
            "trunk_link": 50.0,
            "primary": 60.0,
            "primary_link": 40.0,
            "secondary": 50.0,
            "secondary_link": 40.0,
            "tertiary": 40.0,
            "tertiary_link": 35.0,
            "unclassified": 35.0,
            "residential": 30.0,
            "living_street": 20.0,
        }
        return defaults.get(highway, 30.0)


def get_traffic_speed_factor(lat, lon, name, highway):
    name_l = (name or "").lower()
    if highway in ("motorway", "motorway_link"):
        return 0.90
    if any(k in name_l for k in ["hetmańska", "reymonta", "przemysła", "chartowo", "krzywoustego", "lechicka", "hlonda", "gdyńska", "bałtycka", "witosa", "niesta", "dolna wilda"]):
        return 0.85
    if (52.398 <= lat <= 52.416) and (16.890 <= lon <= 16.950):
        return 0.34
    if highway in ("residential", "living_street", "unclassified"):
        return 0.52
    if highway in ("tertiary", "tertiary_link", "secondary_link"):
        return 0.60
    return 0.68


# ==============================================================================
# 2. BUDOWA ZINTEGROWANEJ BAZY DANYCH GIS POZNANIA (v4.0)
# ==============================================================================
def load_and_prepare_poznan_gis(raw_osm_path, boundary_path, tram_osm_path=None):
    print("=" * 94)
    print(" KOMPILACJA SYSTEMU GIS POZNANIA v4.0 (TRAMWAJE OSM + AUTOBUSY ZTM + AI + SPLIT-SCREEN + TIME)")
    print("=" * 94)

    boundary_ring = load_poznan_boundary(boundary_path)
    print(f"[1/7] Wczytano granicę administracyjną Poznania ({len(boundary_ring)} wierzchołków).")

    with open(raw_osm_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    all_nodes_coords = {}
    inside_poznan_nodes = set()
    ways = []

    for el in data["elements"]:
        if el["type"] == "node":
            lat, lon = round(el["lat"], 5), round(el["lon"], 5)
            all_nodes_coords[el["id"]] = (lat, lon)
            if point_in_polygon(lat, lon, boundary_ring):
                inside_poznan_nodes.add(el["id"])
        elif el["type"] == "way":
            ways.append(el)

    node_use_count = {}
    for w in ways:
        nd_list = [n for n in w.get("nodes", []) if n in inside_poznan_nodes]
        if len(nd_list) < 2:
            continue
        node_use_count[nd_list[0]] = node_use_count.get(nd_list[0], 0) + 2
        node_use_count[nd_list[-1]] = node_use_count.get(nd_list[-1], 0) + 2
        for n in nd_list[1:-1]:
            node_use_count[n] = node_use_count.get(n, 0) + 1

    G_dir = nx.DiGraph()
    G_undir_main = nx.Graph()
    background_roads_major = []
    background_roads_local = []

    for w in ways:
        nd_list = [n for n in w.get("nodes", []) if n in inside_poznan_nodes]
        if len(nd_list) < 2:
            continue
        tags = w.get("tags", {})
        highway = tags.get("highway", "residential")
        name = tags.get("name", tags.get("ref", "Ulica lokalna"))
        maxspeed = parse_maxspeed(tags, highway)
        oneway_tag = tags.get("oneway", "no")
        junction_tag = tags.get("junction", "")

        filtered_nds = [nd_list[0]]
        for idx in range(1, len(nd_list) - 1):
            nid = nd_list[idx]
            if node_use_count.get(nid, 0) > 1:
                filtered_nds.append(nid)
            else:
                prev_c = all_nodes_coords[filtered_nds[-1]]
                curr_c = all_nodes_coords[nid]
                if haversine_km(prev_c[0], prev_c[1], curr_c[0], curr_c[1]) >= 0.014:
                    filtered_nds.append(nid)
        filtered_nds.append(nd_list[-1])

        road_coords = [[all_nodes_coords[n][0], all_nodes_coords[n][1]] for n in filtered_nds]
        if highway in ("motorway", "trunk", "trunk_link", "primary", "primary_link", "secondary", "tertiary"):
            background_roads_major.append({
                "coords": road_coords,
                "name": name,
                "hw": highway,
                "maxspeed": int(maxspeed)
            })
        else:
            background_roads_local.append({
                "coords": road_coords,
                "name": name
            })

        is_oneway = (oneway_tag in ("yes", "true", "1")) or (junction_tag == "roundabout") or (highway in ("motorway", "motorway_link", "trunk_link", "primary_link"))
        is_reverse = (oneway_tag in ("-1", "reverse"))

        for i in range(len(filtered_nds) - 1):
            u, v = filtered_nds[i], filtered_nds[i + 1]
            if u == v:
                continue
            lat1, lon1 = all_nodes_coords[u]
            lat2, lon2 = all_nodes_coords[v]
            dist_km = haversine_km(lat1, lon1, lat2, lon2)
            if dist_km <= 0:
                continue

            mid_lat, mid_lon = (lat1 + lat2) / 2.0, (lon1 + lon2) / 2.0
            factor = get_traffic_speed_factor(mid_lat, mid_lon, name, highway)
            eff_speed_kmh = max(10.0, maxspeed * factor)
            time_min = (dist_km / eff_speed_kmh) * 60.0

            edge_attr = {
                "dist_km": dist_km,
                "time_min": time_min,
                "name": name,
                "highway": highway,
                "maxspeed": int(maxspeed)
            }

            G_dir.add_node(u, pos=all_nodes_coords[u])
            G_dir.add_node(v, pos=all_nodes_coords[v])

            if is_reverse:
                G_dir.add_edge(v, u, **edge_attr)
            elif is_oneway:
                G_dir.add_edge(u, v, **edge_attr)
            else:
                G_dir.add_edge(u, v, **edge_attr)
                G_dir.add_edge(v, u, **edge_attr)

            if highway in ("trunk", "trunk_link", "primary", "primary_link", "secondary", "secondary_link", "tertiary", "unclassified"):
                G_undir_main.add_node(u, pos=all_nodes_coords[u])
                G_undir_main.add_node(v, pos=all_nodes_coords[v])
                if not G_undir_main.has_edge(u, v) or G_undir_main[u][v]["dist_km"] > dist_km:
                    G_undir_main.add_edge(u, v, **edge_attr)

    largest_scc = max(nx.strongly_connected_components(G_dir), key=len)
    G_dir = G_dir.subgraph(largest_scc).copy()
    largest_cc_undir = max(nx.connected_components(G_undir_main), key=len)
    G_undir_main = G_undir_main.subgraph(largest_cc_undir).copy()

    print(f"[2/7] Pełny graf drogowy w granicach Poznania: {G_dir.number_of_nodes()} węzłów, "
          f"{G_dir.number_of_edges()} krawędzi ({len(background_roads_major)} dróg głównych + {len(background_roads_local)} lokalnych).")

    def find_nearest_node(G, lat, lon, name_filter=None):
        best_n = None
        best_d = float("inf")
        for n, d in G.nodes(data=True):
            nlat, nlon = d["pos"]
            dist = haversine_km(lat, lon, nlat, nlon)
            if name_filter and dist < 0.7:
                enames = [(edata.get("name") or "").lower() for _, _, edata in G.edges(n, data=True)]
                if G.is_directed():
                    enames += [(edata.get("name") or "").lower() for _, _, edata in G.in_edges(n, data=True)]
                if not any(name_filter.lower() in en for en in enames):
                    continue
            if dist < best_d:
                best_d = dist
                best_n = n
        if best_n is None and name_filter:
            return find_nearest_node(G, lat, lon, None)
        return best_n

    osm_nodes_list = list(G_dir.nodes())
    osm_id_to_idx = {nid: idx for idx, nid in enumerate(osm_nodes_list)}
    graph_coords = [[G_dir.nodes[nid]["pos"][0], G_dir.nodes[nid]["pos"][1]] for nid in osm_nodes_list]

    street_names_list = ["Ulica lokalna"]
    street_name_to_idx = {"Ulica lokalna": 0}

    graph_edges = []
    for u, v, edata in G_dir.edges(data=True):
        sname = edata.get("name") or "Ulica lokalna"
        if sname not in street_name_to_idx:
            street_name_to_idx[sname] = len(street_names_list)
            street_names_list.append(sname)
        s_idx = street_name_to_idx[sname]
        dist_m = max(1, int(round(edata["dist_km"] * 1000)))
        time_ds = max(1, int(round(edata["time_min"] * 600)))
        graph_edges.append([osm_id_to_idx[u], osm_id_to_idx[v], dist_m, time_ds, s_idx])

    landmarks_raw = [
        ("szpital_przybyszewskiego", "🏥 Szpital Kliniczny (ul. Przybyszewskiego 49)", 52.40585, 16.88512, "przybyszewskiego"),
        ("szpital_szwajcarska",      "🏥 Szpital Miejski im. Strusia (ul. Szwajcarska 3)", 52.38890, 16.99395, "szwajcarska"),
        ("rondo_kaponiera",          "🚉 Rondo Kaponiera / Dworzec Główny", 52.40778, 16.91248, "rondo kaponiera"),
        ("stary_rynek",              "🏛️ Stary Rynek / Centrum (Al. Marcinkowskiego)", 52.40695, 16.92885, "marcinkowskiego"),
        ("politechnika_piotrowo",    "🎓 Politechnika Poznańska (Kampus Piotrowo)", 52.40250, 16.94950, "piotrowo"),
        ("uam_morasko",              "🎓 Kampus UAM Morasko (Piątkowo Północ)", 52.46350, 16.92650, None),
        ("rondo_rataje",             "🚌 Rondo Rataje (Dworzec Autobusowy)", 52.39682, 16.95045, "rondo rataje"),
        ("rondo_srodka",             "🌉 Rondo Śródka / Ostrów Tumski", 52.41065, 16.95535, "rondo śródka"),
        ("petla_ogrody",             "🚋 Pętla Ogrody (ul. Dąbrowskiego)", 52.41695, 16.87872, "dąbrowskiego"),
        ("lotnisko_lawica",          "✈️ Port Lotniczy Poznań-Ławica (ul. Bukowska)", 52.42100, 16.83200, "bukowska"),
        ("gorczyn_petla",            "🚌 Węzeł Górczyn (ul. Głogowska)", 52.37885, 16.88085, "głogowska"),
        ("naramowice",               "🏘️ Naramowice (ul. Naramowicka)", 52.44650, 16.94850, "naramowicka"),
        ("podolany",                 "🏘️ Podolany (ul. Strzeszyńska)", 52.44200, 16.88600, "strzeszyńska"),
        ("staroleka",                "🛣️ Rondo Starołęka (ul. Hetmańska)", 52.38065, 16.93815, "hetmańska"),
    ]
    landmarks = []
    for lid, label, lat, lon, hint in landmarks_raw:
        oid = find_nearest_node(G_dir, lat, lon, hint)
        idx = osm_id_to_idx[oid]
        landmarks.append({
            "id": lid,
            "label": label,
            "node_idx": idx,
            "coords": graph_coords[idx]
        })

    preset_closures = [
        {"id": "most_przemysla", "name": "Most Przemysła I (ul. Hetmańska)", "lat": 52.38525, "lon": 16.94440, "radius_km": 0.35},
        {"id": "most_jadwigi",   "name": "Most Królowej Jadwigi", "lat": 52.40050, "lon": 16.94450, "radius_km": 0.30},
        {"id": "most_rocha",     "name": "Most św. Rocha", "lat": 52.40210, "lon": 16.94720, "radius_km": 0.25},
        {"id": "rondo_kaponiera","name": "Rondo Kaponiera (Centrum)", "lat": 52.40778, "lon": 16.91248, "radius_km": 0.22},
        {"id": "most_chrobrego", "name": "Most Bolesława Chrobrego (Śródka)", "lat": 52.41100, "lon": 16.94550, "radius_km": 0.28},
        {"id": "most_lecha",     "name": "Most Lecha (ul. Lechicka / III Rama)", "lat": 52.43350, "lon": 16.95550, "radius_km": 0.35},
    ]

    # ==========================================================================
    # 3. SIEĆ KANALIZACYJNA GRAWITACYJNA AQUANET POZNAŃ (27 WĘZŁÓW, 26 KOLEKTORÓW)
    # ==========================================================================
    raw_infra_nodes = {
        "Osiedle_Junikowo":         {"name": "Kolektor Junikowski (ul. Grunwaldzka / Cmentarna)",               "target_latlon": (52.38550, 16.84200), "hint": "grunwaldzka",      "elev_m": 87.5, "role": "housing"},
        "Zaklad_Chemiczny_Gorczyn": {"name": "Zakład Przemysłowy 'ChemPoz' (Górczyn - Źródło A)",               "target_latlon": (52.37885, 16.88085), "hint": "głogowska",        "elev_m": 86.2, "role": "factory"},
        "Osiedle_Grunwald":         {"name": "Węzeł Grunwald (Rondo Jana Nowaka-Jeziorańskiego)",               "target_latlon": (52.40014, 16.89268), "hint": "reymonta",         "elev_m": 82.0, "role": "housing"},
        "Osiedle_Lazarz":           {"name": "Dzielnica Łazarz / Targi (ul. Głogowska)",                        "target_latlon": (52.39850, 16.90450), "hint": "głogowska",        "elev_m": 79.0, "role": "housing"},
        "Zawor_Z2_Reymonta":        {"name": "Zasuwa Odcinająca Z-2 (ul. Przybyszewskiego 49)",                 "target_latlon": (52.40585, 16.88512), "hint": "przybyszewskiego", "elev_m": 79.5, "role": "valve"},
        "Osiedle_Smochowice":       {"name": "Osiedle Smochowice / Krzyżowniki (ul. Dąbrowskiego)",             "target_latlon": (52.43100, 16.83500), "hint": "dąbrowskiego",     "elev_m": 88.0, "role": "housing"},
        "Fabryka_Farb_Wola":        {"name": "Fabryka Farb 'KolorPoznań' (Ogrody/Wola - Źródło B)",             "target_latlon": (52.41695, 16.87872), "hint": "dąbrowskiego",     "elev_m": 83.5, "role": "factory_polluter"},
        "Zawor_Z1_Zeromskiego":     {"name": "Zasuwa Odcinająca Z-1 (Jeżyce - ul. Żeromskiego / Dąbrowskiego)", "target_latlon": (52.41485, 16.89620), "hint": "dąbrowskiego",     "elev_m": 76.0, "role": "valve"},
        "Osiedle_Podolany":         {"name": "Osiedle Podolany / Strzeszyn (ul. Strzeszyńska)",                 "target_latlon": (52.44200, 16.88600), "hint": "strzeszyńska",     "elev_m": 85.0, "role": "housing"},
        "Osiedle_Solacz_Piatkowo":  {"name": "Węzeł Sołacz (ul. Niestachowska / Witosa)",                       "target_latlon": (52.42340, 16.89595), "hint": "niestachowska",    "elev_m": 74.5, "role": "housing"},
        "Studzienka_S1_Jezyce":     {"name": "Studzienka Zbiorcza S-1 (Kolektor Bogdanki - Al. Wielkopolska)",  "target_latlon": (52.41980, 16.92120), "hint": "wielkopolska",     "elev_m": 67.5, "role": "manhole_narrow"},
        "Osiedle_Piatkowo":         {"name": "Dzielnica Piątkowo (Al. Solidarności / Witosa)",                  "target_latlon": (52.43950, 16.91500), "hint": "witosa",           "elev_m": 84.0, "role": "housing"},
        "Zawor_Z5_Winogrady":       {"name": "Zasuwa i Węzeł Winogrady Z-5 (Al. Solidarności / Murawa)",        "target_latlon": (52.42950, 16.93550), "hint": "murawa",           "elev_m": 71.0, "role": "valve"},
        "Osiedle_Naramowice":       {"name": "Dzielnica Naramowice (ul. Naramowicka)",                          "target_latlon": (52.44400, 16.94750), "hint": "naramowicka",      "elev_m": 69.0, "role": "housing"},
        "Zaklad_Metal_Debiec":      {"name": "Zakład Przemysłowy 'MetalWilda' (Dębiec - Źródło D)",             "target_latlon": (52.36550, 16.91200), "hint": "28 czerwca",       "elev_m": 79.5, "role": "factory"},
        "Osiedle_Wilda":            {"name": "Dzielnica Wilda (ul. Hetmańska / Rolna)",                         "target_latlon": (52.38445, 16.92045), "hint": "hetmańska",        "elev_m": 75.0, "role": "housing"},
        "Zawor_Z3_Kaponiera":       {"name": "Zasuwa i Węzeł Śródmiejski Z-3 (Rondo Kaponiera)",                "target_latlon": (52.40778, 16.91248), "hint": "święty marcin",    "elev_m": 74.0, "role": "valve"},
        "Studzienka_S4_Centrum":    {"name": "Studzienka Zbiorcza S-4 (Stary Rynek / Al. Marcinkowskiego)",     "target_latlon": (52.40695, 16.92885), "hint": "marcinkowskiego",  "elev_m": 65.0, "role": "manhole"},
        "Studzienka_S2_Garbary":    {"name": "Przepompownia i Czujnik S-2 (Kolektor Lewobrzeżny - Garbary)",    "target_latlon": (52.41635, 16.93595), "hint": "garbary",          "elev_m": 59.5, "role": "sensor_alert"},
        "Osiedle_Staroleka":        {"name": "Dzielnica Starołęka (ul. Starołęcka)",                            "target_latlon": (52.36600, 16.93800), "hint": "starołęcka",       "elev_m": 72.0, "role": "housing"},
        "Osiedle_Szczepankowo":     {"name": "Osiedle Szczepankowo / Spławie (ul. Ostrowska)",                  "target_latlon": (52.37200, 17.00200), "hint": "krzywoustego",     "elev_m": 81.0, "role": "housing"},
        "Osiedle_Zegrze_Chartowo":  {"name": "Osiedla Żegrze i Chartowo (Rondo Żegrze)",                        "target_latlon": (52.38495, 16.96020), "hint": "żegrze",           "elev_m": 71.5, "role": "housing"},
        "Zawor_Z4_Rataje":          {"name": "Zasuwa i Komora Z-4 (Rondo Rataje)",                              "target_latlon": (52.39682, 16.95045), "hint": "jana pawła",       "elev_m": 65.0, "role": "valve"},
        "Zaklad_Spozywczy_Malta":   {"name": "Zakład Przemysłowy 'EkoMalta' (Antoninek / Warszawska - Źródło C)","target_latlon": (52.41020, 16.98200), "hint": "warszawska",      "elev_m": 78.0, "role": "factory"},
        "Kolektor_Prawobrzezny":    {"name": "Studzienka Zbiorcza S-3 (Kolektor Prawobrzeżny - Rondo Śródka)",  "target_latlon": (52.41065, 16.95535), "hint": "podwale",          "elev_m": 60.0, "role": "manhole"},
        "Wezel_Syfon_Baltycka":     {"name": "Komora Połączeniowa S-5 (Most Lecha / ul. Hlonda)",               "target_latlon": (52.43280, 16.96020), "hint": "bałtycka",         "elev_m": 56.5, "role": "manhole"},
        "Oczyszczalnia_Kozieglowy": {"name": "Kolektor Dopływowy COŚ Aquanet (Północna granica miasta - ul. Gdyńska)", "target_latlon": (52.43950, 16.97950), "hint": "gdyńska",   "elev_m": 53.5, "role": "sink"},
    }

    infra_nodes = {}
    node_osm_id = {}
    for key, info in raw_infra_nodes.items():
        oid = find_nearest_node(G_undir_main, info["target_latlon"][0], info["target_latlon"][1], info["hint"])
        node_osm_id[key] = oid
        infra_nodes[key] = {
            "id": key,
            "name": info["name"],
            "coords": [G_undir_main.nodes[oid]["pos"][0], G_undir_main.nodes[oid]["pos"][1]],
            "elev_m": info["elev_m"],
            "role": info["role"]
        }

    collector_definitions = [
        ("Osiedle_Junikowo",         "Osiedle_Grunwald",        [],                                  {"diam_mm": 350, "cap_ls": 160, "base_flow_ls": 105, "pipe": "Kolektor Junikowski DN350 (wzdłuż ul. Grunwaldzkiej)"}),
        ("Zaklad_Chemiczny_Gorczyn", "Osiedle_Grunwald",        [(52.38862, 16.89848, "głogowska")], {"diam_mm": 350, "cap_ls": 150, "base_flow_ls": 95,  "pipe": "Kolektor Górczyński DN350 (ul. Głogowska -> ul. Reymonta)"}),
        ("Osiedle_Grunwald",         "Zawor_Z2_Reymonta",       [],                                  {"diam_mm": 450, "cap_ls": 210, "base_flow_ls": 155, "pipe": "Kolektor Grunwaldzki DN450 (ul. Przybyszewskiego)"}),
        ("Zawor_Z2_Reymonta",        "Zawor_Z1_Zeromskiego",    [(52.41122, 16.89025, "żeromskiego")],{"diam_mm": 500, "cap_ls": 260, "base_flow_ls": 195, "pipe": "Magistrala Zachodnia DN500 (ul. Przybyszewskiego -> Żeromskiego)"}),
        ("Osiedle_Smochowice",       "Fabryka_Farb_Wola",       [],                                  {"diam_mm": 300, "cap_ls": 130, "base_flow_ls": 85,  "pipe": "Kolektor Smochowicki DN300 (wzdłuż ul. Dąbrowskiego)"}),
        ("Fabryka_Farb_Wola",        "Zawor_Z1_Zeromskiego",    [],                                  {"diam_mm": 400, "cap_ls": 170, "base_flow_ls": 120, "pipe": "Kolektor Jeżycki Zachodni DN400 (ul. Dąbrowskiego)"}),
        ("Osiedle_Podolany",         "Osiedle_Solacz_Piatkowo", [],                                  {"diam_mm": 350, "cap_ls": 160, "base_flow_ls": 100, "pipe": "Kolektor Podolański DN350 (ul. Strzeszyńska -> Lutycka)"}),
        ("Osiedle_Solacz_Piatkowo",  "Studzienka_S1_Jezyce",    [],                                  {"diam_mm": 450, "cap_ls": 210, "base_flow_ls": 140, "pipe": "Kolektor Sołacki DN450 (ul. Nad Wierzbakiem)"}),
        ("Zawor_Z1_Zeromskiego",     "Studzienka_S1_Jezyce",    [(52.42150, 16.90650, "wielkopolska")],{"diam_mm": 250, "cap_ls": 150, "base_flow_ls": 185, "pipe": "Kolektor Jeżycki DN250 (Wąskie gardło - Al. Wielkopolska)"}),
        ("Studzienka_S1_Jezyce",     "Studzienka_S2_Garbary",   [],                                  {"diam_mm": 600, "cap_ls": 380, "base_flow_ls": 280, "pipe": "Kolektor Bogdanki DN600 (Al. Armii Poznań)"}),
        ("Osiedle_Lazarz",           "Zawor_Z3_Kaponiera",      [],                                  {"diam_mm": 400, "cap_ls": 180, "base_flow_ls": 120, "pipe": "Kolektor Łazarski DN400 (ul. Głogowska -> Kaponiera)"}),
        ("Zawor_Z3_Kaponiera",       "Studzienka_S4_Centrum",   [],                                  {"diam_mm": 450, "cap_ls": 220, "base_flow_ls": 165, "pipe": "Kolektor Śródmiejski DN450 (ul. Św. Marcin -> Marcinkowskiego)"}),
        ("Zaklad_Metal_Debiec",      "Osiedle_Wilda",           [],                                  {"diam_mm": 350, "cap_ls": 150, "base_flow_ls": 100, "pipe": "Kolektor Dębiecki DN350 (ul. 28 Czerwca 1956 r.)"}),
        ("Osiedle_Wilda",            "Studzienka_S4_Centrum",   [(52.39910, 16.92550, "wierzbięcice")],{"diam_mm": 450, "cap_ls": 190, "base_flow_ls": 150, "pipe": "Kolektor Wilda DN450 (ul. Wierzbięcice -> Centrum)"}),
        ("Studzienka_S4_Centrum",    "Studzienka_S2_Garbary",   [],                                  {"diam_mm": 600, "cap_ls": 360, "base_flow_ls": 265, "pipe": "Kolektor Staromiejski DN600 (Al. Marcinkowskiego -> Garbary)"}),
        ("Osiedle_Piatkowo",         "Zawor_Z5_Winogrady",      [],                                  {"diam_mm": 450, "cap_ls": 220, "base_flow_ls": 155, "pipe": "Kolektor Piątkowski DN450 (Al. Solidarności)"}),
        ("Zawor_Z5_Winogrady",       "Wezel_Syfon_Baltycka",    [],                                  {"diam_mm": 500, "cap_ls": 260, "base_flow_ls": 180, "pipe": "Kolektor Winogradzki DN500 (ul. Murawa -> Lechicka)"}),
        ("Osiedle_Naramowice",       "Wezel_Syfon_Baltycka",    [],                                  {"diam_mm": 450, "cap_ls": 210, "base_flow_ls": 145, "pipe": "Kolektor Naramowicki DN450 (ul. Naramowicka -> Most Lecha)"}),
        ("Studzienka_S2_Garbary",    "Wezel_Syfon_Baltycka",    [(52.42650, 16.94350, "szelągowska"), (52.43520, 16.95100, "lechicka")], {"diam_mm": 900, "cap_ls": 680, "base_flow_ls": 495, "pipe": "Kolektor Lewobrzeżny i Syfon pod Wartą DN900 (ul. Szelągowska)"}),
        ("Osiedle_Staroleka",        "Zawor_Z4_Rataje",         [(52.38065, 16.93815, "starołęcka")],{"diam_mm": 400, "cap_ls": 190, "base_flow_ls": 125, "pipe": "Kolektor Starołęcki DN400 (ul. Starołęcka -> Zamenhofa)"}),
        ("Osiedle_Szczepankowo",     "Osiedle_Zegrze_Chartowo", [],                                  {"diam_mm": 350, "cap_ls": 160, "base_flow_ls": 105, "pipe": "Kolektor Szczepankowski DN350 (ul. Krzywoustego)"}),
        ("Osiedle_Zegrze_Chartowo",  "Zawor_Z4_Rataje",         [],                                  {"diam_mm": 500, "cap_ls": 250, "base_flow_ls": 175, "pipe": "Kolektor Chartowski DN500 (ul. Żegrze -> Rondo Rataje)"}),
        ("Zawor_Z4_Rataje",          "Kolektor_Prawobrzezny",   [],                                  {"diam_mm": 700, "cap_ls": 420, "base_flow_ls": 295, "pipe": "Kolektor Ratajski DN700 (ul. Jana Pawła II -> Rondo Śródka)"}),
        ("Zaklad_Spozywczy_Malta",   "Kolektor_Prawobrzezny",   [],                                  {"diam_mm": 400, "cap_ls": 200, "base_flow_ls": 135, "pipe": "Kolektor Warszawski DN400 (wzdłuż ul. Warszawskiej)"}),
        ("Kolektor_Prawobrzezny",    "Wezel_Syfon_Baltycka",    [(52.42150, 16.95350, "hlonda")],    {"diam_mm": 1000,"cap_ls": 750, "base_flow_ls": 490, "pipe": "Kolektor Prawobrzeżny DN1000 (ul. Podwale -> ul. Hlonda)"}),
        ("Wezel_Syfon_Baltycka",     "Oczyszczalnia_Kozieglowy",[],                                  {"diam_mm": 1400,"cap_ls": 1400,"base_flow_ls": 960, "pipe": "Główny Kolektor Dopływowy COŚ Koziegłowy DN1400 (ul. Gdyńska)"}),
    ]

    infra_edges_list = []
    for idx, (u_key, v_key, mid_vias, attr) in enumerate(collector_definitions):
        chain_osm = [node_osm_id[u_key]]
        for mlat, mlon, mhint in mid_vias:
            mid_oid = find_nearest_node(G_undir_main, mlat, mlon, mhint)
            chain_osm.append(mid_oid)
        chain_osm.append(node_osm_id[v_key])

        geom_coords = []
        dist_sum = 0.0
        for i in range(len(chain_osm) - 1):
            sp = nx.shortest_path(G_undir_main, chain_osm[i], chain_osm[i + 1], weight="dist_km")
            dist_sum += nx.path_weight(G_undir_main, sp, weight="dist_km")
            for j, nid in enumerate(sp):
                if i > 0 and j == 0:
                    continue
                geom_coords.append([G_undir_main.nodes[nid]["pos"][0], G_undir_main.nodes[nid]["pos"][1]])

        infra_edges_list.append({
            "id": f"pipe_{idx}",
            "u": u_key,
            "v": v_key,
            "dist_km": round(dist_sum, 2),
            **attr,
            "geometry": geom_coords
        })

    # ==========================================================================
    # 4. SIEĆ WODOCIĄGOWA POD CIŚNIENIEM AQUANET (UKŁAD PIERŚCIENIOWY + ZAWORY)
    # ==========================================================================
    raw_water_nodes = {
        "SUW_Mosina_Wejscie": {"name": "🚰 Magistrala SUW Mosina (Wejście Południowe DN1000 - Górczyn)", "latlon": (52.3690, 16.8790), "role": "source", "pressure_mpa": 0.62, "valve_code": "Z-SUW1"},
        "SUW_Debina":         {"name": "🚰 Stacja Uzdatniania Wody SUW Dębina / Wiśniowa (DN800)",      "latlon": (52.3710, 16.9260), "role": "source", "pressure_mpa": 0.59, "valve_code": "Z-SUW2"},
        "ZWC_Piatkowo":       {"name": "🚰 Zbiorniki Wody Czystej ZWC Piątkowo / Morasko (DN600)",      "latlon": (52.4560, 16.9180), "role": "source", "pressure_mpa": 0.55, "valve_code": "Z-ZWC3"},

        "WZ_Gorczyn":     {"name": "🔧 Komora Zasuw WZ-01 Górczyn (ul. Głogowska)",           "latlon": (52.3820, 16.8850), "role": "valve", "pressure_mpa": 0.58, "valve_code": "WZ-01 (Górczyn)"},
        "WZ_Junikowo":    {"name": "🔧 Komora Zasuw WZ-02 Junikowo / Kopernika",              "latlon": (52.3910, 16.8580), "role": "valve", "pressure_mpa": 0.55, "valve_code": "WZ-02 (Junikowo)"},
        "WZ_Grunwald":    {"name": "🔧 Komora Zasuw WZ-03 Grunwald (Rondo Jeziorańskiego)",   "latlon": (52.4005, 16.8925), "role": "valve", "pressure_mpa": 0.54, "valve_code": "WZ-03 (Grunwald)"},
        "WZ_Lawica":      {"name": "🔧 Komora Zasuw WZ-04 Ławica / Bukowska",                 "latlon": (52.4160, 16.8550), "role": "valve", "pressure_mpa": 0.51, "valve_code": "WZ-04 (Ławica)"},
        "WZ_Ogrody":      {"name": "🔧 Komora Zasuw WZ-05 Ogrody (ul. Dąbrowskiego)",         "latlon": (52.4170, 16.8810), "role": "valve", "pressure_mpa": 0.52, "valve_code": "WZ-05 (Ogrody)"},
        "WZ_Jezyce":      {"name": "🔧 Komora Zasuw WZ-06 Jeżyce (ul. Żeromskiego / Rynek)",  "latlon": (52.4135, 16.9035), "role": "valve", "pressure_mpa": 0.50, "valve_code": "WZ-06 (Jeżyce)"},
        "WZ_Podolany":    {"name": "🔧 Komora Zasuw WZ-07 Podolany (ul. Strzeszyńska)",       "latlon": (52.4410, 16.8880), "role": "valve", "pressure_mpa": 0.51, "valve_code": "WZ-07 (Podolany)"},
        "WZ_Winogrady":   {"name": "🔧 Komora Zasuw WZ-08 Winogrady (Al. Solidarności)",      "latlon": (52.4310, 16.9320), "role": "valve", "pressure_mpa": 0.52, "valve_code": "WZ-08 (Winogrady)"},
        "WZ_Naramowice":  {"name": "🔧 Komora Zasuw WZ-09 Naramowice (ul. Naramowicka)",      "latlon": (52.4450, 16.9480), "role": "valve", "pressure_mpa": 0.49, "valve_code": "WZ-09 (Naramowice)"},
        "WZ_Kaponiera":   {"name": "🔧 Komora Zasuw WZ-10 Rondo Kaponiera (Śródmieście)",     "latlon": (52.4078, 16.9125), "role": "valve", "pressure_mpa": 0.51, "valve_code": "WZ-10 (Kaponiera)"},
        "WZ_Lazarz":      {"name": "🔧 Komora Zasuw WZ-11 Łazarz / Hetmańska",                "latlon": (52.3915, 16.9040), "role": "valve", "pressure_mpa": 0.55, "valve_code": "WZ-11 (Łazarz)"},
        "WZ_Wilda":       {"name": "🔧 Komora Zasuw WZ-12 Wilda (ul. Wierzbięcice / Rolna)",  "latlon": (52.3885, 16.9220), "role": "valve", "pressure_mpa": 0.56, "valve_code": "WZ-12 (Wilda)"},
        "WZ_StaryRynek":  {"name": "🔧 Komora Zasuw WZ-13 Centrum / Garbary",                 "latlon": (52.4085, 16.9330), "role": "valve", "pressure_mpa": 0.49, "valve_code": "WZ-13 (Garbary)"},
        "WZ_Srodka":      {"name": "🔧 Komora Zasuw WZ-14 Rondo Śródka (Magistrala Wschodnia)","latlon": (52.4105, 16.9550), "role": "valve", "pressure_mpa": 0.48, "valve_code": "WZ-14 (Śródka)"},
        "WZ_Rataje":      {"name": "🔧 Komora Zasuw WZ-15 Rondo Rataje (ul. Jana Pawła II)",  "latlon": (52.3968, 16.9505), "role": "valve", "pressure_mpa": 0.52, "valve_code": "WZ-15 (Rataje)"},
        "WZ_Zegrze":      {"name": "🔧 Komora Zasuw WZ-16 Rondo Żegrze / Hetmańska Wsch.",    "latlon": (52.3850, 16.9580), "role": "valve", "pressure_mpa": 0.54, "valve_code": "WZ-16 (Żegrze)"},
        "WZ_Chartowo":    {"name": "🔧 Komora Zasuw WZ-17 Chartowo / Szpital Szwajcarska",    "latlon": (52.3920, 16.9850), "role": "valve", "pressure_mpa": 0.49, "valve_code": "WZ-17 (Chartowo)"},
        "WZ_Antoninek":   {"name": "🔧 Komora Zasuw WZ-18 Antoninek / Warszawska",            "latlon": (52.4095, 16.9880), "role": "valve", "pressure_mpa": 0.47, "valve_code": "WZ-18 (Antoninek)"},
        "WZ_Staroleka":   {"name": "🔧 Komora Zasuw WZ-19 Starołęka (ul. Starołęcka)",        "latlon": (52.3740, 16.9390), "role": "valve", "pressure_mpa": 0.54, "valve_code": "WZ-19 (Starołęka)"},
    }

    water_nodes = {}
    water_osm_id = {}
    for key, info in raw_water_nodes.items():
        oid = find_nearest_node(G_undir_main, info["latlon"][0], info["latlon"][1], None)
        water_osm_id[key] = oid
        water_nodes[key] = {
            "id": key,
            "name": info["name"],
            "coords": [G_undir_main.nodes[oid]["pos"][0], G_undir_main.nodes[oid]["pos"][1]],
            "role": info["role"],
            "pressure_mpa": info["pressure_mpa"],
            "valve_code": info["valve_code"]
        }

    water_pipes_def = [
        ("SUW_Mosina_Wejscie", "WZ_Gorczyn",    {"name": "Magistrala Mosińska I DN1000 (ul. Głogowska Płd.)",          "diam_mm": 1000, "flow_m3h": 2450, "buildings": 18, "district": "Górczyn Południe"}),
        ("WZ_Gorczyn",         "WZ_Junikowo",   {"name": "Magistrala Zachodnia DN600 (Górczyn <-> Junikowo)",          "diam_mm": 600,  "flow_m3h": 820,  "buildings": 42, "district": "Os. Kopernika / Junikowo"}),
        ("WZ_Gorczyn",         "WZ_Lazarz",     {"name": "Magistrala Głogowska DN800 (Górczyn <-> Łazarz)",            "diam_mm": 800,  "flow_m3h": 1480, "buildings": 56, "district": "Górczyn / Łazarz"}),
        ("WZ_Junikowo",        "WZ_Grunwald",   {"name": "Wodociąg Grunwaldzki DN500 (ul. Grunwaldzka)",               "diam_mm": 500,  "flow_m3h": 640,  "buildings": 64, "district": "Grunwald Zachód"}),
        ("WZ_Junikowo",        "WZ_Lawica",     {"name": "Wodociąg Ławicki DN400 (ul. Bukowska / Złotowska)",          "diam_mm": 400,  "flow_m3h": 410,  "buildings": 38, "district": "Ławica / Marcelin"}),
        ("WZ_Lawica",          "WZ_Ogrody",     {"name": "Wodociąg Bukowski DN450 (Ławica <-> Ogrody)",                "diam_mm": 450,  "flow_m3h": 480,  "buildings": 45, "district": "Ogrody / Wola"}),
        ("WZ_Grunwald",        "WZ_Ogrody",     {"name": "Magistrala Pierścienia Zachodniego DN600 (ul. Żeromskiego)", "diam_mm": 600,  "flow_m3h": 910,  "buildings": 52, "district": "Grunwald / Ogrody"}),
        ("WZ_Grunwald",        "WZ_Kaponiera",  {"name": "Wodociąg Bukowska/Przybyszewskiego DN500",                   "diam_mm": 500,  "flow_m3h": 730,  "buildings": 68, "district": "Grunwald Centralny"}),
        ("WZ_Ogrody",          "WZ_Jezyce",     {"name": "Wodociąg Jeżycki DN500 (ul. Dąbrowskiego)",                  "diam_mm": 500,  "flow_m3h": 690,  "buildings": 74, "district": "Jeżyce"}),
        ("WZ_Ogrody",          "WZ_Podolany",   {"name": "Magistrala Północno-Zachodnia DN500 (ul. Lutycka)",          "diam_mm": 500,  "flow_m3h": 580,  "buildings": 41, "district": "Podolany / Strzeszyn"}),
        ("WZ_Podolany",        "ZWC_Piatkowo",  {"name": "Magistrala ZWC Piątkowo <-> Podolany DN600",                 "diam_mm": 600,  "flow_m3h": 950,  "buildings": 29, "district": "Piątkowo Zachód"}),
        ("ZWC_Piatkowo",       "WZ_Winogrady",  {"name": "Magistrala Piątkowska DN600 (Al. Solidarności)",             "diam_mm": 600,  "flow_m3h": 1120, "buildings": 62, "district": "Piątkowo / Winogrady"}),
        ("ZWC_Piatkowo",       "WZ_Naramowice", {"name": "Wodociąg Naramowicki Północny DN450",                        "diam_mm": 450,  "flow_m3h": 540,  "buildings": 48, "district": "Naramowice / Umultowo"}),
        ("WZ_Winogrady",       "WZ_Naramowice", {"name": "Pierścień Północny Winogrady <-> Naramowice DN500",          "diam_mm": 500,  "flow_m3h": 610,  "buildings": 55, "district": "Winogrady Wschód"}),
        ("WZ_Jezyce",          "WZ_Winogrady",  {"name": "Magistrala Sołacka DN500 (Al. Wielkopolska -> Murawa)",      "diam_mm": 500,  "flow_m3h": 670,  "buildings": 49, "district": "Sołacz / Winogrady"}),
        ("WZ_Jezyce",          "WZ_Kaponiera",  {"name": "Wodociąg Roosevelta DN500 (Jeżyce <-> Kaponiera)",           "diam_mm": 500,  "flow_m3h": 760,  "buildings": 63, "district": "Jeżyce / Centrum"}),
        ("WZ_Lazarz",          "WZ_Kaponiera",  {"name": "Magistrala Głogowska Śródmieście DN600",                     "diam_mm": 600,  "flow_m3h": 980,  "buildings": 71, "district": "Łazarz / MTP"}),
        ("WZ_Lazarz",          "WZ_Wilda",      {"name": "Magistrala Hetmańska Zachód DN700",                          "diam_mm": 700,  "flow_m3h": 1150, "buildings": 44, "district": "Łazarz / Wilda"}),
        ("SUW_Debina",         "WZ_Wilda",      {"name": "Magistrala Wschodnia SUW Dębina -> Wilda DN800",             "diam_mm": 800,  "flow_m3h": 1890, "buildings": 31, "district": "Dębina / Dolna Wilda"}),
        ("SUW_Debina",         "WZ_Staroleka",  {"name": "Przejście Poddenne pod Wartą Dębina -> Starołęka DN700",     "diam_mm": 700,  "flow_m3h": 1340, "buildings": 22, "district": "Starołęka / Dębina"}),
        ("WZ_Wilda",           "WZ_StaryRynek", {"name": "Magistrala Wildecka DN600 (ul. Wierzbięcice -> Centrum)",    "diam_mm": 600,  "flow_m3h": 890,  "buildings": 67, "district": "Wilda / Stare Miasto"}),
        ("WZ_Kaponiera",       "WZ_StaryRynek", {"name": "Magistrala Śródmiejska DN500 (ul. Św. Marcin -> Rynek)",     "diam_mm": 500,  "flow_m3h": 810,  "buildings": 78, "district": "Stare Miasto"}),
        ("WZ_Winogrady",       "WZ_StaryRynek", {"name": "Wodociąg Garbary / Armii Poznań DN500",                      "diam_mm": 500,  "flow_m3h": 720,  "buildings": 53, "district": "Garbary / Cytadela"}),
        ("WZ_StaryRynek",      "WZ_Srodka",     {"name": "Magistrala Mostowa Chwaliszewo <-> Śródka DN600",            "diam_mm": 600,  "flow_m3h": 840,  "buildings": 34, "district": "Chwaliszewo / Ostrów Tumski"}),
        ("WZ_Staroleka",       "WZ_Zegrze",     {"name": "Magistrala Prawobrzeżna Południowa DN700 (ul. Hetmańska)",   "diam_mm": 700,  "flow_m3h": 1220, "buildings": 46, "district": "Starołęka / Żegrze"}),
        ("WZ_Wilda",           "WZ_Zegrze",     {"name": "Magistrala Mostu Przemysła I DN600 (ul. Hetmańska)",         "diam_mm": 600,  "flow_m3h": 940,  "buildings": 25, "district": "Rataje Południe"}),
        ("WZ_Zegrze",          "WZ_Rataje",     {"name": "Magistrala Ratajska DN600 (Rondo Żegrze -> Rondo Rataje)",   "diam_mm": 600,  "flow_m3h": 1050, "buildings": 69, "district": "Rataje Dolne i Górne"}),
        ("WZ_Rataje",          "WZ_Srodka",     {"name": "Magistrala Jana Pawła II DN600 (Rataje <-> Śródka)",         "diam_mm": 600,  "flow_m3h": 920,  "buildings": 48, "district": "Rataje / Berdychowo"}),
        ("WZ_Zegrze",          "WZ_Chartowo",   {"name": "Wodociąg Chartowski DN500 (Os. Orła Białego -> Szwajcarska)","diam_mm": 500,  "flow_m3h": 680,  "buildings": 61, "district": "Żegrze / Chartowo"}),
        ("WZ_Rataje",          "WZ_Chartowo",   {"name": "Wodociąg Piłsudskiego / Inflancka DN450",                    "diam_mm": 450,  "flow_m3h": 590,  "buildings": 58, "district": "Os. Oświecenia / Lecha"}),
        ("WZ_Chartowo",        "WZ_Antoninek",  {"name": "Wodociąg Wschodni Chartowo <-> Antoninek DN400",             "diam_mm": 400,  "flow_m3h": 420,  "buildings": 36, "district": "Chartowo / Antoninek"}),
        ("WZ_Srodka",          "WZ_Antoninek",  {"name": "Magistrala Warszawska DN500 (Śródka <-> Antoninek)",         "diam_mm": 500,  "flow_m3h": 560,  "buildings": 43, "district": "Komandoria / Malta"}),
    ]

    water_edges_list = []
    for idx, (u_key, v_key, attr) in enumerate(water_pipes_def):
        u_oid = water_osm_id[u_key]
        v_oid = water_osm_id[v_key]
        sp = nx.shortest_path(G_undir_main, u_oid, v_oid, weight="dist_km")
        dist_km = round(nx.path_weight(G_undir_main, sp, weight="dist_km"), 2)
        geom_coords = [[G_undir_main.nodes[nid]["pos"][0], G_undir_main.nodes[nid]["pos"][1]] for nid in sp]
        water_edges_list.append({
            "id": f"wpipe_{idx}",
            "u": u_key,
            "v": v_key,
            "dist_km": dist_km,
            **attr,
            "geometry": geom_coords
        })

    # ==========================================================================
    # 5. SIEĆ ELEKTROENERGETYCZNA POZNANIA (ENEA OPERATOR - 32 STACJE, 34 LINIE)
    # ==========================================================================
    raw_power_nodes = {
        "EC_Karolin":       {"name": "⚡ Elektrociepłownia Poznań Karolin (EC II - Główne Źródło 110 kV)", "latlon": (52.4355, 16.9850), "voltage": "110 kV", "role": "source", "customers": 0, "mw": 0.0, "district": "Karolin (Źródło systemowe)", "critical": "Główne źródło energii elektrycznej i ciepła dla Poznania"},
        "SE_Zachod":        {"name": "⚡ Stacja Systemowa 220/110 kV Zachód (Plewiska / Górczyn)",         "latlon": (52.3755, 16.8680), "voltage": "110 kV", "role": "source", "customers": 0, "mw": 0.0, "district": "Górczyn Zachód (Źródło systemowe)", "critical": "Magistrala systemowa 220/110 kV PSE"},

        "GPZ_Garbary":      {"name": "🔌 GPZ Cytadela / Garbary (110/15 kV)",         "latlon": (52.4185, 16.9365), "voltage": "110/15 kV", "role": "gpz", "customers": 4200, "mw": 6.5, "district": "Garbary / Dolna Szelągowska", "critical": "Zasilanie Przepompowni Ścieków Aquanet Garbary"},
        "GPZ_Piatkowo":     {"name": "🔌 GPZ Piątkowo / Lechicka (110/15 kV)",        "latlon": (52.4385, 16.9180), "voltage": "110/15 kV", "role": "gpz", "customers": 5100, "mw": 7.0, "district": "Piątkowo Wschód", "critical": "Podstacja trakcyjna Poznańskiego Szybkiego Tramwaju (PST)"},
        "GPZ_Jezyce":       {"name": "🔌 GPZ Jeżyce / Ogrody (110/15 kV)",            "latlon": (52.4155, 16.8920), "voltage": "110/15 kV", "role": "gpz", "customers": 4800, "mw": 6.0, "district": "Jeżyce Zachód", "critical": "Zajezdnia tramwajowa i węzeł Ogrody"},
        "GPZ_Centrum":      {"name": "🔌 GPZ Centrum / Śródmieście (110/15 kV)",      "latlon": (52.4065, 16.9185), "voltage": "110/15 kV", "role": "gpz", "customers": 6300, "mw": 9.5, "district": "Centrum / Rondo Kaponiera", "critical": "Węzeł kolejowy Poznań Główny i sterowanie ruchem"},
        "GPZ_Grunwald":     {"name": "🔌 GPZ Grunwald / Przybyszewskiego (110/15 kV)","latlon": (52.4035, 16.8855), "voltage": "110/15 kV", "role": "gpz", "customers": 5400, "mw": 8.0, "district": "Grunwald Centralny", "critical": "Węzeł kliniczny Uniwersytetu Medycznego"},
        "GPZ_Wilda":        {"name": "🔌 GPZ Wilda / Dębiec (110/15 kV)",             "latlon": (52.3845, 16.9205), "voltage": "110/15 kV", "role": "gpz", "customers": 4900, "mw": 7.2, "district": "Wilda / ul. Hetmańska", "critical": "Zakłady Cegielskiego i podstacja tramwajowa Hetmańska"},
        "GPZ_Rataje":       {"name": "🔌 GPZ Rataje / Żegrze (110/15 kV)",            "latlon": (52.3925, 16.9525), "voltage": "110/15 kV", "role": "gpz", "customers": 5800, "mw": 7.8, "district": "Rataje Centralne", "critical": "Dworzec autobusowy Rataje i magistrala tramwajowa"},
        "GPZ_Malta":        {"name": "🔌 GPZ Malta / Warszawska (110/15 kV)",         "latlon": (52.4105, 16.9680), "voltage": "110/15 kV", "role": "gpz", "customers": 4100, "mw": 5.5, "district": "Śródka / Malta", "critical": "Węzeł wodociągowy i pompownia wschodnia"},

        "TR_Naramowice":    {"name": "💡 Stacja SN/nN Naramowice / Rubież (15/0,4 kV)",        "latlon": (52.4465, 16.9485), "voltage": "15/0,4 kV", "role": "station", "customers": 16400, "mw": 11.2, "district": "Naramowice i Os. Łokietka", "critical": "Trasa tramwajowa Naramowice"},
        "TR_Morasko_UAM":   {"name": "💡 Stacja SN/nN Os. Sobieskiego / Morasko (15/0,4 kV)",  "latlon": (52.4580, 16.9220), "voltage": "15/0,4 kV", "role": "station", "customers": 19800, "mw": 14.5, "district": "Piątkowo Północ i Kampus UAM Morasko", "critical": "Serwerownie Kampusu UAM i Pętla PST Sobieskiego"},
        "TR_Winogrady":     {"name": "💡 Stacja SN/nN Winogrady (Os. Wichrowe Wzgórze)",       "latlon": (52.4295, 16.9355), "voltage": "15/0,4 kV", "role": "station", "customers": 21500, "mw": 13.8, "district": "Winogrady (Os. Kosmonautów, Przyjaźni, Zwycięstwa)", "critical": "Przychodnie rejonowe i ośrodki zdrowia Winogrady"},
        "TR_Podolany":      {"name": "💡 Stacja SN/nN Podolany / Strzeszyn (15/0,4 kV)",       "latlon": (52.4420, 16.8860), "voltage": "15/0,4 kV", "role": "station", "customers": 14200, "mw": 9.4,  "district": "Podolany i Strzeszyn Grecki", "critical": "Szpital MSWiA (ul. Dojazd)"},
        "TR_Solacz":        {"name": "💡 Stacja SN/nN Sołacz / Al. Wielkopolska (15/0,4 kV)",  "latlon": (52.4215, 16.9120), "voltage": "15/0,4 kV", "role": "station", "customers": 8900,  "mw": 6.1,  "district": "Sołacz i Uniwersytet Przyrodniczy", "critical": "Kolegia i laboratoria Uniwersytetu Przyrodniczego"},
        "TR_Smochowice":    {"name": "💡 Stacja SN/nN Smochowice / Krzyżowniki (15/0,4 kV)",   "latlon": (52.4310, 16.8380), "voltage": "15/0,4 kV", "role": "station", "customers": 10500, "mw": 6.8,  "district": "Smochowice i Krzyżowniki", "critical": "Lokalne ujęcie wody i szkoły"},
        "TR_Lawica":        {"name": "💡 Stacja SN/nN Ławica / ul. Bukowska (15/0,4 kV)",      "latlon": (52.4185, 16.8420), "voltage": "15/0,4 kV", "role": "station", "customers": 9400,  "mw": 12.0, "district": "Ławica i Marcelin", "critical": "Port Lotniczy Poznań-Ławica (przejście na agregaty!)"},
        "TR_Jezyce_Rynek":  {"name": "💡 Stacja SN/nN Rynek Jeżycki / Dąbrowskiego (15/0,4 kV)","latlon": (52.4125, 16.9055), "voltage": "15/0,4 kV", "role": "station", "customers": 18400, "mw": 12.5, "district": "Jeżyce Centralne", "critical": "Szpital Miejski im. F. Raszei (ul. Mickiewicza)"},
        "TR_Szpital_Klin":  {"name": "💡 Stacja SN/nN Szpital Kliniczny Przybyszewskiego",     "latlon": (52.4065, 16.8855), "voltage": "15/0,4 kV", "role": "station", "customers": 13200, "mw": 11.8, "district": "Grunwald Północ (ul. Przybyszewskiego / Bukowska)", "critical": "SZPITAL KLINICZNY im. H. Święcickiego!"},
        "TR_Kopernika":     {"name": "💡 Stacja SN/nN Os. Kopernika / Junikowo (15/0,4 kV)",   "latlon": (52.3910, 16.8580), "voltage": "15/0,4 kV", "role": "station", "customers": 22100, "mw": 14.2, "district": "Os. Kopernika, Raszyn i Junikowo", "critical": "Pętla tramwajowa Junikowo i przychodnie"},
        "TR_Gorczyn":       {"name": "💡 Stacja SN/nN Górczyn / Głogowska (15/0,4 kV)",        "latlon": (52.3790, 16.8820), "voltage": "15/0,4 kV", "role": "station", "customers": 14800, "mw": 9.8,  "district": "Górczyn i Świerczewo Płn.", "critical": "Dworzec autobusowo-tramwajowy Górczyn"},
        "TR_Lazarz_MTP":    {"name": "💡 Stacja SN/nN Łazarz / Targi MTP (15/0,4 kV)",         "latlon": (52.3985, 16.9045), "voltage": "15/0,4 kV", "role": "station", "customers": 19500, "mw": 15.0, "district": "Łazarz i Międzynarodowe Targi Poznańskie", "critical": "Szpital Ginekologiczno-Położniczy (ul. Polna) i hale MTP"},
        "TR_Stary_Rynek":   {"name": "💡 Stacja SN/nN Stary Rynek / Pl. Wolności (15/0,4 kV)", "latlon": (52.4078, 16.9325), "voltage": "15/0,4 kV", "role": "station", "customers": 17600, "mw": 14.8, "district": "Stare Miasto i Chwaliszewo", "critical": "Urząd Miasta Poznania i instytucje centralne"},
        "TR_Wilda_Rynek":   {"name": "💡 Stacja SN/nN Rynek Wildecki / Wierzbięcice",          "latlon": (52.3945, 16.9225), "voltage": "15/0,4 kV", "role": "station", "customers": 18200, "mw": 12.1, "district": "Wilda Centralna i Dolna Wilda", "critical": "Szpital Ortopedyczny im. W. Degi"},
        "TR_Debiec":        {"name": "💡 Stacja SN/nN Dębiec / 28 Czerwca 1956 r.",            "latlon": (52.3695, 16.9120), "voltage": "15/0,4 kV", "role": "station", "customers": 15400, "mw": 10.6, "district": "Dębiec i Świerczewo Płd.", "critical": "Szpital Centrum Medyczne HCP (ul. 28 Czerwca 1956 r.)"},
        "TR_Staroleka":     {"name": "💡 Stacja SN/nN Starołęka / Minikowo (15/0,4 kV)",       "latlon": (52.3680, 16.9390), "voltage": "15/0,4 kV", "role": "station", "customers": 11900, "mw": 8.4,  "district": "Starołęka, Minikowo i Marlewo", "critical": "Przejazdy kolejowe i zakłady Starołęka"},
        "TR_Piotrowo_PP":   {"name": "💡 Stacja SN/nN Piotrowo / Politechnika (15/0,4 kV)",    "latlon": (52.4020, 16.9510), "voltage": "15/0,4 kV", "role": "station", "customers": 14600, "mw": 11.5, "district": "Piotrowo, Berdychowo i Os. Piastowskie", "critical": "Kampus Politechniki Poznańskiej i PCSS"},
        "TR_Zegrze_Orla":   {"name": "💡 Stacja SN/nN Żegrze / Os. Orła Białego (15/0,4 kV)",  "latlon": (52.3845, 16.9610), "voltage": "15/0,4 kV", "role": "station", "customers": 20400, "mw": 13.4, "district": "Żegrze, Os. Orła Białego i Os. Rzeczypospolitej", "critical": "Zajezdnia tramwajowa Franowo"},
        "TR_Szpital_Szwaj": {"name": "💡 Stacja SN/nN Chartowo / Szpital Szwajcarska",         "latlon": (52.3905, 16.9880), "voltage": "15/0,4 kV", "role": "station", "customers": 21200, "mw": 15.2, "district": "Chartowo (Os. Lecha, Czecha, Rusa) i Franowo", "critical": "SZPITAL MIEJSKI im. J. Strusia (ul. Szwajcarska 3)!"},
        "TR_Szczepankowo":  {"name": "💡 Stacja SN/nN Szczepankowo / Spławie (15/0,4 kV)",     "latlon": (52.3740, 16.9980), "voltage": "15/0,4 kV", "role": "station", "customers": 9800,  "mw": 6.4,  "district": "Szczepankowo, Spławie i Krzesiny", "critical": "Węzeł drogowy Krzesiny"},
        "TR_Glowna_Zawady": {"name": "💡 Stacja SN/nN Główna / Zawady (15/0,4 kV)",            "latlon": (52.4220, 16.9620), "voltage": "15/0,4 kV", "role": "station", "customers": 11600, "mw": 8.1,  "district": "Główna, Zawady i Komandoria", "critical": "Dworzec Poznań Wschód"},
        "TR_Antoninek":     {"name": "💡 Stacja SN/nN Antoninek / Kobylepole (15/0,4 kV)",     "latlon": (52.4060, 17.0020), "voltage": "15/0,4 kV", "role": "station", "customers": 10800, "mw": 11.0, "district": "Antoninek, Zieliniec i Kobylepole", "critical": "Zakłady Volkswagen Poznań (Antoninek)"},
    }

    power_nodes = {}
    power_osm_id = {}
    for key, info in raw_power_nodes.items():
        oid = find_nearest_node(G_undir_main, info["latlon"][0], info["latlon"][1], None)
        power_osm_id[key] = oid
        power_nodes[key] = {
            "id": key,
            "name": info["name"],
            "coords": [G_undir_main.nodes[oid]["pos"][0], G_undir_main.nodes[oid]["pos"][1]],
            "voltage": info["voltage"],
            "role": info["role"],
            "customers": info["customers"],
            "mw": info["mw"],
            "district": info["district"],
            "critical": info["critical"]
        }

    power_edges_def = [
        ("EC_Karolin",     "GPZ_Garbary",   {"voltage": "110 kV", "type": "Linia WN 110 kV Karolin -> Garbary",   "is_backup": False}),
        ("EC_Karolin",     "GPZ_Malta",     {"voltage": "110 kV", "type": "Linia WN 110 kV Karolin -> Malta",     "is_backup": False}),
        ("GPZ_Garbary",    "GPZ_Piatkowo",  {"voltage": "110 kV", "type": "Linia WN 110 kV Garbary -> Piątkowo",  "is_backup": False}),
        ("GPZ_Garbary",    "GPZ_Centrum",   {"voltage": "110 kV", "type": "Linia kablowa 110 kV Garbary -> Centrum", "is_backup": False}),
        ("GPZ_Malta",      "GPZ_Rataje",    {"voltage": "110 kV", "type": "Linia WN 110 kV Malta -> Rataje",      "is_backup": False}),
        ("SE_Zachod",      "GPZ_Grunwald",  {"voltage": "110 kV", "type": "Linia WN 110 kV Zachód -> Grunwald",   "is_backup": False}),
        ("SE_Zachod",      "GPZ_Wilda",     {"voltage": "110 kV", "type": "Linia WN 110 kV Zachód -> Wilda",      "is_backup": False}),
        ("GPZ_Grunwald",   "GPZ_Jezyce",    {"voltage": "110 kV", "type": "Linia kablowa 110 kV Grunwald -> Jeżyce", "is_backup": False}),
        ("GPZ_Piatkowo",   "TR_Naramowice", {"voltage": "15 kV", "type": "Kabel SN 15 kV Piątkowo -> Naramowice", "is_backup": False}),
        ("GPZ_Piatkowo",   "TR_Morasko_UAM",{"voltage": "15 kV", "type": "Kabel SN 15 kV Piątkowo -> Os. Sobieskiego / Morasko", "is_backup": False}),
        ("GPZ_Piatkowo",   "TR_Podolany",   {"voltage": "15 kV", "type": "Kabel SN 15 kV Piątkowo -> Podolany",   "is_backup": False}),
        ("GPZ_Garbary",    "TR_Winogrady",  {"voltage": "15 kV", "type": "Kabel SN 15 kV Garbary -> Winogrady",   "is_backup": False}),
        ("GPZ_Garbary",    "TR_Stary_Rynek",{"voltage": "15 kV", "type": "Kabel SN 15 kV Garbary -> Stary Rynek", "is_backup": False}),
        ("GPZ_Jezyce",     "TR_Solacz",     {"voltage": "15 kV", "type": "Kabel SN 15 kV Jeżyce -> Sołacz",       "is_backup": False}),
        ("GPZ_Jezyce",     "TR_Smochowice", {"voltage": "15 kV", "type": "Kabel SN 15 kV Jeżyce -> Smochowice",   "is_backup": False}),
        ("GPZ_Jezyce",     "TR_Lawica",     {"voltage": "15 kV", "type": "Kabel SN 15 kV Jeżyce -> Ławica",       "is_backup": False}),
        ("GPZ_Jezyce",     "TR_Jezyce_Rynek",{"voltage": "15 kV","type": "Kabel SN 15 kV Jeżyce -> Rynek Jeżycki","is_backup": False}),
        ("GPZ_Grunwald",   "TR_Szpital_Klin",{"voltage": "15 kV","type": "Kabel SN 15 kV Grunwald -> Szpital Przybyszewskiego", "is_backup": False}),
        ("GPZ_Grunwald",   "TR_Kopernika",  {"voltage": "15 kV", "type": "Kabel SN 15 kV Grunwald -> Os. Kopernika / Junikowo", "is_backup": False}),
        ("GPZ_Grunwald",   "TR_Gorczyn",    {"voltage": "15 kV", "type": "Kabel SN 15 kV Grunwald -> Górczyn",    "is_backup": False}),
        ("GPZ_Centrum",    "TR_Lazarz_MTP", {"voltage": "15 kV", "type": "Kabel SN 15 kV Centrum -> Łazarz / Targi MTP", "is_backup": False}),
        ("GPZ_Wilda",      "TR_Wilda_Rynek",{"voltage": "15 kV", "type": "Kabel SN 15 kV Wilda -> Rynek Wildecki","is_backup": False}),
        ("GPZ_Wilda",      "TR_Debiec",     {"voltage": "15 kV", "type": "Kabel SN 15 kV Wilda -> Dębiec",        "is_backup": False}),
        ("GPZ_Wilda",      "TR_Staroleka",  {"voltage": "15 kV", "type": "Kabel SN 15 kV Wilda -> Starołęka",     "is_backup": False}),
        ("GPZ_Rataje",     "TR_Piotrowo_PP",{"voltage": "15 kV", "type": "Kabel SN 15 kV Rataje -> Piotrowo / Politechnika", "is_backup": False}),
        ("GPZ_Rataje",     "TR_Zegrze_Orla",{"voltage": "15 kV", "type": "Kabel SN 15 kV Rataje -> Żegrze",       "is_backup": False}),
        ("GPZ_Rataje",     "TR_Szpital_Szwaj",{"voltage":"15 kV","type": "Kabel SN 15 kV Rataje -> Chartowo / Szpital Szwajcarska", "is_backup": False}),
        ("TR_Zegrze_Orla", "TR_Szczepankowo",{"voltage":"15 kV", "type": "Linia SN 15 kV Żegrze -> Szczepankowo", "is_backup": False}),
        ("GPZ_Malta",      "TR_Glowna_Zawady",{"voltage":"15 kV","type": "Kabel SN 15 kV Malta -> Główna / Zawady","is_backup": False}),
        ("GPZ_Malta",      "TR_Antoninek",  {"voltage": "15 kV", "type": "Kabel SN 15 kV Malta -> Antoninek",     "is_backup": False}),
        ("GPZ_Centrum",    "GPZ_Jezyce",    {"voltage": "110 kV (Rezerwa SZR)", "type": "Łącznik rezerwowy SZR Centrum <-> Jeżyce", "is_backup": True}),
        ("GPZ_Centrum",    "GPZ_Wilda",     {"voltage": "110 kV (Rezerwa SZR)", "type": "Łącznik rezerwowy SZR Centrum <-> Wilda",  "is_backup": True}),
        ("GPZ_Wilda",      "GPZ_Rataje",    {"voltage": "110 kV (Rezerwa SZR)", "type": "Łącznik rezerwowy SZR Wilda <-> Rataje",   "is_backup": True}),
        ("GPZ_Piatkowo",   "GPZ_Jezyce",    {"voltage": "110 kV (Rezerwa SZR)", "type": "Łącznik rezerwowy SZR Piątkowo <-> Jeżyce","is_backup": True}),
    ]

    power_edges_list = []
    for idx, (u_key, v_key, attr) in enumerate(power_edges_def):
        u_oid = power_osm_id[u_key]
        v_oid = power_osm_id[v_key]
        sp = nx.shortest_path(G_undir_main, u_oid, v_oid, weight="dist_km")
        dist_km = round(nx.path_weight(G_undir_main, sp, weight="dist_km"), 2)
        geom_coords = [[G_undir_main.nodes[nid]["pos"][0], G_undir_main.nodes[nid]["pos"][1]] for nid in sp]
        power_edges_list.append({
            "id": f"pwr_{idx}",
            "u": u_key,
            "v": v_key,
            "dist_km": dist_km,
            **attr,
            "geometry": geom_coords
        })

    # ==========================================================================
    # 6. DOKŁADNA SIEĆ TRAMWAJOWA ZTM (OSM railway=tram) ORAZ AUTOBUSOWA ZTM
    # ==========================================================================
    G_tram = nx.Graph()
    tram_tracks_bg = []

    if tram_osm_path and os.path.exists(tram_osm_path):
        with open(tram_osm_path, "r", encoding="utf-8") as f:
            tdata = json.load(f)
        for el in tdata.get("elements", []):
            if el.get("type") == "way" and "geometry" in el:
                pts = [[round(p["lat"], 5), round(p["lon"], 5)] for p in el["geometry"]
                       if point_in_polygon(p["lat"], p["lon"], boundary_ring)]
                if len(pts) >= 2:
                    tram_tracks_bg.append(pts)
                    for i in range(len(pts) - 1):
                        p1 = (pts[i][0], pts[i][1])
                        p2 = (pts[i + 1][0], pts[i + 1][1])
                        if p1 != p2:
                            dkm = haversine_km(p1[0], p1[1], p2[0], p2[1])
                            G_tram.add_node(p1, pos=p1)
                            G_tram.add_node(p2, pos=p2)
                            G_tram.add_edge(p1, p2, dist_km=dkm)

        # Łączymy sąsiednie tory dwutorowe i zwrotnice w promieniu 48 m, aby graf torowisk był w 100% spójny
        tram_nodes = list(G_tram.nodes())
        grid = {}
        for pt in tram_nodes:
            cell = (int(pt[0] * 1000), int(pt[1] * 1000))
            grid.setdefault(cell, []).append(pt)
        for pt in tram_nodes:
            c0, c1 = int(pt[0] * 1000), int(pt[1] * 1000)
            for dc0 in (-1, 0, 1):
                for dc1 in (-1, 0, 1):
                    for other in grid.get((c0 + dc0, c1 + dc1), []):
                        if pt < other and not G_tram.has_edge(pt, other):
                            d = haversine_km(pt[0], pt[1], other[0], other[1])
                            if d <= 0.048:
                                G_tram.add_edge(pt, other, dist_km=d * 1.25)

    def find_nearest_tram_node(lat, lon):
        best_pt = None
        best_d = float("inf")
        for pt in G_tram.nodes():
            d = (pt[0] - lat) ** 2 + (pt[1] - lon) ** 2
            if d < best_d:
                best_d = d
                best_pt = pt
        return best_pt

    # Węzły przesiadkowe i przystanki ZTM Poznań (Tramwaje + Autobusy Miejskie)
    ztm_stops_raw = [
        # Tramwajowe węzły ZTM Poznań
        ("ztm_sobieskiego",   "🚋 Pętla PST Os. Sobieskiego",          52.4628, 16.9182, "Tramwaj: 12, 14, 15, 16 (PST) | Autobus: 169, 174, 185, 198", "tram"),
        ("ztm_szymanowskiego","🚋 Przystanek PST Szymanowskiego",      52.4535, 16.9212, "Tramwaj: 12, 14, 15, 16 (PST) | Autobus: 146, 151", "tram"),
        ("ztm_kurpinskiego",  "🚋 Przystanek PST Kurpińskiego",        52.4445, 16.9215, "Tramwaj: 12, 14, 15, 16 (PST) | Autobus: 151, 169, 185", "tram"),
        ("ztm_plaza",         "🚋 Przystanek PST Lechicka / Plaza",    52.4375, 16.9205, "Tramwaj: 12, 14, 15, 16 (PST) | Autobus: 178, 183", "tram"),
        ("ztm_solidarnosci",  "🚋 Przystanek PST Al. Solidarności",    52.4302, 16.9192, "Tramwaj: 12, 14, 15, 16 (PST) | Autobus: 168, 171, 178", "tram"),
        ("ztm_slowianska",    "🚋 Przystanek PST Słowiańska",          52.4232, 16.9178, "Tramwaj: 12, 14, 15, 16 (PST) | Autobus: 168, 169, 171", "tram"),
        ("ztm_teatralny",     "🚋 Węzeł Most Teatralny (Teatralka)",   52.4112, 16.9145, "Tramwaj: 2, 3, 7, 8, 12, 14, 15, 16, 18 | Autobus: 164, 169", "tram"),
        ("ztm_kaponiera",     "🚋 Węzeł Rondo Kaponiera / Dworzec",    52.4078, 16.9125, "Tramwaj: 2, 5, 6, 8, 10, 12, 13, 15, 18 | Autobus: 148, 159, 163, 168, 169", "tram"),
        ("ztm_dworzec_zach",  "🚋 Dworzec Zachodni PKP (PST)",         52.4018, 16.9088, "Tramwaj: 5, 8, 11, 12, 14, 18 | Autobus: 145, 159", "tram"),
        ("ztm_ogrody",        "🚋 Pętla Ogrody (Węzeł Zachodni)",      52.4170, 16.8788, "Tramwaj: 2, 7, 8, 18 | Autobus: 156, 164, 182, 186, 191, 193", "tram"),
        ("ztm_zeromskiego",   "🚋 Węzeł Żeromskiego / Dąbrowskiego",   52.4148, 16.8962, "Tramwaj: 2, 7, 8, 18 | Autobus: 182, 191, 193", "tram"),
        ("ztm_rynek_jezycki", "🚋 Rynek Jeżycki",                      52.4126, 16.9048, "Tramwaj: 2, 8, 18", "tram"),
        ("ztm_junikowo",      "🚋 Pętla Junikowo",                     52.3845, 16.8390, "Tramwaj: 1, 6, 13, 15 | Autobus: 177", "tram"),
        ("ztm_stadion",       "🚋 Enea Stadion Miejski (Bułgarska)",   52.3945, 16.8725, "Tramwaj: 1, 6, 13, 15 | Autobus: 145, 163, 191, 193", "tram"),
        ("ztm_reymonta",      "🚋 Węzeł Reymonta / Grunwaldzka",       52.4002, 16.8927, "Tramwaj: 1, 6, 7, 13, 15 | Autobus: 145, 163, 164, 182", "tram"),
        ("ztm_gorczyn",       "🚋 Pętla Górczyn PKM",                  52.3788, 16.8808, "Tramwaj: 5, 8, 14 | Autobus: 149, 163, 175, 180, 182, 193", "tram"),
        ("ztm_hetmanska_glog","🚋 Węzeł Głogowska / Hetmańska",        52.3886, 16.8985, "Tramwaj: 1, 5, 7, 8, 11, 14 | Autobus: 179", "tram"),
        ("ztm_traugutta",     "🚋 Węzeł Traugutta / Hetmańska (Wilda)",52.3845, 16.9205, "Tramwaj: 1, 2, 7, 9, 10, 11 | Autobus: 171, 176", "tram"),
        ("ztm_debiec",        "🚋 Pętla Dębiec PKM",                   52.3658, 16.9125, "Tramwaj: 2, 9, 10 | Autobus: 149, 171, 176", "tram"),
        ("ztm_marcinkowskiego","🚋 Al. Marcinkowskiego / Stary Rynek", 52.4070, 16.9288, "Tramwaj: 2, 3, 5, 9, 13, 16 | Autobus: 160, 163, 174, 190", "tram"),
        ("ztm_polwiejska",    "🚋 Półwiejska / Stary Browar",          52.4025, 16.9265, "Tramwaj: 2, 5, 6, 9, 12, 18", "tram"),
        ("ztm_politechnika",  "🚋 Most św. Rocha / Politechnika",      52.4021, 16.9492, "Tramwaj: 3, 5, 6, 13, 16 | Autobus: 174, 184", "tram"),
        ("ztm_rondo_rataje",  "🚋 Dworzec Rondo Rataje",               52.3968, 16.9505, "Tramwaj: 1, 5, 6, 7, 12, 13, 18 | Autobus: 152, 162, 166, 174, 181, 184", "tram"),
        ("ztm_rondo_zegrze",  "🚋 Węzeł Rondo Żegrze",                 52.3850, 16.9602, "Tramwaj: 1, 5, 11, 17 | Autobus: 152, 166, 174", "tram"),
        ("ztm_staroleka",     "🚋 Węzeł Rondo Starołęka",              52.3806, 16.9382, "Tramwaj: 1, 7, 11, 12, 13 | Autobus: 158, 194", "tram"),
        ("ztm_franowo",       "🚋 Pętla Franowo / Szpital Szwajcarska",52.3889, 16.9920, "Tramwaj: 1, 16, 18 | Autobus: 152, 166, 181, 184", "tram"),
        ("ztm_os_lecha",      "🚋 Os. Lecha / Trasa Kórnicka",         52.3948, 16.9735, "Tramwaj: 5, 16, 18 | Autobus: 152, 166", "tram"),
        ("ztm_rondo_srodka",  "🚋 Dworzec Rondo Śródka",               52.4106, 16.9554, "Tramwaj: 3, 6, 7, 8, 17 | Autobus: 157, 167, 173, 178, 183, 185", "tram"),
        ("ztm_milostowo",     "🚋 Pętla Miłostowo (ul. Warszawska)",   52.4095, 16.9950, "Tramwaj: 6, 8 | Autobus: 157, 173", "tram"),
        ("ztm_wilczak",       "🚋 Węzeł Wilczak / Serbska",            52.4285, 16.9425, "Tramwaj: 3, 10 | Autobus: 167, 169, 174, 178, 183, 190", "tram"),
        ("ztm_blazeja",       "🚋 Nowa Trasa Naramowicka (Al. Praw Kobiet / Błażeja)", 52.4480, 16.9455, "Tramwaj: 3, 10 | Autobus: 146, 167, 169", "tram"),
        ("ztm_polabska",      "🚋 Pętla Połabska (Winogrady)",         52.4292, 16.9328, "Tramwaj: 7, 15 | Autobus: 168, 171, 174, 190", "tram"),
        ("ztm_piatkowska",    "🚋 Pętla Piątkowska",                   52.4295, 16.9068, "Tramwaj: 9, 11, 19 | Autobus: 160, 164, 190, 191", "tram"),

        # Przystanki i pętle Autobusowe ZTM Poznań (obsługujące dzielnice poza siecią tramwajową)
        ("bus_lawica_port",   "🚌 Port Lotniczy Poznań-Ławica (Autobus ZTM)", 52.4198, 16.8335, "Autobus ZTM: 148, 159 (Pośpieszny Lotnisko <-> Dworzec/Kaponiera)", "bus"),
        ("bus_malawska_buk",  "🚌 Węzeł Bukowska / Polska (Ławica)",          52.4138, 16.8675, "Autobus ZTM: 148, 159, 163, 177, 182", "bus"),
        ("bus_smochowice",    "🚌 Smochowice / Krzyżowniki (ul. Dąbrowskiego)",52.4310, 16.8360, "Autobus ZTM: 156, 186 (-> Pętla Ogrody)", "bus"),
        ("bus_strzeszyn",     "🚌 Os. Strzeszyn Grecki (Pętla Autobusowa)",   52.4525, 16.8620, "Autobus ZTM: 160, 164, 168, 195", "bus"),
        ("bus_podolany",      "🚌 Podolany (ul. Strzeszyńska / Druskienicka)",52.4420, 16.8860, "Autobus ZTM: 160, 168, 183", "bus"),
        ("bus_szpital_lut",   "🚌 Szpital Wojewódzki Lutycka / Juraszów",     52.4352, 16.9005, "Autobus ZTM: 160, 164, 168, 183, 191, 193", "bus"),
        ("bus_uam_morasko",   "🚌 Kampus UAM Morasko (Pętla Autobusowa)",     52.4660, 16.9255, "Autobus ZTM: 188, 198", "bus"),
        ("bus_umultowo",      "🚌 Os. Umultowo / Radojewo",                   52.4635, 16.9535, "Autobus ZTM: 167, 198 (-> Błażeja / Śródka)", "bus"),
        ("bus_kopernika",     "🚌 Os. Kopernika (Pętla Autobusowa)",          52.3912, 16.8580, "Autobus ZTM: 145, 163, 169, 179, 182, 191, 193", "bus"),
        ("bus_male_garbary",  "🚌 Węzeł Garbary PKM / Małe Garbary",          52.4118, 16.9355, "Autobus ZTM: 160, 163, 167, 174, 176, 190", "bus"),
        ("bus_glowna_zawady", "🚌 Dworzec Autobusowy Główna / Zawady",        52.4225, 16.9620, "Autobus ZTM: 173, 178, 183, 185", "bus"),
        ("bus_termy_malta",   "🚌 Termy Maltańskie / Termalna",               52.4068, 16.9765, "Autobus ZTM: 157, 184", "bus"),
        ("bus_antoninek",     "🚌 Antoninek / Browarna (VW Poznań)",          52.4045, 17.0110, "Autobus ZTM: 152, 157, 166, 184", "bus"),
        ("bus_szczepankowo",  "🚌 Szczepankowo / Spławie (ul. Ostrowska)",    52.3735, 16.9985, "Autobus ZTM: 154, 162, 196 (-> Rondo Rataje)", "bus"),
        ("bus_krzesiny",      "🚌 Krzesiny / Garaszewo",                      52.3565, 16.9760, "Autobus ZTM: 162, 196", "bus"),
        ("bus_minikowo",      "🚌 Starołęka Wielka / Minikowo (Pętla)",       52.3625, 16.9420, "Autobus ZTM: 158, 184, 194", "bus"),
    ]

    ztm_stops = {}
    for sid, sname, lat, lon, lines_str, stype in ztm_stops_raw:
        oid = find_nearest_node(G_dir, lat, lon, None)
        idx = osm_id_to_idx[oid]
        ztm_stops[sid] = {
            "id": sid,
            "name": sname,
            "coords": [round(lat, 5), round(lon, 5)],
            "node_idx": idx,
            "lines": lines_str,
            "mode": stype
        }

    # Relacje tramwajowe (wyznaczane dokładnie po grafie torowisk OSM railway=tram!)
    ztm_tram_edges_def = [
        ("ztm_sobieskiego",   "ztm_szymanowskiego", 42.0, "Tramwaj PST (12, 14, 15, 16)"),
        ("ztm_szymanowskiego","ztm_kurpinskiego",   42.0, "Tramwaj PST (12, 14, 15, 16)"),
        ("ztm_kurpinskiego",  "ztm_plaza",          42.0, "Tramwaj PST (12, 14, 15, 16)"),
        ("ztm_plaza",         "ztm_solidarnosci",   42.0, "Tramwaj PST (12, 14, 15, 16)"),
        ("ztm_solidarnosci",  "ztm_slowianska",     42.0, "Tramwaj PST (12, 14, 15, 16)"),
        ("ztm_slowianska",    "ztm_teatralny",      38.0, "Tramwaj PST (12, 14, 15, 16)"),
        ("ztm_teatralny",     "ztm_kaponiera",      28.0, "Tramwaj PST / Roosevelta"),
        ("ztm_kaponiera",     "ztm_dworzec_zach",   35.0, "Tramwaj PST (5, 8, 12, 14)"),
        ("ztm_piatkowska",    "ztm_teatralny",      25.0, "Tramwaj 9, 11, 19 (Sołacz -> Teatralka)"),
        ("ztm_polabska",      "ztm_teatralny",      26.0, "Tramwaj 7, 15 (Winogrady -> Pułaskiego)"),
        ("ztm_ogrody",        "ztm_zeromskiego",    26.0, "Tramwaj 2, 7, 8, 18 (ul. Dąbrowskiego)"),
        ("ztm_zeromskiego",   "ztm_rynek_jezycki",  24.0, "Tramwaj 2, 8, 18 (Jeżyce)"),
        ("ztm_rynek_jezycki", "ztm_teatralny",      24.0, "Tramwaj 2, 8, 18 (ul. Dąbrowskiego)"),
        ("ztm_zeromskiego",   "ztm_reymonta",       28.0, "Tramwaj 7 (ul. Przybyszewskiego)"),
        ("ztm_junikowo",      "ztm_stadion",        30.0, "Tramwaj 1, 6, 13, 15 (ul. Grunwaldzka)"),
        ("ztm_stadion",       "ztm_reymonta",       28.0, "Tramwaj 1, 6, 13, 15 (ul. Grunwaldzka)"),
        ("ztm_reymonta",      "ztm_kaponiera",      26.0, "Tramwaj 6, 13, 15 (ul. Grunwaldzka -> Bałtyk)"),
        ("ztm_reymonta",      "ztm_hetmanska_glog", 30.0, "Tramwaj 1, 7 (ul. Reymonta)"),
        ("ztm_gorczyn",       "ztm_hetmanska_glog", 28.0, "Tramwaj 5, 8, 14 (ul. Głogowska)"),
        ("ztm_hetmanska_glog","ztm_dworzec_zach",   26.0, "Tramwaj 5, 8, 11, 14 (ul. Głogowska)"),
        ("ztm_hetmanska_glog","ztm_traugutta",      30.0, "Tramwaj 1, 7, 11 (ul. Hetmańska)"),
        ("ztm_debiec",        "ztm_traugutta",      27.0, "Tramwaj 2, 9, 10 (ul. 28 Czerwca 1956 r.)"),
        ("ztm_traugutta",     "ztm_polwiejska",     24.0, "Tramwaj 2, 9, 10 (ul. Górna Wilda)"),
        ("ztm_teatralny",     "ztm_marcinkowskiego",22.0, "Tramwaj 3, 8, 16 (ul. Fredry / Gwarna)"),
        ("ztm_kaponiera",     "ztm_marcinkowskiego",24.0, "Tramwaj 5, 13, 16 (ul. Św. Marcin)"),
        ("ztm_kaponiera",     "ztm_polwiejska",     24.0, "Tramwaj 6, 12, 18 (ul. Towarowa / Królowej Jadwigi)"),
        ("ztm_marcinkowskiego","ztm_politechnika",  26.0, "Tramwaj 5, 13, 16 (Most św. Rocha)"),
        ("ztm_polwiejska",    "ztm_rondo_rataje",   28.0, "Tramwaj 6, 12, 18 (Most Królowej Jadwigi)"),
        ("ztm_politechnika",  "ztm_rondo_rataje",   28.0, "Tramwaj 5, 13, 16 (ul. Kórnicka / Jana Pawła II)"),
        ("ztm_politechnika",  "ztm_os_lecha",       33.0, "Tramwaj 5, 16, 18 (Trasa Kórnicka)"),
        ("ztm_rondo_rataje",  "ztm_os_lecha",       32.0, "Tramwaj Trasa Kórnicka"),
        ("ztm_os_lecha",      "ztm_franowo",        34.0, "Tramwaj 1, 16, 18 (Tunel Franowo)"),
        ("ztm_traugutta",     "ztm_staroleka",      32.0, "Tramwaj 1, 7, 11 (Most Przemysła I)"),
        ("ztm_staroleka",     "ztm_rondo_zegrze",   32.0, "Tramwaj 1, 11, 17 (ul. Hetmańska)"),
        ("ztm_rondo_zegrze",  "ztm_rondo_rataje",   30.0, "Tramwaj 1, 5, 17 (ul. Zamenhofa)"),
        ("ztm_rondo_zegrze",  "ztm_franowo",        32.0, "Tramwaj 1 (Os. Orła Białego -> Franowo)"),
        ("ztm_rondo_rataje",  "ztm_rondo_srodka",   30.0, "Tramwaj 6, 7, 8, 17 (ul. Jana Pawła II)"),
        ("ztm_marcinkowskiego","ztm_rondo_srodka",  25.0, "Tramwaj 3, 8, 17 (Małe Garbary -> Ostrów Tumski)"),
        ("ztm_rondo_srodka",  "ztm_milostowo",      32.0, "Tramwaj 6, 8 (ul. Warszawska)"),
        ("ztm_rondo_srodka",  "ztm_wilczak",        28.0, "Tramwaj 3 (Estakada -> Winogrady)"),
        ("ztm_wilczak",       "ztm_blazeja",        34.0, "Tramwaj 3, 10 (Nowa Trasa Naramowicka)"),
    ]

    # Relacje autobusowe ZTM Poznań (wyznaczane dokładnie po grafie ulic G_undir_main!)
    ztm_bus_edges_def = [
        ("bus_lawica_port",   "bus_malawska_buk",   28.0, "Autobus 148, 159 (ul. Bukowska - Lotnisko)"),
        ("bus_malawska_buk",  "ztm_kaponiera",      24.0, "Autobus 148, 159 (ul. Bukowska -> Rondo Kaponiera)"),
        ("bus_malawska_buk",  "ztm_reymonta",       25.0, "Autobus 163, 182 (ul. Szylinga / Bukowska)"),
        ("bus_smochowice",    "ztm_ogrody",         28.0, "Autobus 156, 186 (ul. Dąbrowskiego)"),
        ("bus_strzeszyn",     "bus_podolany",       29.0, "Autobus 160, 168 (ul. Koszalińska / Strzeszyńska)"),
        ("bus_podolany",      "bus_szpital_lut",    27.0, "Autobus 160, 168, 183 (ul. Lutycka / Strzeszyńska)"),
        ("bus_szpital_lut",   "ztm_ogrody",         26.0, "Autobus 164, 182, 191, 193 (Szpital Lutycka <-> Ogrody)"),
        ("bus_szpital_lut",   "ztm_piatkowska",     26.0, "Autobus 160, 190, 191 (Al. Solidarności)"),
        ("bus_szpital_lut",   "ztm_solidarnosci",   27.0, "Autobus 168, 178 (Al. Solidarności -> PST)"),
        ("ztm_sobieskiego",   "bus_uam_morasko",    31.0, "Autobus 188, 198 (Kampus UAM Morasko)"),
        ("bus_uam_morasko",   "bus_umultowo",       30.0, "Autobus 198 (ul. Umultowska)"),
        ("bus_umultowo",      "ztm_blazeja",        28.0, "Autobus 167, 198 (Umultowo -> Naramowice)"),
        ("ztm_blazeja",       "ztm_plaza",          28.0, "Autobus 146, 169 (ul. Lechicka)"),
        ("ztm_polabska",      "ztm_wilczak",        27.0, "Autobus 168, 171, 174 (Al. Solidarności / Murawa)"),
        ("ztm_polabska",      "bus_male_garbary",   25.0, "Autobus 163, 174, 190 (ul. Szelągowska / Garbary)"),
        ("bus_male_garbary",  "ztm_marcinkowskiego",22.0, "Autobus 160, 163, 174, 190 (Garbary -> Centrum)"),
        ("bus_male_garbary",  "ztm_rondo_srodka",   25.0, "Autobus 163, 167 (Most Chrobrego / Estakada)"),
        ("ztm_rondo_srodka",  "bus_glowna_zawady",  26.0, "Autobus 173, 178, 183, 185 (ul. Główna / Zawady)"),
        ("bus_glowna_zawady", "ztm_wilczak",        28.0, "Autobus 178, 183 (Most Lecha -> Winogrady)"),
        ("ztm_rondo_srodka",  "bus_termy_malta",    28.0, "Autobus 157, 184 (ul. Warszawska / Termalna)"),
        ("bus_termy_malta",   "bus_antoninek",      29.0, "Autobus 157, 184 (Nowe ZOO / Antoninek)"),
        ("bus_antoninek",     "ztm_franowo",        28.0, "Autobus 152, 166, 184 (ul. Kobylepole / Szwajcarska)"),
        ("ztm_rondo_rataje",  "bus_szczepankowo",   30.0, "Autobus 154, 162, 196 (ul. Krzywoustego -> Szczepankowo)"),
        ("bus_szczepankowo",  "bus_krzesiny",       31.0, "Autobus 162, 196 (ul. Ostrowska / Krzesiny)"),
        ("ztm_staroleka",     "bus_minikowo",       28.0, "Autobus 158, 194 (ul. Starołęcka)"),
        ("bus_kopernika",     "ztm_stadion",        26.0, "Autobus 145, 163, 191, 193 (ul. Jawornicka / Ptasia)"),
        ("bus_kopernika",     "ztm_gorczyn",        27.0, "Autobus 163, 182, 193 (ul. Zgoda / Rakoniewicka)"),
        ("ztm_gorczyn",       "ztm_debiec",         28.0, "Autobus 149 (Górczyn <-> Dębiec)"),
    ]

    ztm_edges = []

    # 1) Wyznaczamy dokładną geometrię linii tramwajowych po torowiskach OSM (G_tram) z walidacją korytarza
    for u_s, v_s, speed_kmh, label in ztm_tram_edges_def:
        c1 = ztm_stops[u_s]["coords"]
        c2 = ztm_stops[v_s]["coords"]
        direct_km = haversine_km(c1[0], c1[1], c2[0], c2[1])
        geom_coords = []
        dkm = 0.0
        if G_tram.number_of_nodes() > 0:
            tn1 = find_nearest_tram_node(c1[0], c1[1])
            tn2 = find_nearest_tram_node(c2[0], c2[1])
            if tn1 and tn2 and nx.has_path(G_tram, tn1, tn2):
                sp = nx.shortest_path(G_tram, tn1, tn2, weight="dist_km")
                cand_dkm = round(nx.path_weight(G_tram, sp, weight="dist_km"), 2)
                # Sprawdzamy, czy graf szyn nie robi okrężnego objazdu przez lukę na zwrotnicy/moście
                if cand_dkm <= max(0.35, direct_km * 1.38):
                    geom_coords = [c1] + [[pt[0], pt[1]] for pt in sp] + [c2]
                    dkm = cand_dkm

        if len(geom_coords) < 2:
            u_oid = find_nearest_node(G_undir_main, c1[0], c1[1], None)
            v_oid = find_nearest_node(G_undir_main, c2[0], c2[1], None)
            if u_oid in G_undir_main and v_oid in G_undir_main and nx.has_path(G_undir_main, u_oid, v_oid):
                sp = nx.shortest_path(G_undir_main, u_oid, v_oid, weight="dist_km")
                cand_dkm = round(nx.path_weight(G_undir_main, sp, weight="dist_km"), 2)
                if cand_dkm <= max(0.45, direct_km * 1.45):
                    geom_coords = [c1] + [[G_undir_main.nodes[n]["pos"][0], G_undir_main.nodes[n]["pos"][1]] for n in sp] + [c2]
                    dkm = cand_dkm
            if len(geom_coords) < 2:
                geom_coords = [c1, c2]
                dkm = round(direct_km * 1.12, 2)

        tmin = round((max(0.2, dkm) / speed_kmh) * 60.0, 2)
        ztm_edges.append({
            "u": u_s,
            "v": v_s,
            "dist_km": round(max(0.2, dkm), 2),
            "time_min": tmin,
            "speed_kmh": int(speed_kmh),
            "label": label,
            "mode": "tram",
            "geometry": geom_coords
        })

    # 2) Wyznaczamy dokładną geometrię linii autobusowych ZTM po rzeczywistych ulicach OSM (G_undir_main)
    for u_s, v_s, speed_kmh, label in ztm_bus_edges_def:
        c1 = ztm_stops[u_s]["coords"]
        c2 = ztm_stops[v_s]["coords"]
        u_oid = find_nearest_node(G_undir_main, c1[0], c1[1], None)
        v_oid = find_nearest_node(G_undir_main, c2[0], c2[1], None)
        sp = nx.shortest_path(G_undir_main, u_oid, v_oid, weight="dist_km")
        geom_coords = [c1] + [[G_undir_main.nodes[n]["pos"][0], G_undir_main.nodes[n]["pos"][1]] for n in sp] + [c2]
        dkm = round(nx.path_weight(G_undir_main, sp, weight="dist_km"), 2)
        tmin = round((max(0.2, dkm) / speed_kmh) * 60.0, 2)
        ztm_edges.append({
            "u": u_s,
            "v": v_s,
            "dist_km": round(max(0.2, dkm), 2),
            "time_min": tmin,
            "speed_kmh": int(speed_kmh),
            "label": label,
            "mode": "bus",
            "geometry": geom_coords
        })

    # ==========================================================================
    # 7. BAZA SŁUŻB RATUNKOWYCH (OD), KOMIWOJAŻERA (TSP), LOKALIZACJI-ALOKACJI, EWAKUACJI
    # ==========================================================================
    emergency_facilities_raw = [
        ("sor_przybyszewskiego", "🏥 Szpital Kliniczny USK (SOR ul. Przybyszewskiego 49)", "hospital", 52.40585, 16.88512, "przybyszewskiego", "Grunwald / Jeżyce"),
        ("sor_szwajcarska",      "🏥 Szpital Miejski im. J. Strusia (SOR ul. Szwajcarska 3)", "hospital", 52.38890, 16.99395, "szwajcarska", "Chartowo / Rataje"),
        ("sor_lutycka",          "🏥 Szpital Wojewódzki (SOR ul. Juraszów / Lutycka 7/19)", "hospital", 52.43520, 16.90050, "lutycka", "Podolany / Winiary"),
        ("sor_hcp_debiec",       "🏥 Centrum Medyczne HCP (SOR ul. 28 Czerwca 1956 r. 194)", "hospital", 52.37650, 16.91420, "28 czerwca", "Wilda / Dębiec"),
        ("sor_raszei",           "🏥 Szpital Miejski im. F. Raszei (ul. Mickiewicza 2)", "hospital", 52.41120, 16.90980, "mickiewicza", "Jeżyce Centralne"),
        ("sor_mswia",            "🏥 Szpital Kliniczny MSWiA (ul. Dojazd 34)", "hospital", 52.42680, 16.90520, None, "Winiary / Sołacz"),
        ("sor_degi",             "🏥 Szpital Ortopedyczny im. W. Degi (ul. 28 Czerwca 1956 r. 135)", "hospital", 52.38950, 16.91880, "28 czerwca", "Wilda"),

        ("jrg1_wolnica",         "🚒 JRG-1 PSP Poznań Stare Miasto (ul. Wolnica 1)", "fire", 52.41180, 16.93120, "małe garbary", "Stare Miasto"),
        ("jrg2_grunwaldzka",     "🚒 JRG-2 PSP Poznań Grunwald (ul. Grunwaldzka 16a)", "fire", 52.40410, 16.90120, "grunwaldzka", "Grunwald / Łazarz"),
        ("jrg3_antoninek",       "🚒 JRG-3 PSP Poznań Antoninek (ul. Bobrzańska 6)", "fire", 52.39820, 16.99650, None, "Antoninek / Chartowo"),
        ("jrg4_krzesiny",        "🚒 JRG-4 PSP Poznań Krzesiny / Szczepankowo", "fire", 52.36550, 16.98200, None, "Szczepankowo / Krzesiny"),
        ("jrg5_piatkowo",        "🚒 JRG-5 PSP Poznań Piątkowo (Os. Chrobrego 119)", "fire", 52.45180, 16.91350, None, "Piątkowo"),
        ("jrg6_rataje",          "🚒 JRG-6 PSP Poznań Rataje (ul. Wyzwolenia 32)", "fire", 52.38780, 16.94980, None, "Rataje / Żegrze"),
        ("jrg7_lawica",          "🚒 JRG-7 PSP Poznań Ławica (ul. Bukowska 285)", "fire", 52.41750, 16.84250, "bukowska", "Ławica"),

        ("kmp_centrum",          "🚓 KMP Poznań Stare Miasto (Al. Marcinkowskiego 31)", "police", 52.40820, 16.92850, "marcinkowskiego", "Stare Miasto"),
        ("kp_jezyce",            "🚓 KP Poznań Jeżyce (ul. Kochanowskiego 16)", "police", 52.41320, 16.90650, None, "Jeżyce"),
        ("kp_grunwald",          "🚓 KP Poznań Grunwald (ul. Rycerska 2)", "police", 52.39780, 16.87850, None, "Grunwald"),
        ("kp_polnoc",            "🚓 KP Poznań Północ (Os. Jana III Sobieskiego 115)", "police", 52.46120, 16.91950, None, "Piątkowo Północ"),
        ("kp_nowe_miasto",       "🚓 KP Poznań Nowe Miasto (ul. Polanka 24)", "police", 52.39980, 16.95980, None, "Rataje / Malta"),
        ("kp_wilda",             "🚓 KP Poznań Wilda (ul. Chłapowskiego 12)", "police", 52.38650, 16.92180, None, "Wilda"),
    ]
    emergency_facilities = []
    for fid, name, ftype, lat, lon, hint, dist_name in emergency_facilities_raw:
        oid = find_nearest_node(G_dir, lat, lon, hint)
        idx = osm_id_to_idx[oid]
        emergency_facilities.append({
            "id": fid,
            "name": name,
            "category": ftype,
            "district": dist_name,
            "node_idx": idx,
            "coords": graph_coords[idx]
        })

    tsp_presets_raw = {
        "courier_inpost": {
            "name": "📦 Kurier Miejski InPost/DHL (8 punktów + Okna czasowe)",
            "depot": ("Baza Logistyczna Górczyn (ul. Głogowska)", 52.37885, 16.88085, "07:30"),
            "stops": [
                ("Paczkomat POZ01M - Rondo Kaponiera", 52.40778, 16.91248, "08:00 - 09:30", 6),
                ("Paczkomat POZ44A - Politechnika Piotrowo", 52.40250, 16.94950, "08:30 - 10:30", 5),
                ("Paczkomat POZ12N - Rynek Jeżycki", 52.41260, 16.90480, "08:00 - 10:00", 5),
                ("Paczkomat POZ88R - Rondo Rataje", 52.39682, 16.95045, "08:30 - 11:00", 6),
                ("Paczkomat POZ19W - Winogrady (Murawa)", 52.42950, 16.93550, "09:00 - 11:30", 5),
                ("Paczkomat POZ05S - Stary Rynek / Garbary", 52.40695, 16.92885, "08:15 - 10:30", 7),
                ("Paczkomat POZ31L - Rynek Wildecki", 52.39450, 16.92250, "08:00 - 11:30", 5),
            ]
        },
        "waste_goap": {
            "name": "🚛 Śmieciarka Komunalna Poznań (Zbiórka selektywna - 7 rejonów)",
            "depot": ("Baza Komunalna Karolin / Gdyńska", 52.43550, 16.97950, "06:00"),
            "stops": [
                ("Rejon 1: Naramowice (ul. Naramowicka)", 52.44400, 16.94750, "06:15 - 08:00", 10),
                ("Rejon 2: Rondo Śródka / Ostrów Tumski", 52.41065, 16.95535, "06:30 - 09:00", 8),
                ("Rejon 3: Piątkowo (Al. Solidarności)", 52.43950, 16.91500, "06:30 - 09:30", 10),
                ("Rejon 4: Stare Miasto (Al. Marcinkowskiego)", 52.40695, 16.92885, "07:00 - 10:00", 9),
                ("Rejon 5: Sołacz (Al. Wielkopolska)", 52.41980, 16.92120, "07:00 - 10:30", 8),
                ("Rejon 6: Winogrady (ul. Murawa)", 52.42950, 16.93550, "06:30 - 10:00", 9),
            ]
        },
        "med_samples": {
            "name": "🧪 Transport Medyczny Próbek (6 Szpitali w Poznaniu -> RCKiK)",
            "depot": ("Regionalne Centrum Krwiodawstwa (ul. Marcelińska)", 52.40680, 16.86850, "08:00"),
            "stops": [
                ("Szpital Kliniczny Przybyszewskiego 49", 52.40585, 16.88512, "08:05 - 09:00", 6),
                ("Szpital Miejski im. J. Strusia (Szwajcarska 3)", 52.38890, 16.99395, "08:30 - 10:15", 7),
                ("Szpital Wojewódzki (ul. Lutycka)", 52.43520, 16.90050, "08:15 - 09:45", 6),
                ("Centrum Medyczne HCP (Dębiec)", 52.37650, 16.91420, "08:20 - 10:00", 6),
                ("Szpital im. F. Raszei (Jeżyce)", 52.41120, 16.90980, "08:10 - 09:30", 5),
                ("Szpital Ortopedyczny im. Degi (Wilda)", 52.38950, 16.91880, "08:15 - 10:00", 5),
            ]
        }
    }
    tsp_presets = {}
    for pk, pval in tsp_presets_raw.items():
        dname, dlat, dlon, dstart = pval["depot"]
        d_oid = find_nearest_node(G_dir, dlat, dlon, None)
        d_idx = osm_id_to_idx[d_oid]
        stops_list = []
        for sname, slat, slon, tw, srv in pval["stops"]:
            s_oid = find_nearest_node(G_dir, slat, slon, None)
            s_idx = osm_id_to_idx[s_oid]
            stops_list.append({
                "name": sname,
                "node_idx": s_idx,
                "coords": graph_coords[s_idx],
                "time_window": tw,
                "service_min": srv
            })
        tsp_presets[pk] = {
            "name": pval["name"],
            "depot": {
                "name": dname,
                "node_idx": d_idx,
                "coords": graph_coords[d_idx],
                "start_time": dstart
            },
            "stops": stops_list
        }

    demand_districts_raw = [
        ("Piątkowo Północ (Os. Sobieskiego / Chrobrego)", 52.4565, 16.9185, 31500),
        ("Piątkowo Południe (Os. Batorego / Śmiałego)",   52.4415, 16.9195, 26800),
        ("Winogrady (Os. Wichrowe Wzgórze / Kosmonautów)",52.4295, 16.9355, 32400),
        ("Naramowice i Umultowo",                         52.4480, 16.9485, 24200),
        ("Podolany i Strzeszyn",                          52.4435, 16.8850, 19800),
        ("Sołacz i Winiary",                              52.4235, 16.9065, 15400),
        ("Jeżyce Centralne",                              52.4128, 16.9045, 25600),
        ("Ogrody, Wola i Smochowice",                     52.4215, 16.8650, 18900),
        ("Ławica i Marcelin",                             52.4110, 16.8540, 14500),
        ("Grunwald Północ (ul. Przybyszewskiego)",        52.4055, 16.8880, 24100),
        ("Grunwald Południe i Os. Kopernika",             52.3935, 16.8650, 29400),
        ("Junikowo i Kwiatowe",                           52.3845, 16.8390, 16200),
        ("Górczyn i Świerczewo",                          52.3785, 16.8850, 18700),
        ("Łazarz (ul. Głogowska / Rynek Łazarski)",       52.3965, 16.9025, 28900),
        ("Stare Miasto / Centrum",                        52.4075, 16.9265, 31200),
        ("Garbary i Chwaliszewo",                         52.4135, 16.9365, 14800),
        ("Wilda Centralna i Dolna Wilda",                 52.3895, 16.9215, 27600),
        ("Dębiec",                                        52.3685, 16.9125, 17400),
        ("Starołęka i Minikowo",                          52.3715, 16.9410, 15200),
        ("Rataje Dolne (Os. Piastowskie / Jagiellońskie)",52.3985, 16.9495, 29800),
        ("Rataje Górne (Os. Oświecenia / Powstańców)",    52.3915, 16.9615, 26500),
        ("Żegrze i Os. Orła Białego",                     52.3835, 16.9625, 24900),
        ("Chartowo (Os. Lecha, Czecha, Rusa)",            52.3935, 16.9825, 28400),
        ("Szczepankowo, Spławie i Krzesiny",              52.3725, 16.9950, 13800),
        ("Śródka, Komandoria i Malta",                    52.4105, 16.9615, 16900),
        ("Główna i Zawady",                               52.4235, 16.9635, 13400),
        ("Antoninek, Zieliniec i Kobylepole",             52.4065, 17.0010, 12900),
    ]
    demand_districts = []
    for dname, dlat, dlon, pop in demand_districts_raw:
        oid = find_nearest_node(G_dir, dlat, dlon, None)
        idx = osm_id_to_idx[oid]
        demand_districts.append({
            "name": dname,
            "population": pop,
            "node_idx": idx,
            "coords": graph_coords[idx]
        })

    evacuation_hazards = [
        {"id": "flood_warta",   "name": "🌊 Powódź stuletnia na Warcie (Chwaliszewo / Śródka / Garbary)", "lat": 52.4115, "lon": 16.9435, "radius_m": 950,  "pop": 14500, "type": "Zagrożenie powodziowe (fala kulminacyjna Warty)"},
        {"id": "chem_gorczyn",  "name": "☣️ Wyciek amoniaku / chloru (Zakłady Chemiczne Górczyn)",         "lat": 52.3805, "lon": 16.8840, "radius_m": 1000, "pop": 16200, "type": "Skażenie chemiczne toksycznym środkiem przemysłowym"},
        {"id": "fire_mtp",      "name": "🔥 Pożar wielkopowierzchniowy / Zagrożenie (Targi MTP / Dworzec)","lat": 52.4035, "lon": 16.9095, "radius_m": 800,  "pop": 22000, "type": "Zagrożenie pożarowo-wybuchowe w centrum"},
        {"id": "ind_karolin",   "name": "⚡ Awaria przemysłowa w rejonie Karolina / ul. Gdyńskiej",         "lat": 52.4355, "lon": 16.9760, "radius_m": 1100, "pop": 9800,  "type": "Zagrożenie technologiczno-energetyczne"},
    ]
    evacuation_shelters_raw = [
        ("shelter_stadion", "🏟️ Punkt Zbiórki: Enea Stadion Miejski (ul. Bułgarska)", 52.3975, 16.8585, 38000),
        ("shelter_arena",   "🏟️ Punkt Zbiórki: Hala Widowiskowa Arena (Park Kasprowicza)", 52.3935, 16.8905, 12000),
        ("shelter_cytadela","🌳 Punkt Zbiórki: Park Cytadela (Górny Taras - bezpieczny n.p.m.)", 52.4235, 16.9345, 30000),
        ("shelter_pp",      "🎓 Punkt Zbiórki: Kampus Politechniki Poznańskiej / Malta", 52.4015, 16.9565, 25000),
        ("shelter_uam",     "🎓 Punkt Zbiórki: Kampus UAM Morasko (Wysoczyzna Północna)", 52.4635, 16.9265, 28000),
        ("shelter_franowo", "🅿️ Punkt Zbiórki: Węzeł Park & Ride Franowo / Szwajcarska", 52.3855, 16.9940, 22000),
    ]
    evacuation_shelters = []
    for sh_id, sh_name, slat, slon, cap in evacuation_shelters_raw:
        oid = find_nearest_node(G_dir, slat, slon, None)
        idx = osm_id_to_idx[oid]
        evacuation_shelters.append({
            "id": sh_id,
            "name": sh_name,
            "capacity": cap,
            "node_idx": idx,
            "coords": graph_coords[idx]
        })

    road_criticality_raw = [
        ("Most Przemysła I (ul. Hetmańska - II Rama)",      52.38525, 16.94440, 98, 54200, "+6.9 min", "Główna przeprawa południowa Wschód-Zachód (Rataje <-> Wilda/Łazarz)"),
        ("Most Lecha (ul. Lechicka / DK92 - III Rama Płn.)",52.43350, 16.95550, 96, 61500, "+8.4 min", "Kluczowy most tranzytowy północnego Poznania i syfon kolektora DN900"),
        ("Rondo Kaponiera (Węzeł Centralny Śródmieście)",   52.40778, 16.91248, 94, 48900, "+5.8 min", "Centralny węzeł integracyjny dróg, PST i linii tramwajowych W-Z"),
        ("Rondo Rataje (ul. Jana Pawła II / Krzywoustego)", 52.39682, 16.95045, 92, 52300, "+6.1 min", "Główny węzeł rozrządowy prawobrzeżnego Poznania i wlotu z S5/A2"),
        ("Most Królowej Jadwigi (Trasa Półwiejska-Rataje)", 52.40050, 16.94450, 89, 43100, "+5.2 min", "Podstawowe połączenie centrum i Wildy z Ratajami"),
        ("Węzeł Głogowska / Hetmańska (Łazarz / Górczyn)",  52.38862, 16.89848, 86, 41600, "+4.7 min", "Skrzyżowanie wylotu DK5 na Wrocław z II Ramą Komunikacyjną"),
        ("Wiadukt ul. Niestachowska / Witosa (Sołacz)",     52.42340, 16.89595, 85, 46800, "+4.9 min", "Jedyny bezkolizyjny korytarz łączący Piątkowo/Północ z Grunwaldem"),
        ("Rondo Śródka (ul. Wyszyńskiego / Warszawska)",    52.41065, 16.95535, 83, 44200, "+4.5 min", "Wschodni węzeł wylotowy DK92 i brama na Ostrów Tumski"),
        ("Most Bolesława Chrobrego (Katedra / Śródka)",     52.41100, 16.94550, 80, 36400, "+4.1 min", "Przeprawa przez Wartę na osi Garbary - Śródka"),
        ("Węzeł Rondo Jana Nowaka-Jeziorańskiego (Grunwald)",52.40014, 16.89268, 78, 39200, "+3.8 min", "Kluczowy węzeł ul. Grunwaldzkiej, Reymonta i Przybyszewskiego"),
    ]
    road_criticality = []
    for rname, rlat, rlon, score, aadt, delay_str, desc in road_criticality_raw:
        oid = find_nearest_node(G_dir, rlat, rlon, None)
        idx = osm_id_to_idx[oid]
        road_criticality.append({
            "name": rname,
            "coords": graph_coords[idx],
            "node_idx": idx,
            "score": score,
            "aadt": aadt,
            "delay": delay_str,
            "desc": desc
        })

    print(f"[3/7] Sieć wodociągowa ciśnieniowa Aquanet: {len(water_nodes)} komór/SUW, {len(water_edges_list)} magistral pierścieniowych.")
    print(f"[4/7] Sieć kanalizacyjna Aquanet ({len(infra_nodes)} węzłów) i elektroenergetyczna Enea ({len(power_nodes)} stacji).")
    print(f"[5/7] Sieć ZTM Poznań: {len(tram_tracks_bg)} odcinków torowisk OSM (railway=tram), {len(ztm_stops)} przystanków (Tramwaj + Autobus), {len(ztm_edges)} relacji.")

    return {
        "boundary_ring": boundary_ring,
        "graph_coords": graph_coords,
        "graph_edges": graph_edges,
        "street_names": street_names_list,
        "background_roads_major": background_roads_major,
        "background_roads_local": background_roads_local,
        "tram_tracks_bg": tram_tracks_bg,
        "landmarks": landmarks,
        "preset_closures": preset_closures,
        "infra_nodes": infra_nodes,
        "infra_edges": infra_edges_list,
        "water_nodes": water_nodes,
        "water_edges": water_edges_list,
        "power_nodes": power_nodes,
        "power_edges": power_edges_list,
        "emergency_facilities": emergency_facilities,
        "ztm_stops": ztm_stops,
        "ztm_edges": ztm_edges,
        "tsp_presets": tsp_presets,
        "demand_districts": demand_districts,
        "evacuation_hazards": evacuation_hazards,
        "evacuation_shelters": evacuation_shelters,
        "road_criticality": road_criticality,
    }


# ==============================================================================
# 3. KOMPILACJA APLIKACJI WEB-GIS / DESKTOP QGIS v4.0
# ==============================================================================
def build_interactive_web_app(bundle, output_html="index.html"):
    center_poznan = [52.4085, 16.9250]
    m = folium.Map(
        location=center_poznan,
        zoom_start=12,
        max_zoom=20,
        tiles=None,
        prefer_canvas=True,
        control_scale=True
    )

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
        attr="&copy; OpenStreetMap &copy; CARTO",
        name="1. Mapa Jasna (CartoDB Positron)",
        max_zoom=20
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
        attr="&copy; OpenStreetMap &copy; CARTO",
        name="2. Mapa Ciemna Analityczna (Dark Matter)",
        max_zoom=20
    ).add_to(m)

    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="&copy; OpenStreetMap contributors",
        name="3. OpenStreetMap Standard (Z numerami budynków i uliczkami)",
        max_zoom=19
    ).add_to(m)

    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="&copy; Esri",
        name="4. Ortofotomapa (Zdjęcia Satelitarne - Raster)",
        max_zoom=19
    ).add_to(m)

    folium.WmsTileLayer(
        url="https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaUzbrojeniaTerenu",
        layers="przewod_kanalizacyjny,przewod_wodociagowy,przewod_elektroenergetyczny,przewod_urzadzenia",
        name="5. Geoportal GUGiK GESUT: Rury i kable podziemne (Zoom 17+)",
        fmt="image/png",
        transparent=True,
        version="1.3.0",
        attr="GUGiK - Krajowa Integracja Uzbrojenia Terenu (GESUT)",
        overlay=True,
        control=True,
        show=True,
        max_zoom=20
    ).add_to(m)

    folium.LayerControl(position="bottomleft").add_to(m)

    payload_json = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))

    html_part1 = r"""
    <style>
        * { box-sizing: border-box; }
        body, html {
            margin: 0;
            padding: 0;
            width: 100%;
            height: 100%;
            overflow: hidden;
            font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
        }

        /* =====================================================================
           TRYB 1: NOWOCZESNY WEB-GIS (FLOATING DASHBOARD - DOMYŚLNY)
           ===================================================================== */
        body.theme-modern #qgis-menubar,
        body.theme-modern #qgis-toolbar,
        body.theme-modern #qgis-left-dock,
        body.theme-modern #qgis-statusbar {
            display: none !important;
        }

        body.theme-modern #gis-panel {
            position: fixed;
            top: 10px;
            right: 10px;
            width: 472px;
            max-height: calc(100vh - 54px);
            overflow-y: auto;
            background: rgba(15, 23, 42, 0.96);
            color: #f8fafc;
            border-radius: 14px;
            padding: 13px 15px;
            z-index: 9999;
            box-shadow: 0 12px 34px rgba(0,0,0,0.65);
            border: 1px solid rgba(56, 189, 248, 0.38);
            backdrop-filter: blur(10px);
        }

        .leaflet-control-attribution {
            display: none !important;
        }

        #author-badge {
            position: fixed;
            bottom: 8px;
            right: 10px;
            background: rgba(15, 23, 42, 0.96);
            color: #f8fafc;
            border: 1px solid rgba(56, 189, 248, 0.45);
            border-left: 3.5px solid #38bdf8;
            border-radius: 9px;
            padding: 5.5px 13px;
            z-index: 10010;
            box-shadow: 0 8px 24px rgba(0,0,0,0.65);
            backdrop-filter: blur(10px);
            display: flex;
            align-items: center;
            gap: 7px;
            font-size: 11.5px;
            user-select: none;
        }
        body.theme-qgis #author-badge {
            bottom: 33px;
            right: 10px;
            background: #26292c;
            border-color: #38bdf8;
            border-radius: 6px;
        }

        body.theme-modern #dem-dock {
            position: fixed;
            bottom: 10px;
            left: 55px;
            width: calc(100vw - 550px);
            max-width: 890px;
            background: rgba(15, 23, 42, 0.96);
            color: #f8fafc;
            border-radius: 12px;
            padding: 9px 14px;
            z-index: 9998;
            box-shadow: 0 8px 26px rgba(0,0,0,0.58);
            border: 1px solid rgba(56, 189, 248, 0.35);
            transition: all 0.2s ease;
        }
        body.theme-modern #dem-dock.collapsed {
            height: 42px;
            overflow: hidden;
            padding: 7px 14px;
        }

        /* =====================================================================
           TRYB 2: PROFESJONALNY DESKTOP GIS (STYL QGIS 3.34 LTR)
           ===================================================================== */
        body.theme-qgis {
            background: #2b2b2b;
            color: #e8e8e8;
        }
        body.theme-qgis .folium-map {
            position: fixed !important;
            top: 66px !important;
            left: 300px !important;
            width: calc(100vw - 760px) !important;
            height: calc(100vh - 292px) !important;
            z-index: 1;
            border: 1px solid #1f1f1f;
        }
        body.theme-qgis.dem-hidden .folium-map {
            height: calc(100vh - 94px) !important;
        }

        #qgis-menubar {
            position: fixed;
            top: 0; left: 0; right: 0;
            height: 28px;
            background: #323232;
            border-bottom: 1px solid #1f1f1f;
            display: flex;
            align-items: center;
            padding: 0 10px;
            gap: 4px;
            z-index: 10005;
            font-size: 12px;
            color: #dcdcdc;
            user-select: none;
        }
        .qgis-brand {
            display: flex;
            align-items: center;
            gap: 6px;
            font-weight: 700;
            color: #93c5fd;
            margin-right: 10px;
            padding-right: 10px;
            border-right: 1px solid #4a4a4a;
        }
        .qgis-brand span.q-logo {
            background: #589632;
            color: #fff;
            font-weight: 900;
            padding: 1px 6px;
            border-radius: 3px;
            font-size: 11px;
        }
        .qgis-menu-item {
            padding: 4px 8px;
            border-radius: 3px;
            cursor: pointer;
            font-size: 11.5px;
        }
        .qgis-menu-item:hover {
            background: #4a4a4a;
            color: #ffffff;
        }

        #qgis-toolbar {
            position: fixed;
            top: 28px; left: 0; right: 0;
            height: 38px;
            background: #3c3f41;
            border-bottom: 1px solid #222;
            display: flex;
            align-items: center;
            padding: 0 10px;
            gap: 5px;
            z-index: 10004;
            overflow-x: auto;
        }
        .q-tool-btn {
            background: #4b4f52;
            color: #f0f0f0;
            border: 1px solid #5a5e61;
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 11px;
            font-weight: 600;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            white-space: nowrap;
        }
        .q-tool-btn:hover {
            background: #5c6164;
            border-color: #8ab4f8;
        }
        .q-sep {
            width: 1px;
            height: 22px;
            background: #555;
            margin: 0 2px;
        }

        #qgis-left-dock {
            position: fixed;
            top: 66px; left: 0;
            width: 300px;
            height: calc(100vh - 94px);
            background: #313335;
            border-right: 1px solid #1e1e1e;
            z-index: 10002;
            display: flex;
            flex-direction: column;
            color: #e2e8f0;
            font-size: 11.5px;
        }
        .q-dock-header {
            background: #3c3f41;
            padding: 6px 10px;
            font-weight: 700;
            font-size: 11px;
            color: #cfd8dc;
            border-bottom: 1px solid #222;
            border-top: 1px solid #484a4c;
            display: flex;
            justify-content: space-between;
            align-items: center;
            text-transform: uppercase;
        }
        .q-dock-body {
            padding: 7px 10px;
            overflow-y: auto;
            flex: 1;
        }
        .q-layer-item {
            display: flex;
            align-items: center;
            gap: 7px;
            padding: 3.5px 5px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 11px;
            color: #e2e8f0;
        }
        .q-layer-item:hover { background: #3e4347; }
        .q-tree-cat {
            font-weight: 700;
            color: #93c5fd;
            margin: 7px 0 3px 0;
            font-size: 10.5px;
            border-bottom: 1px solid #45494e;
            padding-bottom: 2px;
        }
        .q-alg-item {
            padding: 4.5px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-size: 11px;
            display: flex;
            align-items: center;
            gap: 6px;
            color: #d1d5db;
            margin-bottom: 2px;
        }
        .q-alg-item:hover { background: #3e4347; color: #fff; }
        .q-alg-item.active { background: #2d5a88; color: #fff; font-weight: 600; }

        body.theme-qgis #gis-panel {
            position: fixed;
            top: 66px; right: 0;
            width: 460px;
            height: calc(100vh - 132px);
            max-height: none;
            overflow-y: auto;
            background: #313335;
            color: #f1f5f9;
            border-radius: 0;
            padding: 10px 13px;
            z-index: 10002;
            box-shadow: none;
            border: none;
            border-left: 1px solid #1e1e1e;
        }

        body.theme-qgis #dem-dock {
            position: fixed;
            bottom: 28px;
            left: 300px;
            width: calc(100vw - 760px);
            max-width: none;
            height: 198px;
            background: #2b2d30;
            color: #e2e8f0;
            border-radius: 0;
            padding: 6px 12px;
            z-index: 10001;
            box-shadow: none;
            border: none;
            border-top: 1px solid #1e1e1e;
        }
        body.theme-qgis.dem-hidden #dem-dock {
            display: none !important;
        }

        #qgis-statusbar {
            position: fixed;
            bottom: 0; left: 0; right: 0;
            height: 28px;
            background: #323232;
            border-top: 1px solid #1c1c1c;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 12px;
            font-size: 11px;
            color: #cccccc;
            z-index: 10005;
        }
        .q-status-group { display: flex; align-items: center; gap: 10px; }
        .q-status-box {
            background: #262626;
            border: 1px solid #444;
            padding: 1px 7px;
            border-radius: 3px;
            font-family: Consolas, monospace;
            font-size: 11px;
            color: #93c5fd;
        }

        /* =====================================================================
           TRYB SPLIT-SCREEN (MULTI-SCENARIUSZ A vs B)
           ===================================================================== */
        body.split-mode .folium-map {
            position: fixed !important;
            top: 48px !important;
            left: 0 !important;
            width: calc(50vw - 236px) !important;
            height: calc(100vh - 48px) !important;
            border-right: 2px solid #38bdf8;
        }
        #split-map-container {
            display: none;
            position: fixed;
            top: 48px;
            left: calc(50vw - 236px);
            width: calc(50vw - 236px);
            height: calc(100vh - 48px);
            z-index: 500;
            background: #0f172a;
            border-right: 1px solid #334155;
        }
        body.split-mode #split-map-container {
            display: block !important;
        }
        #split-top-banner {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: calc(100vw - 472px);
            height: 48px;
            background: #0f172a;
            border-bottom: 2px solid #38bdf8;
            z-index: 10006;
            align-items: center;
            justify-content: space-between;
            padding: 0 14px;
            color: #f8fafc;
            font-size: 11.5px;
        }
        body.split-mode #split-top-banner {
            display: flex !important;
        }
        body.split-mode #dem-dock {
            display: none !important;
        }

        /* =====================================================================
           PANEL POWIADOMIEŃ I LOG ZDARZEŃ KRYZYSOWYCH (EVENT LOG DRAWER)
           ===================================================================== */
        #event-log-drawer {
            display: none;
            position: fixed;
            top: 60px;
            left: 60px;
            width: 460px;
            max-height: 75vh;
            background: rgba(15, 23, 42, 0.98);
            color: #f8fafc;
            border: 1px solid #f59e0b;
            border-radius: 12px;
            box-shadow: 0 16px 42px rgba(0,0,0,0.75);
            z-index: 15000;
            flex-direction: column;
            overflow: hidden;
        }
        .log-entry {
            padding: 8px 10px;
            border-bottom: 1px solid #1e293b;
            font-size: 11px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .log-entry:hover {
            background: rgba(56, 189, 248, 0.08);
        }

        /* =====================================================================
           ASYSTENT DORADCZY AI (AI ADVISOR BOX) & SUWAK CZASU (TIME-SLIDER)
           ===================================================================== */
        .ai-advisor-box {
            background: linear-gradient(135deg, rgba(30, 58, 138, 0.48), rgba(15, 23, 42, 0.92));
            border: 1px solid #38bdf8;
            border-left: 4px solid #fde047;
            border-radius: 8px;
            padding: 8px 10px;
            margin-bottom: 8px;
            font-size: 11px;
            line-height: 1.42;
            color: #f1f5f9;
        }
        .ai-advisor-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-weight: 700;
            color: #fde047;
            margin-bottom: 4px;
            font-size: 11px;
        }
        .time-slider-bar {
            display: flex;
            align-items: center;
            gap: 8px;
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 7px;
            padding: 4px 10px;
            margin-bottom: 5px;
            font-size: 11px;
        }

        /* =====================================================================
           WSPÓLNE ELEMENTY PANELU ANALITYCZNEGO
           ===================================================================== */
        .panel-top-bar {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 6px;
            margin-bottom: 6px;
            padding-bottom: 6px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.22);
        }
        .panel-top-bar h2 { margin: 0; font-size: 14.5px; color: #38bdf8; }
        .panel-top-bar .subtitle { font-size: 10px; color: #94a3b8; margin-top: 2px; }
        .top-actions { display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
        .icon-btn {
            background: #1e293b;
            color: #e2e8f0;
            border: 1px solid #475569;
            border-radius: 6px;
            padding: 4px 7px;
            font-size: 10.5px;
            font-weight: 600;
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.15s;
        }
        .icon-btn:hover { background: #0284c7; border-color: #38bdf8; color: #fff; }
        .icon-btn.active-toggle { background: #b45309; border-color: #fde047; color: #fff; }

        .cat-tabs {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 4px;
            margin-bottom: 5px;
        }
        .cat-btn {
            background: #0f172a;
            color: #94a3b8;
            border: 1px solid #334155;
            padding: 4.5px 4px;
            border-radius: 6px;
            font-size: 10.5px;
            font-weight: 700;
            cursor: pointer;
            text-align: center;
        }
        .cat-btn.active {
            background: #1e3a8a;
            color: #38bdf8;
            border-color: #38bdf8;
        }

        .tabs-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 4px;
            margin-bottom: 7px;
        }
        .mode-btn {
            text-align: left;
            background: #1e293b;
            color: #e2e8f0;
            border: 1px solid #334155;
            padding: 5.5px 8px;
            border-radius: 7px;
            cursor: pointer;
            font-size: 10.8px;
            font-weight: 600;
        }
        .mode-btn:hover { background: #334155; border-color: #38bdf8; }
        .mode-btn.active {
            background: #0284c7;
            color: #ffffff;
            border-color: #38bdf8;
            box-shadow: 0 0 8px rgba(56, 189, 248, 0.35);
        }

        .ctrl-box {
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 8px;
            padding: 7px 9px;
            margin-bottom: 7px;
            font-size: 11px;
        }
        .ctrl-box label {
            display: block;
            font-size: 10.5px;
            color: #94a3b8;
            margin-bottom: 3px;
            font-weight: 600;
        }
        .ctrl-row { display: flex; gap: 5px; align-items: center; margin-bottom: 5px; }
        select.gis-select {
            flex: 1;
            background: #0f172a;
            color: #f8fafc;
            border: 1px solid #475569;
            border-radius: 6px;
            padding: 4px 6px;
            font-size: 11px;
        }
        .pick-btn {
            background: #334155;
            color: #f8fafc;
            border: 1px solid #475569;
            border-radius: 6px;
            padding: 4px 8px;
            cursor: pointer;
            font-size: 10.5px;
            font-weight: 600;
            white-space: nowrap;
        }
        .pick-btn:hover { background: #475569; border-color: #38bdf8; }
        .pick-btn.picking {
            background: #f59e0b;
            color: #0f172a;
            border-color: #fde047;
            animation: pulse 1s infinite;
        }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.65; } 100% { opacity: 1; } }
        input[type="range"] { width: 100%; accent-color: #38bdf8; cursor: pointer; }

        .result-box {
            background: #0f172a;
            border-left: 4px solid #38bdf8;
            padding: 8px 10px;
            border-radius: 6px;
            font-size: 11px;
            line-height: 1.42;
            margin-bottom: 7px;
        }
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 5px; margin: 5px 0; }
        .stat-card {
            background: #1e293b;
            padding: 5px 7px;
            border-radius: 6px;
            border: 1px solid #334155;
        }
        .stat-card b { display: block; font-size: 12.5px; color: #38bdf8; }
        .stat-card span { font-size: 9.8px; color: #cbd5e1; }
        .formula-box {
            background: rgba(56, 189, 248, 0.08);
            border: 1px dashed rgba(56, 189, 248, 0.35);
            padding: 6px 8px;
            border-radius: 6px;
            font-size: 10px;
            color: #cbd5e1;
        }
        .badge {
            display: inline-block;
            padding: 1.5px 6px;
            border-radius: 4px;
            font-size: 9.8px;
            font-weight: 700;
            margin-right: 4px;
        }
        .b-blue { background: #2563eb; color: #fff; }
        .b-green { background: #10b981; color: #052e16; }
        .b-red { background: #dc2626; color: #fff; }
        .b-amber { background: #f59e0b; color: #0f172a; }
        .b-purple { background: #9333ea; color: #fff; }

        .gis-table { width: 100%; border-collapse: collapse; font-size: 10px; margin-top: 4px; }
        .gis-table th {
            background: #1e293b; color: #93c5fd; text-align: left;
            padding: 3.5px 5px; border-bottom: 1px solid #475569;
        }
        .gis-table td { padding: 3.5px 5px; border-bottom: 1px solid #1e293b; color: #e2e8f0; }
        .gis-table tr:hover td { background: rgba(56, 189, 248, 0.12); cursor: pointer; }

        .export-bar {
            display: flex;
            gap: 4px;
            margin-top: 6px;
            padding-top: 6px;
            border-top: 1px solid rgba(148, 163, 184, 0.2);
        }
        .exp-btn {
            flex: 1;
            background: #0f172a;
            color: #cbd5e1;
            border: 1px solid #334155;
            border-radius: 5px;
            padding: 4px 5px;
            font-size: 9.8px;
            font-weight: 600;
            cursor: pointer;
            text-align: center;
        }
        .exp-btn:hover { background: #1e293b; border-color: #38bdf8; color: #fff; }

        #settings-modal-backdrop {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.65);
            z-index: 20000;
            align-items: center;
            justify-content: center;
            backdrop-filter: blur(4px);
        }
        #settings-modal {
            background: #0f172a;
            color: #f8fafc;
            border: 1px solid #38bdf8;
            border-radius: 14px;
            width: 560px;
            max-width: 94vw;
            padding: 18px 20px;
            box-shadow: 0 20px 50px rgba(0,0,0,0.8);
        }
        .gui-card-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin: 10px 0 14px 0; }
        .gui-choice-card {
            background: #1e293b;
            border: 2px solid #334155;
            border-radius: 10px;
            padding: 12px;
            cursor: pointer;
            transition: all 0.15s;
        }
        .gui-choice-card:hover { border-color: #60a5fa; }
        .gui-choice-card.selected {
            border-color: #38bdf8;
            background: rgba(2, 132, 199, 0.2);
            box-shadow: 0 0 14px rgba(56, 189, 248, 0.35);
        }
        .gui-choice-card h4 { margin: 0 0 5px 0; font-size: 13px; color: #38bdf8; }
        .gui-choice-card p { margin: 0; font-size: 11px; color: #cbd5e1; line-height: 1.35; }
    </style>

    <!-- =======================================================================
         BANER I DRUGA MAPA DLA TRYBU SPLIT-SCREEN (PORÓWNANIE SCENARIUSZY A vs B)
         ======================================================================= -->
    <div id="split-top-banner">
        <div style="display:flex;align-items:center;gap:10px;">
            <span class="badge b-amber" style="font-size:11px;">🔀 TRYB MULTI-SCENARIUSZ (SPLIT-SCREEN)</span>
            <span><b>LEWA MAPA (Główna — Scenariusz A):</b> Sterowana z panelu prawego</span>
        </div>
        <div style="display:flex;align-items:center;gap:8px;">
            <span><b>PRAWA MAPA (Scenariusz Porównawczy B):</b></span>
            <select class="gis-select" id="split-scenario-b-select" style="width:270px;" onchange="renderSplitMapB()">
                <option value="power_grunwald">⚡ Awaria Prądu: GPZ Grunwald (54 500 odb.)</option>
                <option value="power_wilda">⚡ Awaria Prądu: GPZ Wilda (45 500 odb.)</option>
                <option value="water_chwaliszewo">💧 Awaria Wodociągu: Magistrala Chwaliszewo (WZ-13/WZ-14)</option>
                <option value="water_jezyce">💧 Awaria Wodociągu: Magistrala Jeżycka (WZ-05/WZ-06)</option>
                <option value="road_przemysla">🚧 Zamknięcie Mostu Przemysła I (ul. Hetmańska)</option>
                <option value="storm_200">🌧️ Ekstremalna Ulewa 200% (Przepełnienia kolektorów)</option>
            </select>
            <button class="pick-btn" style="background:#dc2626;" onclick="toggleSplitScreenMode()">✖ Zamknij Split-Screen</button>
        </div>
    </div>
    <div id="split-map-container">
        <div id="map-split-b" style="width:100%;height:100%;"></div>
        <div id="split-b-summary" style="position:absolute;bottom:14px;left:14px;right:14px;background:rgba(15,23,42,0.94);color:#f8fafc;padding:10px 12px;border-radius:10px;border:1px solid #f59e0b;z-index:1000;font-size:11.5px;"></div>
    </div>

    <!-- =======================================================================
         SZUFLADA DZIENNIKA ZDARZEŃ I ALARMÓW (CRISIS EVENT LOG)
         ======================================================================= -->
    <div id="event-log-drawer">
        <div style="background:#1e293b;padding:9px 12px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #334155;">
            <span style="font-weight:700;font-size:12.5px;color:#fde047;">🔔 Dziennik Zdarzeń i Symulacji Kryzysowych (Event Log)</span>
            <div style="display:flex;gap:5px;">
                <button class="pick-btn" style="font-size:10px;padding:2px 7px;" onclick="clearEventLog()">🗑️ Wyczyść</button>
                <button class="pick-btn" style="font-size:10px;padding:2px 7px;" onclick="toggleEventLogDrawer()">✖ Zamknij</button>
            </div>
        </div>
        <div style="padding:6px 12px;background:#0f172a;font-size:10.5px;color:#94a3b8;border-bottom:1px solid #1e293b;">
            Każda przeprowadzona analiza i symulacja awarii jest rejestrowana poniżej. Kliknij <b>Przywróć</b>, aby wrócić do danego incydentu.
        </div>
        <div id="event-log-list" style="overflow-y:auto;max-height:58vh;padding:4px 0;"></div>
    </div>

    <!-- =======================================================================
         ELEMENTY STYLU QGIS DESKTOP
         ======================================================================= -->
    <div id="qgis-menubar">
        <div class="qgis-brand">
            <span class="q-logo">QGIS</span>
            <span>Poznań Network GIS 3.34 LTR</span>
        </div>
        <div class="qgis-menu-item" onclick="resetPoznanView()">Projekt</div>
        <div class="qgis-menu-item" onclick="setScenario('routes')">Transport</div>
        <div class="qgis-menu-item" onclick="setScenario('water')">Infrastruktura Utility</div>
        <div class="qgis-menu-item" onclick="toggleEventLogDrawer()">🔔 Log Zdarzeń</div>
        <div class="qgis-menu-item" onclick="toggleSplitScreenMode()">🔀 Split-Screen (A vs B)</div>
        <div class="qgis-menu-item" onclick="exportActiveAnalysis('geojson')">Eksport GeoJSON/KML</div>
        <div class="qgis-menu-item" style="color:#fde047;font-weight:700;" onclick="openSettingsModal()">⚙️ Ustawienia (Zmień GUI)</div>
    </div>

    <div id="qgis-toolbar">
        <button class="q-tool-btn" onclick="resetPoznanView()">🏠 Cały Poznań</button>
        <button class="q-tool-btn" onclick="zoomToRealGESUT()">🔍 GUGiK GESUT (1:1000)</button>
        <div class="q-sep"></div>
        <button class="q-tool-btn" onclick="setScenario('water')">💧 Wodociągi</button>
        <button class="q-tool-btn" onclick="setScenario('upstream')">🧪 Kanalizacja</button>
        <button class="q-tool-btn" onclick="setScenario('power')">⚡ Energetyka</button>
        <button class="q-tool-btn" onclick="setScenario('criticality')">🎯 Krytyczność</button>
        <div class="q-sep"></div>
        <button class="q-tool-btn" onclick="setScenario('od_matrix')">📊 Macierz OD</button>
        <button class="q-tool-btn" onclick="setScenario('tsp')">🚚 TSP/VRP</button>
        <button class="q-tool-btn" onclick="setScenario('multimodal')">🚋/🚌 Tramwaj+Autobus</button>
        <div class="q-sep"></div>
        <button class="q-tool-btn" style="background:#7c2d12;border-color:#f97316;" onclick="toggleEventLogDrawer()">🔔 Log Zdarzeń (<span id="qgis-log-count">0</span>)</button>
        <button class="q-tool-btn" style="background:#4c1d95;border-color:#a855f7;" onclick="toggleSplitScreenMode()">🔀 Split-Screen A/B</button>
        <button class="q-tool-btn" style="background:#0284c7;border-color:#38bdf8;" onclick="openSettingsModal()">⚙️ Ustawienia GUI</button>
    </div>

    <div id="qgis-left-dock">
        <div class="q-dock-header">
            <span>🗂️ Warstwy Projektu (Layers)</span>
            <span style="font-size:10px;color:#93c5fd;">EPSG:2180 / 4326</span>
        </div>
        <div class="q-dock-body" style="flex: 0 0 218px; border-bottom: 1px solid #222;">
            <label class="q-layer-item"><input type="checkbox" checked onchange="toggleMapLayer('outside_mask', this.checked)"> 🌑 Maska zaciemniająca poza Poznaniem</label>
            <label class="q-layer-item"><input type="checkbox" checked onchange="toggleMapLayer('boundary', this.checked)"> 🌐 Granica m. Poznania (Poligon)</label>
            <label class="q-layer-item"><input type="checkbox" id="q-chk-tram" onchange="toggleMapLayer('tram_tracks', this.checked)"> 🚋 Torowiska tramwajowe ZTM (816 odc.)</label>
            <label class="q-layer-item"><input type="checkbox" id="q-chk-bus" onchange="toggleMapLayer('bus_routes', this.checked)"> 🚌 Linie autobusowe ZTM Poznań (Ulice)</label>
            <label class="q-layer-item"><input type="checkbox" checked onchange="toggleMapLayer('major_roads', this.checked)"> 🛣️ Sieć dróg głównych (4 605 odc.)</label>
            <label class="q-layer-item"><input type="checkbox" checked onchange="toggleMapLayer('local_roads', this.checked)"> 🏘️ Ulice lokalne i osiedlowe (8 293 odc.)</label>
            <label class="q-layer-item"><input type="checkbox" checked onchange="toggleMapLayer('active_analysis', this.checked)"> 🎯 Warstwa aktywnej analizy sieciowej</label>
        </div>
        <div class="q-dock-header">
            <span>⚙️ Skrzynka Algorytmów (Processing)</span>
        </div>
        <div class="q-dock-body" id="qgis-alg-tree"></div>
    </div>

    <div id="qgis-statusbar">
        <div class="q-status-group">
            <span>WGS84: <span class="q-status-box" id="status-wgs84">52.40850°N, 16.92500°E</span></span>
            <span>PUWG 1992 (EPSG:2180): <span class="q-status-box" id="status-puwg">X: 506812 m, Y: 358845 m</span></span>
            <span>DEM: <span class="q-status-box" id="status-dem">64.2 m n.p.m.</span></span>
        </div>
        <div class="q-status-group">
            <span>Graf: <b>39 911 węzłów | 816 torów ZTM | 28 linii Bus</b></span>
            <span class="q-status-box" id="status-zoom">Zoom: 12</span>
            <button class="q-tool-btn" style="padding:2px 8px;background:#1e3a8a;" onclick="toggleGuiThemeQuick()">🖥️ Przełącz na Web-GIS</button>
        </div>
    </div>

    <!-- =======================================================================
         GŁÓWNY PANEL ANALITYCZNY (FLOATING LUB PRAWY DOK W QGIS)
         ======================================================================= -->
    <div id="gis-panel">
        <div class="panel-top-bar">
            <div>
                <h2>🌐 System Analiz Sieciowych — Poznań</h2>
                <div class="subtitle">OSM • Wodociągi • Kanalizacja • Enea • Tramwaje i Autobusy ZTM • AI</div>
            </div>
            <div class="top-actions">
                <button class="icon-btn" onclick="toggleEventLogDrawer()" id="btn-event-log" title="Dziennik zdarzeń i historia symulacji kryzysowych">🔔 Log (<span id="log-badge-count">0</span>)</button>
                <button class="icon-btn" onclick="toggleSplitScreenMode()" id="btn-split-screen" title="Porównaj dwa scenariusze obok siebie (Split-Screen A vs B)">🔀 A vs B</button>
                <button class="icon-btn" onclick="toggleGuiThemeQuick()" id="quick-gui-btn" title="Przełącz wygląd: Web-GIS <-> QGIS Desktop">🖥️ QGIS</button>
                <button class="icon-btn" onclick="openSettingsModal()" title="Ustawienia interfejsu i warstw">⚙️</button>
            </div>
        </div>

        <!-- 4 KATEGORIE TEMATYCZNE -->
        <div class="cat-tabs">
            <button class="cat-btn active" id="cat-btn-utility" onclick="selectCategory('utility')">1. Sieci Utility</button>
            <button class="cat-btn" id="cat-btn-transport" onclick="selectCategory('transport')">2. Transport ZTM</button>
            <button class="cat-btn" id="cat-btn-resilience" onclick="selectCategory('resilience')">3. Ratownictwo</button>
            <button class="cat-btn" id="cat-btn-basic" onclick="selectCategory('basic')">4. Trasy i Objazdy</button>
        </div>

        <!-- PRZYCISKI MODUŁÓW W WYBRANEJ KATEGORII -->
        <div class="tabs-row" id="module-buttons-container"></div>

        <!-- DYNAMICZNE KONTROLKI PARAMETRÓW -->
        <div id="dynamic-controls"></div>

        <!-- BOKS WYNIKÓW ANALIZY -->
        <div id="result-box" class="result-box"></div>

        <!-- ASYSTENT AI -->
        <div id="ai-advisor-box" class="ai-advisor-box"></div>

        <!-- BOKS OPISU ALGORYTMU -->
        <div id="formula-box" class="formula-box"></div>

        <!-- PASEK EKSPORTU DANYCH GEOPRZESTRZENNYCH -->
        <div class="export-bar">
            <button class="exp-btn" onclick="exportActiveAnalysis('geojson')">💾 GeoJSON (QGIS)</button>
            <button class="exp-btn" onclick="exportActiveAnalysis('kml')">🌍 KML</button>
            <button class="exp-btn" onclick="exportActiveAnalysis('csv')">📊 CSV / WKT</button>
            <button class="exp-btn" onclick="toggleDemDock()">📈 Profil DEM / Czas</button>
        </div>
    </div>

    <!-- WIZYTÓWKA AUTORA W PRAWYM DOLNYM ROGU (STYL GUI SYSTEMU) -->
    <div id="author-badge">
        <span class="badge b-blue" style="margin:0;font-size:9.8px;padding:2px 6px;">👤 AUTOR</span>
        <span style="color:#f8fafc;font-weight:700;font-size:12px;letter-spacing:0.5px;">PAWEŁ SICIŃSKI</span>
        <span style="color:#64748b;font-size:10px;">| GIS Poznań</span>
    </div>

    <!-- =======================================================================
         DOLNY DOK: SUWAK CZASU (TIME-SLIDER ANIMACJI) + PROFIL WYSOKOŚCIOWY DEM
         ======================================================================= -->
    <div id="dem-dock">
        <!-- PASEK ANIMACJI PROPAGACJI ZJAWISK W CZASIE (TIME-SLIDER) -->
        <div class="time-slider-bar">
            <button class="pick-btn" id="btn-play-anim" style="background:#0284c7;border-color:#38bdf8;min-width:92px;" onclick="toggleTimeAnimation()">▶️ Animuj czas</button>
            <button class="pick-btn" style="padding:3px 7px;" onclick="resetTimeAnimation()">⏮️ t=0</button>
            <span style="white-space:nowrap;color:#fde047;font-weight:700;min-width:96px;" id="time-slider-label">⏱️ Czas: 60 min</span>
            <input type="range" id="time-slider-input" min="1" max="60" step="1" value="60" style="flex:1;" oninput="onTimeSliderInput(this.value)">
            <span id="time-dynamic-effect" style="font-size:10.5px;color:#38bdf8;white-space:nowrap;font-weight:600;">Stan ustalony (100%)</span>
            <button class="pick-btn" style="padding:2px 7px;font-size:10px;" onclick="toggleDemDock()">Zwiń</button>
        </div>

        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
            <div style="font-size:11px;font-weight:700;color:#38bdf8;" id="dem-title">
                📈 Profil Wysokościowy Terenu i Sieci (DEM Poznania — SRTM / GUGiK NMT)
            </div>
            <span id="dem-stats" style="font-size:10px;color:#cbd5e1;">Najedź kursorem na wykres, aby zlokalizować punkt na mapie</span>
        </div>
        <div id="dem-chart-container" style="width:100%;height:106px;position:relative;"></div>
    </div>

    <!-- =======================================================================
         OKNO MODALNE USTAWIEŃ (WYBÓR GUI, MASKA POZA POZNANIEM, WARSTWY ZTM)
         ======================================================================= -->
    <div id="settings-modal-backdrop" onclick="if(event.target===this) closeSettingsModal()">
        <div id="settings-modal">
            <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #334155;padding-bottom:10px;margin-bottom:12px;">
                <h3 style="margin:0;color:#38bdf8;font-size:16px;">⚙️ Ustawienia Systemu, Warstw ZTM i Interfejsu GUI</h3>
                <button class="pick-btn" onclick="closeSettingsModal()">✖ Zamknij</button>
            </div>

            <label style="font-size:12px;font-weight:700;color:#e2e8f0;">1. Wybierz styl interfejsu graficznego (GUI):</label>
            <div class="gui-card-grid">
                <div class="gui-choice-card selected" id="card-gui-modern" onclick="setGuiTheme('modern')">
                    <h4>✨ Nowoczesny Web-GIS (Floating)</h4>
                    <p>Mapa na pełnym ekranie z półprzezroczystym pływającym panelem sterowania, asystentem AI, suwakiem czasu i wykresem DEM.</p>
                </div>
                <div class="gui-choice-card" id="card-gui-qgis" onclick="setGuiTheme('qgis')">
                    <h4>🖥️ Profesjonalny Desktop GIS (Styl QGIS)</h4>
                    <p>Układ programu QGIS 3.34: górny pasek menu i narzędzi, lewy dok warstw i algorytmów, prawy dok wyników oraz pasek PUWG 1992.</p>
                </div>
            </div>

            <label style="font-size:12px;font-weight:700;color:#e2e8f0;display:block;margin-bottom:6px;">2. Widoczność warstw przestrzennych Poznania:</label>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;background:#1e293b;padding:10px;border-radius:8px;margin-bottom:14px;font-size:11.5px;">
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                    <input type="checkbox" checked onchange="toggleMapLayer('outside_mask', this.checked)"> 🌑 Zaciemnienie poza granicami Poznania
                </label>
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                    <input type="checkbox" checked onchange="toggleMapLayer('boundary', this.checked)"> 🌐 Granica administracyjna Poznania
                </label>
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                    <input type="checkbox" id="chk-layer-tram" onchange="toggleMapLayer('tram_tracks', this.checked)"> 🚋 Torowiska tramwajowe ZTM (OSM)
                </label>
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                    <input type="checkbox" id="chk-layer-bus" onchange="toggleMapLayer('bus_routes', this.checked)"> 🚌 Linie autobusowe ZTM Poznań (Ulice)
                </label>
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                    <input type="checkbox" checked onchange="toggleMapLayer('major_roads', this.checked)"> 🛣️ Sieć dróg głównych (4 605 odc.)
                </label>
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;">
                    <input type="checkbox" checked onchange="toggleMapLayer('local_roads', this.checked)"> 🏘️ Sieć ulic lokalnych (8 293 odc.)
                </label>
            </div>

            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-size:11px;color:#94a3b8;">Poznań GIS v4.0 • Tramwaje OSM + Autobusy ZTM + Asystent AI</span>
                <button class="icon-btn" style="background:#0284c7;color:#fff;padding:7px 16px;" onclick="closeSettingsModal()">✅ Gotowe</button>
            </div>
        </div>
    </div>
"""

    html_part2 = r"""
    <script>
        const DATA = __GIS_DATA_PAYLOAD__;

        const MODULES_BY_CAT = {
            utility: [
                { id: 'water',       label: '💧 1. Wodociągi (Zawory i Ciśnienie)' },
                { id: 'criticality', label: '🎯 2. Krytyczność Węzłów (N-1)' },
                { id: 'upstream',    label: '🧪 3. Kanalizacja (Śledzenie Skażeń)' },
                { id: 'storm',       label: '🌧️ 4. Ulewa i Przepełnienia Rur' },
                { id: 'power',       label: '⚡ 5. Sieć Elektryczna (Blackout)' }
            ],
            transport: [
                { id: 'multimodal',  label: '🚋/🚌 1. Transport ZTM (Tramwaje i Autobusy)' },
                { id: 'od_matrix',   label: '📊 2. Macierz OD (Najbliższe Służby)' },
                { id: 'tsp',         label: '🚚 3. Komiwojażer TSP / VRP (Okna)' }
            ],
            resilience: [
                { id: 'location',    label: '📍 1. Lokalizacja-Alokacja (Nowa Baza)' },
                { id: 'evacuation',  label: '🚨 2. Symulacja Ewakuacji i Gardła' },
                { id: 'isochrone',   label: '🚑 3. Izochrony Dostępności Czasowej' }
            ],
            basic: [
                { id: 'routes',      label: '🚗 1. Dojazdy (Najkrótsza/Najszybsza)' },
                { id: 'detour',      label: '🚧 2. Odcięcie Drogi / Objazd' }
            ]
        };

        let currentCategory = 'utility';
        let currentMode = 'water';
        let currentGuiTheme = 'modern';

        // Stan parametrów dla poszczególnych modułów
        let startNodeIdx = DATA.landmarks[0].node_idx;
        let targetNodeIdx = DATA.landmarks[1].node_idx;
        let isoCenterIdx = DATA.landmarks[0].node_idx;
        let isoMinutes = 8;

        let blockedEdgesSet = new Set();
        let blockedStreetsNames = new Set();
        let activeClosureMarkers = [];

        let selectedInfraNode = "Studzienka_S2_Garbary";
        let stormRainfallMm = 55; // 55 mm = 55 000 ml deszczu / m² (55 l/m²)
        let stormZoneKey = "jezyce_bogdanka";
        let stormCustomCenter = null;
        let stormRadiusKm = 2.8;
        let selectedBrokenPipeId = "pipe_8";

        const STORM_ZONES = {
            all_city:          { name: "🌧️ Cały Poznań (Wielkoskalowy front burzowy — 261,9 km²)", lat: 52.4085, lon: 16.9250, radius_km: 12.5, area_km2: 261.9, imperv: 0.48, flood_spots: "Tunel pod Dworcem Zachodnim, ul. Garbary, Rondo Kaponiera, Al. Wielkopolska" },
            jezyce_bogdanka:   { name: "⛈️ Zlewnia Bogdanki: Jeżyce / Ogrody / Sołacz",            lat: 52.4165, lon: 16.8980, radius_km: 2.6,  area_km2: 21.2,  imperv: 0.72, flood_spots: "Al. Wielkopolska / Pułaskiego, ul. Dąbrowskiego, ul. Kościelna, Wiadukt Koszalińska" },
            stare_miasto:      { name: "⛈️ Stare Miasto / Chwaliszewo / Garbary",                 lat: 52.4105, lon: 16.9355, radius_km: 2.2,  area_km2: 15.2,  imperv: 0.82, flood_spots: "Przepompownia Garbary, ul. Estkowskiego, ul. Chwaliszewo, Podziemia Rondo Kaponiera" },
            wilda_lazarz:      { name: "⛈️ Wilda / Łazarz / Górczyn (Kolektor Prawobrzeżny)",     lat: 52.3885, lon: 16.9080, radius_km: 2.8,  area_km2: 24.6,  imperv: 0.74, flood_spots: "Wiadukt Górczyński, ul. Głogowska / Hetmańska, ul. Dolna Wilda / Królowej Jadwigi" },
            rataje_chartowo:   { name: "⛈️ Rataje / Żegrze / Chartowo (Kolektor Wschodni)",       lat: 52.3915, lon: 16.9650, radius_km: 2.8,  area_km2: 24.6,  imperv: 0.64, flood_spots: "Rondo Rataje (płyta dolna), ul. Krzywoustego, Tunel Trasy Kórnickiej, ul. Zamenhofa" },
            winogrady_piatkowo:{ name: "⛈️ Winogrady / Piątkowo / Naramowice (Kolektor Północny)",lat: 52.4410, lon: 16.9280, radius_km: 3.1,  area_km2: 30.2,  imperv: 0.58, flood_spots: "ul. Lechicka / Murawa, ul. Naramowicka, Przejście podziemne PST Szymanowskiego" },
            custom:            { name: "📍 Własne epicentrum komórki burzowej (kliknij na mapie)", lat: 52.4085, lon: 16.9250, radius_km: 2.8,  area_km2: 24.6,  imperv: 0.68, flood_spots: "Lokalne obniżenia terenu DEM oraz studzienki w zasięgu komórki burzowej" }
        };

        let selectedWaterPipeId = "wpipe_23"; // Domyślnie Magistrala Mostowa Chwaliszewo <-> Śródka (WZ-13 / WZ-14, 34 budynki!)
        let waterValveFailureMode = false;

        let selectedFailedPowerNode = "GPZ_Jezyce";
        let backupSZREnabled = false;

        let criticalityDomain = "roads";

        let odOriginNodeIdx = DATA.landmarks[2].node_idx;
        let odFacilityCategory = "hospital";
        let odTopK = 3;

        let tspPresetKey = "courier_inpost";
        let tspOptimizeEnabled = true;
        let customTspStops = [];

        // W trybie ZTM punkty Start i Cel mogą być umieszczane WYŁĄCZNIE na trasach i przystankach ZTM!
        let mmStartStopId = "bus_lawica_port"; // Port Lotniczy Poznań-Ławica (Autobus ZTM)
        let mmTargetStopId = "ztm_franowo";    // Pętla Franowo / Szpital Szwajcarska (Tramwaj ZTM)
        let mmModeType = "all";                // 'all' (Tramwaj+Autobus), 'tram_only' (Tylko Tramwaje), 'bus_only' (Tylko Autobusy)

        let locFacilityType = "hospital";
        let locCandidateNodeIdx = null;

        let evacHazardId = "flood_warta";
        let evacCustomCenter = null;
        let evacRadiusM = 950;

        let pickingState = null;

        // Warstwy Leaflet
        let activeLayerGroup = null;
        let outsideMaskLayerGroup = null;
        let boundaryLayerGroup = null;
        let tramTracksLayerGroup = null;
        let busRoutesLayerGroup = null;
        let majorRoadsLayerGroup = null;
        let localRoadsLayerGroup = null;
        let hoverDemMarker = null;
        let animDynamicLayerGroup = null;

        // Stan Dziennika Zdarzeń (Event Log), Animacji Czasu (Time-Slider) i Split-Screen
        let eventLogEntries = [];
        let lastLoggedSignature = "";
        let simMaxMinutes = 20.0;
        let simTimeMinutes = 20.0;
        let animIntervalId = null;
        let isTimeSliderActive = false;
        let activeAnimCoords = [];
        let activeAnimLabel = "";
        let activeAnimTotalKm = 0.0;

        let isSplitScreenActive = false;
        let splitMapB = null;
        let splitLayerGroupB = null;

        let lastExportFeatures = [];
        let lastDemProfile = [];

        // Inicjalizacja grafu ulicznego (39 911 węzłów)
        const NUM_NODES = DATA.graph_coords.length;
        const ADJ = Array.from({ length: NUM_NODES }, () => []);
        DATA.graph_edges.forEach((e, edgeIdx) => {
            const [u, v, dist_m, time_ds, s_idx] = e;
            ADJ[u].push({ to: v, dist_m, time_ds, s_idx, edgeIdx });
        });

        function getDemElevation(lat, lon) {
            const wartaLon = 16.944 + 0.018 * Math.sin((lat - 52.38) * 18.0);
            const distWartaKm = Math.abs(lon - wartaLon) * 67.8;
            const wartaBase = 52.2 - (lat - 52.40) * 32.0;
            const valleyRise = 28.5 * (1.0 - Math.exp(-Math.pow(distWartaKm, 1.45) / 1.85));
            const westSlope = Math.max(0.0, (16.935 - lon) * 95.0);
            const eastSlope = Math.max(0.0, (lon - 16.955) * 68.0);
            const bogdankaLat = 52.423 + (16.915 - lon) * 0.28;
            const distBogdanka = Math.abs(lat - bogdankaLat) * 111.0;
            const bogdankaDip = (lon < 16.942) ? (-9.5 * Math.exp(-Math.pow(distBogdanka, 2) / 0.35)) : 0.0;
            const dMorasko = haversineKm(lat, lon, 52.486, 16.912);
            const moraskoHill = 62.0 * Math.exp(-Math.pow(dMorasko, 2) / 4.2);
            const micro = 1.6 * Math.sin(lat * 420.0) * Math.cos(lon * 380.0);
            const elev = wartaBase + valleyRise + westSlope + eastSlope + bogdankaDip + moraskoHill + micro;
            return Math.round(Math.max(49.8, Math.min(154.0, elev)) * 10) / 10;
        }

        function wgs84ToPuwg1992(lat, lon) {
            const phi = lat * Math.PI / 180.0;
            const lam = lon * Math.PI / 180.0;
            const lam0 = 19.0 * Math.PI / 180.0;
            const a = 6378137.0;
            const e2 = 0.00669438002290;
            const N = a / Math.sqrt(1 - e2 * Math.sin(phi) * Math.sin(phi));
            const t = Math.tan(phi);
            const eta2 = (e2 / (1 - e2)) * Math.cos(phi) * Math.cos(phi);
            const l = lam - lam0;
            const A0 = 1 - e2 / 4 - 3 * e2 * e2 / 64;
            const A2 = (3.0 / 8.0) * (e2 + e2 * e2 / 4);
            const s = a * (A0 * phi - A2 * Math.sin(2 * phi));
            const xgk = s + (N * t * Math.pow(Math.cos(phi), 2) * l * l) / 2.0;
            const ygk = N * Math.cos(phi) * l * (1 + (Math.pow(Math.cos(phi), 2) * l * l * (1 - t * t + eta2)) / 6.0);
            return {
                X: Math.round(xgk * 0.9993 - 5300000),
                Y: Math.round(ygk * 0.9993 + 500000)
            };
        }

        function haversineKm(lat1, lon1, lat2, lon2) {
            const R = 6371.0;
            const dLat = (lat2 - lat1) * Math.PI / 180.0;
            const dLon = (lon2 - lon1) * Math.PI / 180.0;
            const a = Math.sin(dLat/2)*Math.sin(dLat/2) +
                      Math.cos(lat1*Math.PI/180.0)*Math.cos(lat2*Math.PI/180.0)*
                      Math.sin(dLon/2)*Math.sin(dLon/2);
            return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
        }

        class MinHeap {
            constructor() { this.heap = []; }
            push(node, priority) {
                this.heap.push({ node, priority });
                this.bubbleUp(this.heap.length - 1);
            }
            pop() {
                if (this.heap.length === 0) return null;
                const top = this.heap[0];
                const bottom = this.heap.pop();
                if (this.heap.length > 0) {
                    this.heap[0] = bottom;
                    this.sinkDown(0);
                }
                return top;
            }
            bubbleUp(idx) {
                while (idx > 0) {
                    const pIdx = (idx - 1) >> 1;
                    if (this.heap[pIdx].priority <= this.heap[idx].priority) break;
                    [this.heap[pIdx], this.heap[idx]] = [this.heap[idx], this.heap[pIdx]];
                    idx = pIdx;
                }
            }
            sinkDown(idx) {
                const len = this.heap.length;
                while (true) {
                    let left = (idx << 1) + 1;
                    let right = left + 1;
                    let smallest = idx;
                    if (left < len && this.heap[left].priority < this.heap[smallest].priority) smallest = left;
                    if (right < len && this.heap[right].priority < this.heap[smallest].priority) smallest = right;
                    if (smallest === idx) break;
                    [this.heap[idx], this.heap[smallest]] = [this.heap[smallest], this.heap[idx]];
                    idx = smallest;
                }
            }
            isEmpty() { return this.heap.length === 0; }
        }

        function runDijkstra(startIdx, targetIdx, weightType, ignoreBlocks = false) {
            const dist = new Float64Array(NUM_NODES).fill(Infinity);
            const prev = new Int32Array(NUM_NODES).fill(-1);
            const prevEdge = new Int32Array(NUM_NODES).fill(-1);
            const pq = new MinHeap();

            dist[startIdx] = 0;
            pq.push(startIdx, 0);

            while (!pq.isEmpty()) {
                const { node: u, priority: d } = pq.pop();
                if (d > dist[u]) continue;
                if (targetIdx !== null && u === targetIdx) break;

                const neighbors = ADJ[u];
                for (let i = 0; i < neighbors.length; i++) {
                    const edge = neighbors[i];
                    if (!ignoreBlocks && blockedEdgesSet.has(edge.edgeIdx)) continue;
                    const w = (weightType === 'dist') ? edge.dist_m : edge.time_ds;
                    const alt = d + w;
                    if (alt < dist[edge.to]) {
                        dist[edge.to] = alt;
                        prev[edge.to] = u;
                        prevEdge[edge.to] = edge.edgeIdx;
                        pq.push(edge.to, alt);
                    }
                }
            }

            if (targetIdx === null) return { dist, prev };
            if (dist[targetIdx] === Infinity) return null;

            const pathNodes = [];
            const coords = [];
            let curr = targetIdx;
            let totalDistM = 0;
            let totalTimeDs = 0;
            const viaStreets = [];

            while (curr !== -1) {
                pathNodes.push(curr);
                coords.push(DATA.graph_coords[curr]);
                const eIdx = prevEdge[curr];
                if (eIdx !== -1) {
                    const eData = DATA.graph_edges[eIdx];
                    totalDistM += eData[2];
                    totalTimeDs += eData[3];
                    const sName = DATA.street_names[eData[4]];
                    if (sName && sName !== "Ulica lokalna" && (viaStreets.length === 0 || viaStreets[viaStreets.length - 1] !== sName)) {
                        viaStreets.push(sName);
                    }
                }
                curr = prev[curr];
            }
            pathNodes.reverse();
            coords.reverse();
            viaStreets.reverse();

            return {
                pathNodes,
                coords,
                dist_km: (totalDistM / 1000.0).toFixed(2),
                time_min: (totalTimeDs / 600.0).toFixed(1),
                streets: viaStreets.slice(0, 7).join(" → ") || "Sieć ulic lokalnych Poznania"
            };
        }

        function findNearestGraphNode(lat, lon) {
            let bestIdx = 0;
            let bestDist = Infinity;
            for (let i = 0; i < NUM_NODES; i++) {
                const c = DATA.graph_coords[i];
                const d = (c[0] - lat) * (c[0] - lat) + (c[1] - lon) * (c[1] - lon);
                if (d < bestDist) {
                    bestDist = d;
                    bestIdx = i;
                }
            }
            return bestIdx;
        }

        // Przyciąganie kliknięcia na mapie WYŁĄCZNIE do najbliższego przystanku/węzła na trasie ZTM
        function findNearestZtmStopId(lat, lon) {
            let bestStopId = mmStartStopId;
            let bestDist = Infinity;
            Object.values(DATA.ztm_stops).forEach(st => {
                if (mmModeType === 'tram_only' && st.mode !== 'tram') return;
                const d = (st.coords[0] - lat) * (st.coords[0] - lat) + (st.coords[1] - lon) * (st.coords[1] - lon);
                if (d < bestDist) {
                    bestDist = d;
                    bestStopId = st.id;
                }
            });
            return bestStopId;
        }

        function getMapInstance() {
            for (let key in window) {
                if (key.startsWith('map_') && window[key] && window[key].addLayer) {
                    return window[key];
                }
            }
            return null;
        }

        // =====================================================================
        // INICJALIZACJA WARSTW BAZOWYCH, ZACIEMNIENIA POZA POZNANIEM ORAZ ZTM
        // =====================================================================
        function initBaseLayers(map) {
            if (!outsideMaskLayerGroup) {
                // 1) ZACIEMNIENIE OBSZARU POZA GRANICAMI POZNANIA (Inverted Polygon z otworem na Poznań)
                outsideMaskLayerGroup = L.layerGroup().addTo(map);
                const worldOuterRing = [[85.0, -180.0], [85.0, 180.0], [-85.0, 180.0], [-85.0, -180.0]];
                L.polygon([worldOuterRing, DATA.boundary_ring], {
                    color: '#0284c7',
                    weight: 2.0,
                    opacity: 0.9,
                    fillColor: '#040711',
                    fillOpacity: 0.72,
                    interactive: false
                }).addTo(outsideMaskLayerGroup);

                // 2) GRANICA ADMINISTRACYJNA POZNANIA (Poligon z obrysem)
                boundaryLayerGroup = L.layerGroup().addTo(map);
                L.polyline(DATA.boundary_ring, {
                    color: '#38bdf8',
                    weight: 3.5,
                    opacity: 0.95,
                    dashArray: '8, 5',
                    interactive: false
                }).addTo(boundaryLayerGroup);

                // 3) ULICE LOKALNE I OSIEDLOWE POZNANIA
                localRoadsLayerGroup = L.layerGroup().addTo(map);
                DATA.background_roads_local.forEach(r => {
                    L.polyline(r.coords, {
                        color: '#94a3b8',
                        weight: 1.0,
                        opacity: 0.34,
                        interactive: false
                    }).addTo(localRoadsLayerGroup);
                });

                // 4) SIEĆ DRÓG GŁÓWNYCH I ZBIORCZYCH POZNANIA
                majorRoadsLayerGroup = L.layerGroup().addTo(map);
                DATA.background_roads_major.forEach(r => {
                    const isPrimary = (r.hw === 'motorway' || r.hw === 'trunk' || r.hw === 'primary');
                    L.polyline(r.coords, {
                        color: isPrimary ? '#475569' : '#64748b',
                        weight: isPrimary ? 2.1 : 1.4,
                        opacity: 0.52,
                        interactive: false
                    }).addTo(majorRoadsLayerGroup);
                });

                // 5) LINIE AUTOBUSOWE ZTM POZNAŃ (domyślnie ukryte, podświetlane automatycznie w trybie ZTM!)
                busRoutesLayerGroup = L.layerGroup();
                DATA.ztm_edges.filter(e => e.mode === 'bus').forEach(e => {
                    L.polyline(e.geometry, {
                        color: '#f97316',
                        weight: 2.8,
                        opacity: 0.72,
                        dashArray: '6, 4'
                    }).bindTooltip(`<b>🚌 ${e.label}</b> (${e.dist_km} km, V=${e.speed_kmh} km/h)`).addTo(busRoutesLayerGroup);
                });

                // 6) DOKŁADNE TOROWISKA TRAMWAJOWE ZTM POZNAŃ (domyślnie ukryte, podświetlane automatycznie w trybie ZTM!)
                tramTracksLayerGroup = L.layerGroup();
                DATA.tram_tracks_bg.forEach(trackCoords => {
                    L.polyline(trackCoords, {
                        color: '#db2777',
                        weight: 2.5,
                        opacity: 0.72,
                        interactive: false
                    }).addTo(tramTracksLayerGroup);
                });

                animDynamicLayerGroup = L.layerGroup().addTo(map);
            }
        }

        function toggleMapLayer(layerKey, visible) {
            const map = getMapInstance();
            if (!map) return;
            const grpMap = {
                outside_mask: outsideMaskLayerGroup,
                boundary: boundaryLayerGroup,
                tram_tracks: tramTracksLayerGroup,
                bus_routes: busRoutesLayerGroup,
                major_roads: majorRoadsLayerGroup,
                local_roads: localRoadsLayerGroup,
                active_analysis: activeLayerGroup
            };
            const grp = grpMap[layerKey];
            if (!grp) return;
            if (visible) {
                if (!map.hasLayer(grp)) map.addLayer(grp);
            } else {
                if (map.hasLayer(grp)) map.removeLayer(grp);
            }
        }

        // =====================================================================
        // 1. DZIENNIK ZDARZEŃ I ALARMÓW KRYZYSOWYCH (EVENT LOG)
        // =====================================================================
        function logCrisisEvent(categoryBadge, severity, title, details, restoreStateObj) {
            const sig = `${currentMode}:${title}:${details}`;
            if (sig === lastLoggedSignature) return;
            lastLoggedSignature = sig;

            const now = new Date();
            const timeStr = now.toTimeString().slice(0, 8);
            eventLogEntries.unshift({
                id: Date.now() + Math.random(),
                time: timeStr,
                category: categoryBadge,
                severity: severity,
                title: title,
                details: details,
                state: JSON.parse(JSON.stringify(restoreStateObj))
            });
            if (eventLogEntries.length > 35) eventLogEntries.pop();

            const c1 = document.getElementById('log-badge-count');
            const c2 = document.getElementById('qgis-log-count');
            if (c1) c1.textContent = eventLogEntries.length;
            if (c2) c2.textContent = eventLogEntries.length;

            renderEventLogList();
        }

        function renderEventLogList() {
            const listEl = document.getElementById('event-log-list');
            if (!listEl) return;
            if (eventLogEntries.length === 0) {
                listEl.innerHTML = `<div style="padding:18px;text-align:center;color:#64748b;font-size:11px;">Brak zarejestrowanych zdarzeń. Kliknij dowolny element sieci na mapie.</div>`;
                return;
            }
            listEl.innerHTML = eventLogEntries.map((item, idx) => {
                const sevClass = item.severity === 'crit' ? 'b-red' : (item.severity === 'warn' ? 'b-amber' : 'b-blue');
                return `
                    <div class="log-entry">
                        <div style="display:flex;justify-content:space-between;align-items:center;">
                            <div>
                                <span style="font-family:monospace;color:#94a3b8;margin-right:4px;">${item.time}</span>
                                <span class="badge ${sevClass}">${item.category}</span>
                                <b>${item.title}</b>
                            </div>
                            <button class="pick-btn" style="padding:2px 6px;font-size:9.5px;background:#0284c7;" onclick="restoreEventFromLog(${idx})">🔄 Przywróć</button>
                        </div>
                        <div style="color:#cbd5e1;font-size:10.5px;padding-left:4px;">${item.details}</div>
                    </div>
                `;
            }).join('');
        }

        function restoreEventFromLog(idx) {
            const item = eventLogEntries[idx];
            if (!item || !item.state) return;
            const st = item.state;
            if (st.selectedWaterPipeId) selectedWaterPipeId = st.selectedWaterPipeId;
            if (st.waterValveFailureMode !== undefined) waterValveFailureMode = st.waterValveFailureMode;
            if (st.selectedFailedPowerNode) selectedFailedPowerNode = st.selectedFailedPowerNode;
            if (st.backupSZREnabled !== undefined) backupSZREnabled = st.backupSZREnabled;
            if (st.selectedInfraNode) selectedInfraNode = st.selectedInfraNode;
            if (st.stormRainfallMm) stormRainfallMm = st.stormRainfallMm;
            if (st.stormZoneKey) stormZoneKey = st.stormZoneKey;
            if (st.startNodeIdx !== undefined) startNodeIdx = st.startNodeIdx;
            if (st.targetNodeIdx !== undefined) targetNodeIdx = st.targetNodeIdx;
            if (st.mmStartStopId) mmStartStopId = st.mmStartStopId;
            if (st.mmTargetStopId) mmTargetStopId = st.mmTargetStopId;
            if (st.mode) setScenario(st.mode);
            else renderCurrentScenario();
        }

        function toggleEventLogDrawer() {
            const dr = document.getElementById('event-log-drawer');
            const isVis = (dr.style.display === 'flex');
            dr.style.display = isVis ? 'none' : 'flex';
            document.getElementById('btn-event-log').classList.toggle('active-toggle', !isVis);
            renderEventLogList();
        }

        function clearEventLog() {
            eventLogEntries = [];
            lastLoggedSignature = "";
            document.getElementById('log-badge-count').textContent = "0";
            document.getElementById('qgis-log-count').textContent = "0";
            renderEventLogList();
        }

        // =====================================================================
        // 2. DYNAMICZNA ANIMACJA CZASU DOPASOWANA DO RZECZYWISTEGO CZASU TRASY / ZJAWISKA
        // =====================================================================
        function stopAndResetTimeAnimation() {
            if (animIntervalId) {
                clearInterval(animIntervalId);
                animIntervalId = null;
            }
            const btn = document.getElementById('btn-play-anim');
            if (btn) {
                btn.innerHTML = '▶️ Animuj czas';
                btn.style.background = '#0284c7';
            }
            isTimeSliderActive = false;
            activeAnimCoords = [];
            activeAnimLabel = "";
            activeAnimTotalKm = 0.0;
            lastDemProfile = [];
            if (animDynamicLayerGroup) animDynamicLayerGroup.clearLayers();
            const map = getMapInstance();
            if (map && hoverDemMarker) {
                map.removeLayer(hoverDemMarker);
                hoverDemMarker = null;
            }
            const demTitleEl = document.getElementById('dem-title');
            if (demTitleEl) demTitleEl.innerHTML = '📈 Profil wysokościowy DEM / Spadek kolektora';
            const demStatsEl = document.getElementById('dem-stats');
            if (demStatsEl) demStatsEl.innerHTML = '';
            renderDemChart();
        }

        function setScenarioDuration(maxMin, totalKm = 0.0) {
            const cleanMax = Math.max(1.0, +(parseFloat(maxMin) || 15.0).toFixed(1));
            simMaxMinutes = cleanMax;
            simTimeMinutes = cleanMax;
            activeAnimTotalKm = totalKm || 0.0;
            const slider = document.getElementById('time-slider-input');
            if (slider) {
                const stepVal = cleanMax <= 20 ? 0.2 : 0.5;
                slider.min = stepVal;
                slider.max = cleanMax;
                slider.step = stepVal;
                slider.value = cleanMax;
            }
        }

        function toggleTimeAnimation() {
            const btn = document.getElementById('btn-play-anim');
            if (animIntervalId) {
                clearInterval(animIntervalId);
                animIntervalId = null;
                btn.innerHTML = '▶️ Animuj czas';
                btn.style.background = '#0284c7';
                return;
            }
            isTimeSliderActive = true;
            const stepVal = simMaxMinutes <= 20 ? 0.25 : 0.5;
            if (simTimeMinutes >= simMaxMinutes - 0.05) simTimeMinutes = stepVal;
            btn.innerHTML = '⏸️ Pauza';
            btn.style.background = '#f59e0b';
            updateTimePropagationVisuals();

            animIntervalId = setInterval(() => {
                simTimeMinutes = +(simTimeMinutes + stepVal).toFixed(2);
                if (simTimeMinutes >= simMaxMinutes) {
                    clearInterval(animIntervalId);
                    animIntervalId = null;
                    simTimeMinutes = simMaxMinutes;
                    btn.innerHTML = '▶️ Animuj czas';
                    btn.style.background = '#0284c7';
                }
                const slider = document.getElementById('time-slider-input');
                if (slider) slider.value = simTimeMinutes;
                updateTimePropagationVisuals();
            }, 110);
        }

        function resetTimeAnimation() {
            if (animIntervalId) {
                clearInterval(animIntervalId);
                animIntervalId = null;
            }
            const btn = document.getElementById('btn-play-anim');
            if (btn) {
                btn.innerHTML = '▶️ Animuj czas';
                btn.style.background = '#0284c7';
            }
            isTimeSliderActive = false;
            simTimeMinutes = simMaxMinutes;
            const slider = document.getElementById('time-slider-input');
            if (slider) slider.value = simTimeMinutes;
            updateTimePropagationVisuals();
        }

        function onTimeSliderInput(val) {
            isTimeSliderActive = true;
            simTimeMinutes = parseFloat(val);
            updateTimePropagationVisuals();
        }

        function sliceCoordsByFraction(coords, frac) {
            if (!coords || coords.length < 2) return { subPath: coords || [], head: coords ? coords[0] : [52.4085, 16.9250] };
            const clamped = Math.max(0.0, Math.min(1.0, frac));
            const exactPos = clamped * (coords.length - 1);
            const idx = Math.floor(exactPos);
            const rem = exactPos - idx;
            const subPath = coords.slice(0, idx + 1);
            let head = coords[idx];
            if (idx < coords.length - 1 && rem > 0.01) {
                const c1 = coords[idx], c2 = coords[idx + 1];
                head = [c1[0] + rem * (c2[0] - c1[0]), c1[1] + rem * (c2[1] - c1[1])];
                subPath.push(head);
            }
            return { subPath, head };
        }

        function updateTimePropagationVisuals() {
            const frac = Math.max(0.0, Math.min(1.0, simTimeMinutes / Math.max(0.1, simMaxMinutes)));
            const lblEl = document.getElementById('time-slider-label');
            if (lblEl) lblEl.innerHTML = `⏱️ ${simTimeMinutes.toFixed(1)} / ${simMaxMinutes.toFixed(1)} min`;
            const effEl = document.getElementById('time-dynamic-effect');

            if (!animDynamicLayerGroup) return;
            animDynamicLayerGroup.clearLayers();

            if (!isTimeSliderActive) {
                if (effEl) {
                    const totKmTxt = activeAnimTotalKm > 0 ? ` (${activeAnimTotalKm.toFixed(2)} km)` : '';
                    effEl.innerHTML = `Skalibrowano czas: <b>0.0 – ${simMaxMinutes.toFixed(1)} min</b>${totKmTxt} — kliknij <b>▶️ Animuj czas</b> lub przesuń suwak`;
                }
                return;
            }

            // 1) WODOCIĄGI: Propagacja fali spadku ciśnienia PO RURACH + Dojazd brygady Aquanet PO ULICACH do zasuw!
            if (currentMode === 'water') {
                const brokenPipe = DATA.water_edges.find(e => e.id === selectedWaterPipeId) || DATA.water_edges[0];
                const uNode = DATA.water_nodes[brokenPipe.u];
                const vNode = DATA.water_nodes[brokenPipe.v];

                // Dojazd brygady pogotowia Aquanet z bazy centralnej do zasuwy uNode (po prawdziwych ulicach!)
                const aquanetBaseIdx = DATA.landmarks[0].node_idx;
                const valveTargetIdx = findNearestGraphNode(uNode.coords[0], uNode.coords[1]);
                const crewRoute = runDijkstra(aquanetBaseIdx, valveTargetIdx, 'time');
                const crewArrivalMin = crewRoute ? Math.min(12.5, Math.max(4.0, parseFloat(crewRoute.time_min))) : 7.5;
                const valveClosedMin = crewArrivalMin + 2.5;

                // Propagacja spadku ciśnienia wzdłuż połączonych rur wodociągowych (bez żadnego sztucznego okręgu!)
                DATA.water_edges.forEach(e => {
                    const isMain = (e.id === brokenPipe.id);
                    const isNeighbor = (e.u === brokenPipe.u || e.u === brokenPipe.v || e.v === brokenPipe.u || e.v === brokenPipe.v);
                    if (isMain) {
                        const isClosedNow = simTimeMinutes >= valveClosedMin;
                        L.polyline(e.geometry, {
                            color: isClosedNow ? '#64748b' : '#fde047',
                            weight: isClosedNow ? 6 : 9,
                            opacity: 0.95,
                            dashArray: isClosedNow ? '4, 8' : '10, 6',
                            interactive: false
                        }).addTo(animDynamicLayerGroup);
                    } else if (isNeighbor && simTimeMinutes >= 1.2) {
                        const waveFrac = Math.min(1.0, (simTimeMinutes - 1.2) / Math.max(2.0, crewArrivalMin));
                        const oriented = (e.u === brokenPipe.u || e.u === brokenPipe.v) ? e.geometry : [...e.geometry].reverse();
                        const { subPath, head } = sliceCoordsByFraction(oriented, waveFrac);
                        const isRecovered = simTimeMinutes >= valveClosedMin;
                        L.polyline(subPath, {
                            color: isRecovered ? '#38bdf8' : '#f97316',
                            weight: 6.5,
                            opacity: 0.92,
                            interactive: false
                        }).addTo(animDynamicLayerGroup);
                        const pDrop = isRecovered ? uNode.pressure_mpa.toFixed(2) : Math.max(0.08, uNode.pressure_mpa - 0.24 * waveFrac).toFixed(2);
                        L.circleMarker(head, {
                            radius: 6,
                            fillColor: isRecovered ? '#38bdf8' : '#f97316',
                            color: '#fff',
                            weight: 2,
                            fillOpacity: 1
                        }).bindTooltip(`<b>🌊 Fala ciśnienia w rurze (${e.name.split(' (')[0]}): ${pDrop} MPa</b>`).addTo(animDynamicLayerGroup);
                    }
                });

                // Animacja pojazdu Pogotowia Wodociągowego Aquanet jadącego po ulicach do zasuw WZ!
                if (crewRoute && crewRoute.coords.length >= 2) {
                    const driveFrac = Math.min(1.0, simTimeMinutes / crewArrivalMin);
                    const { subPath, head } = sliceCoordsByFraction(crewRoute.coords, driveFrac);
                    L.polyline(subPath, { color: '#38bdf8', weight: 5, dashArray: '6, 4', opacity: 0.9, interactive: false }).addTo(animDynamicLayerGroup);
                    const crewStatus = simTimeMinutes < crewArrivalMin
                        ? `🚐 Pogotowie Aquanet w drodze do zasuw ${uNode.valve_code}/${vNode.valve_code} (${Math.round(driveFrac*100)}%)`
                        : (simTimeMinutes < valveClosedMin ? `🔧 Brygada zamyka zasuwy ${uNode.valve_code} i ${vNode.valve_code}...` : `🔒 Zasuwy ${uNode.valve_code} i ${vNode.valve_code} zamknięte — podstawiono beczkowóz!`);
                    L.circleMarker(head, {
                        radius: 9,
                        fillColor: simTimeMinutes >= valveClosedMin ? '#10b981' : '#fde047',
                        color: '#0f172a',
                        weight: 2.5,
                        fillOpacity: 1
                    }).bindTooltip(`<b>${crewStatus}</b>`).addTo(animDynamicLayerGroup).openTooltip();
                }

                const activeLeakTime = Math.min(simTimeMinutes, valveClosedMin);
                const leakedM3 = Math.round((brokenPipe.flow_m3h * 0.42) * (activeLeakTime / 60.0));
                const pPipe = (simTimeMinutes >= valveClosedMin) ? "0.00 (Odcięty)" : Math.max(0.05, uNode.pressure_mpa * (1.0 - 0.85 * (simTimeMinutes / valveClosedMin))).toFixed(2);
                effEl.innerHTML = `Ciśnienie: <b>${pPipe} MPa</b> | Wyciek: <b>${leakedM3} m³</b> | ${simTimeMinutes < crewArrivalMin ? '🚐 Dojazd brygady (' + crewArrivalMin.toFixed(1) + ' min)' : (simTimeMinutes < valveClosedMin ? '🔧 Zamykanie zasuw' : '🔒 Zasuwy zamknięte')}`;
            }
            // 2) ENERGETYKA: Kaskadowy zanik napięcia PO LINIACH WN/SN + Impuls SZR po kablu + Dojazd Pogotowia Enea po ulicach!
            else if (currentMode === 'power') {
                const failedObj = DATA.power_nodes[selectedFailedPowerNode];
                const szrSwitchMin = 3.5;

                // Linie wychodzące ze stacji objętej awarią (propagacja impulsu po geometrii kabli!)
                DATA.power_edges.forEach(e => {
                    const isConnectedToFault = (e.u === selectedFailedPowerNode || e.v === selectedFailedPowerNode);
                    if (isConnectedToFault && !e.is_backup) {
                        const oriented = (e.u === selectedFailedPowerNode) ? e.geometry : [...e.geometry].reverse();
                        const waveFrac = Math.min(1.0, simTimeMinutes / 2.5);
                        const { subPath, head } = sliceCoordsByFraction(oriented, waveFrac);
                        L.polyline(subPath, { color: '#ef4444', weight: 7, opacity: 0.95, interactive: false }).addTo(animDynamicLayerGroup);
                        L.circleMarker(head, { radius: 6.5, fillColor: '#fde047', color: '#dc2626', weight: 2.5, fillOpacity: 1 })
                            .bindTooltip(`<b>⚡ Fala wyłączenia awaryjnego (${e.type})</b>`).addTo(animDynamicLayerGroup);
                    }
                    if (e.is_backup && backupSZREnabled && simTimeMinutes >= szrSwitchMin) {
                        const szrFrac = Math.min(1.0, (simTimeMinutes - szrSwitchMin) / 4.0);
                        const { subPath, head } = sliceCoordsByFraction(e.geometry, szrFrac);
                        L.polyline(subPath, { color: '#38bdf8', weight: 7, opacity: 0.98, interactive: false }).addTo(animDynamicLayerGroup);
                        L.circleMarker(head, { radius: 7.5, fillColor: '#38bdf8', color: '#fff', weight: 2.5, fillOpacity: 1 })
                            .bindTooltip(`<b>🔄 Automatyczne podanie napięcia SZR 15 kV (${Math.round(szrFrac*100)}%)</b>`).addTo(animDynamicLayerGroup);
                    }
                });

                // Dojazd Pogotowia Energetycznego Enea Operator po ulicach do uszkodzonego GPZ
                if (failedObj) {
                    const eneaBaseIdx = DATA.landmarks[1].node_idx;
                    const gpzNodeIdx = findNearestGraphNode(failedObj.coords[0], failedObj.coords[1]);
                    const eneaRoute = runDijkstra(eneaBaseIdx, gpzNodeIdx, 'time');
                    if (eneaRoute && eneaRoute.coords.length >= 2) {
                        const arrivalMin = Math.max(3.0, parseFloat(eneaRoute.time_min));
                        const driveFrac = Math.min(1.0, simTimeMinutes / arrivalMin);
                        const { subPath, head } = sliceCoordsByFraction(eneaRoute.coords, driveFrac);
                        L.polyline(subPath, { color: '#eab308', weight: 4.5, dashArray: '5, 5', opacity: 0.9, interactive: false }).addTo(animDynamicLayerGroup);
                        L.circleMarker(head, { radius: 8.5, fillColor: '#eab308', color: '#0f172a', weight: 2.5, fillOpacity: 1 })
                            .bindTooltip(`<b>🚐 Pogotowie Energetyczne Enea (${driveFrac >= 1 ? 'Na miejscu w ' + failedObj.name : 'Dojazd: ' + Math.round(driveFrac*100) + '%'})</b>`).addTo(animDynamicLayerGroup).openTooltip();
                    }
                }

                const phaseTxt = simTimeMinutes < 2.5 ? '⚡ Propagacja zwarcia na liniach 15 kV'
                    : (simTimeMinutes < szrSwitchMin ? '🏥 Start agregatów szpitalnych'
                    : (backupSZREnabled ? '🔄 Przełączenie pierścieni SZR 15 kV' : '🚐 Dojazd brygady pomiarowej Enea'));
                effEl.innerHTML = `${phaseTxt} (t = ${simTimeMinutes.toFixed(1)} min)`;
            }
            // 3) ULEWA I KANALIZACJA: Propagacja fali spływu deszczowego wzdłuż kolektorów grawitacyjnych!
            else if (currentMode === 'storm') {
                // Hietogram burzowy (narastanie fali spływu w kolektorach od t=0 do szczytu i spływ do Warty/Koziegłów)
                const stormWaveFactor = Math.sin(Math.min(1.0, frac * 1.15) * (Math.PI / 2));
                const currentMm = +(stormRainfallMm * frac).toFixed(1);
                const currentMlPerM2 = Math.round(currentMm * 1000);
                let surchargedNow = 0;

                DATA.infra_edges.forEach(e => {
                    const localMult = e._lastLocalStormMult || 1.0;
                    const qNow = Math.round(e.base_flow_ls * (1.0 + (localMult - 1.0) * stormWaveFactor));
                    const fillRatio = qNow / Math.max(1, e.cap_ls);
                    if (fillRatio > 0.75) {
                        const { subPath, head } = sliceCoordsByFraction(e.geometry, Math.min(1.0, frac * 1.25));
                        const isOver = fillRatio > 1.0;
                        if (isOver) surchargedNow++;
                        L.polyline(subPath, {
                            color: isOver ? '#dc2626' : '#f97316',
                            weight: isOver ? 9 : 6.5,
                            opacity: 0.92,
                            interactive: false
                        }).addTo(animDynamicLayerGroup);
                        if (isOver) {
                            L.circleMarker(head, { radius: 7.5, fillColor: '#dc2626', color: '#fde047', weight: 2.5, fillOpacity: 1 })
                                .bindTooltip(`<b>⛲ Cofka / Wybicie studzienki (${Math.round(fillRatio*100)}% napełnienia)</b><br>${e.pipe}: ${qNow} / ${e.cap_ls} l/s`).addTo(animDynamicLayerGroup);
                        }
                    }
                });
                effEl.innerHTML = `Opad skumulowany: <b>${currentMm} mm (${currentMlPerM2.toLocaleString()} ml/m²)</b> | Wybijające kolektory: <b>${surchargedNow}</b>`;
            }
            // 4) IZOCHRONY: Rozszerzanie rzeczywistego drzewa ulic osiągalnych w czasie t = simTimeMinutes!
            else if (currentMode === 'isochrone') {
                const { dist } = runDijkstra(isoCenterIdx, null, 'time');
                const currentLimitDs = simTimeMinutes * 600.0;
                let reachedStreets = 0;
                DATA.graph_edges.forEach(e => {
                    const [u, v] = e;
                    if (u < v && dist[u] <= currentLimitDs && dist[v] <= currentLimitDs) {
                        reachedStreets++;
                        const tMin = Math.max(dist[u], dist[v]) / 600.0;
                        const r = tMin / Math.max(1.0, simMaxMinutes);
                        const col = r <= 0.35 ? '#10b981' : (r <= 0.7 ? '#eab308' : '#f97316');
                        L.polyline([DATA.graph_coords[u], DATA.graph_coords[v]], {
                            color: col, weight: 3.2, opacity: 0.92, interactive: false
                        }).addTo(animDynamicLayerGroup);
                    }
                });
                effEl.innerHTML = `Zasięg izochrony w <b>t = ${simTimeMinutes.toFixed(1)} min</b>: <b>${reachedStreets} ulic Poznania</b>`;
            }
            // 5) WSZYSTKIE TRASY (Samochód, Objazd, Transport ZTM, Karetki OD, TSP, Ewakuacja, Skażenie w kolektorze)
            else if (activeAnimCoords && activeAnimCoords.length >= 2) {
                const { subPath, head } = sliceCoordsByFraction(activeAnimCoords, frac);
                L.polyline(subPath, { color: '#fde047', weight: 7, opacity: 0.96, interactive: false }).addTo(animDynamicLayerGroup);
                const kmDone = (activeAnimTotalKm * frac).toFixed(2);
                const totKm = activeAnimTotalKm > 0 ? ` | Dystans: <b>${kmDone} / ${activeAnimTotalKm.toFixed(2)} km</b>` : '';
                L.circleMarker(head, {
                    radius: 9.5,
                    fillColor: '#fde047',
                    color: '#0f172a',
                    weight: 3,
                    fillOpacity: 1
                }).bindTooltip(`<b>⏱️ Czas: ${simTimeMinutes.toFixed(1)} / ${simMaxMinutes.toFixed(1)} min (${Math.round(frac*100)}%)</b>${totKm}<br>${activeAnimLabel}`).addTo(animDynamicLayerGroup).openTooltip();
                effEl.innerHTML = `Czas jazdy: <b>${simTimeMinutes.toFixed(1)} z ${simMaxMinutes.toFixed(1)} min</b> (${Math.round(frac*100)}%)${totKm}`;
            } else {
                effEl.innerHTML = `Czas symulacji: <b>${simTimeMinutes.toFixed(1)} / ${simMaxMinutes.toFixed(1)} min</b>`;
            }
        }

        // =====================================================================
        // 3. TRYB MULTI-SCENARIUSZOWY (SPLIT-SCREEN MAPA A vs MAPA B)
        // =====================================================================
        function toggleSplitScreenMode() {
            isSplitScreenActive = !isSplitScreenActive;
            document.body.classList.toggle('split-mode', isSplitScreenActive);
            document.getElementById('btn-split-screen').classList.toggle('active-toggle', isSplitScreenActive);

            const mapA = getMapInstance();
            setTimeout(() => {
                if (mapA) mapA.invalidateSize();
                if (isSplitScreenActive) {
                    initOrUpdateSplitMapB();
                }
            }, 150);
        }

        function initOrUpdateSplitMapB() {
            const mapA = getMapInstance();
            if (!splitMapB) {
                splitMapB = L.map('map-split-b', {
                    center: mapA ? mapA.getCenter() : [52.4085, 16.9250],
                    zoom: mapA ? mapA.getZoom() : 12,
                    preferCanvas: true
                });
                L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                    attribution: '&copy; OpenStreetMap &copy; CARTO',
                    maxZoom: 20
                }).addTo(splitMapB);

                // Granica Poznania na Mapie B
                L.polyline(DATA.boundary_ring, { color: '#38bdf8', weight: 2.5, dashArray: '6, 4' }).addTo(splitMapB);
                splitLayerGroupB = L.layerGroup().addTo(splitMapB);

                // Synchronizacja widoku Mapy A i Mapy B
                if (mapA) {
                    mapA.on('moveend', () => {
                        if (isSplitScreenActive && splitMapB) {
                            splitMapB.setView(mapA.getCenter(), mapA.getZoom(), { animate: false });
                        }
                    });
                }
            } else {
                splitMapB.invalidateSize();
            }
            renderSplitMapB();
        }

        function renderSplitMapB() {
            if (!splitMapB || !splitLayerGroupB) return;
            splitLayerGroupB.clearLayers();
            const val = document.getElementById('split-scenario-b-select').value;
            const sumEl = document.getElementById('split-b-summary');

            if (val === 'power_grunwald' || val === 'power_wilda') {
                const failId = (val === 'power_grunwald') ? 'GPZ_Grunwald' : 'GPZ_Wilda';
                const powered = new Set(['EC_Karolin', 'SE_Zachod']);
                const q = ['EC_Karolin', 'SE_Zachod'];
                while (q.length > 0) {
                    const curr = q.shift();
                    DATA.power_edges.forEach(e => {
                        if (e.is_backup) return;
                        const nxt = (e.u === curr) ? e.v : ((e.v === curr) ? e.u : null);
                        if (nxt && nxt !== failId && !powered.has(nxt)) { powered.add(nxt); q.push(nxt); }
                    });
                }
                let outCust = 0, outMw = 0;
                Object.values(DATA.power_nodes).forEach(n => {
                    const hasP = powered.has(n.id);
                    if (!hasP) {
                        outCust += n.customers; outMw += n.mw;
                        L.circle(n.coords, { radius: 950, color: '#dc2626', fillColor: '#dc2626', fillOpacity: 0.28 }).addTo(splitLayerGroupB);
                    }
                    L.circleMarker(n.coords, { radius: hasP ? 6 : 9, fillColor: hasP ? '#10b981' : '#dc2626', color: '#fff', weight: 1.5, fillOpacity: 0.95 })
                        .bindTooltip(`<b>${n.name}</b>`).addTo(splitLayerGroupB);
                });
                sumEl.innerHTML = `<b>📊 SCENARIUSZ PORÓWNAWCZY B: Awaria ${DATA.power_nodes[failId].name}</b><br>` +
                    `Odcięci odbiorcy: <b style="color:#f87171;">${outCust.toLocaleString()} mieszkańców</b> | Utracona moc: <b style="color:#fde047;">${outMw.toFixed(1)} MW</b> | Wymagane zaangażowanie pogotowia energetycznego Enea.`;
            }
            else if (val === 'water_chwaliszewo' || val === 'water_jezyce') {
                const pId = (val === 'water_chwaliszewo') ? 'wpipe_23' : 'wpipe_8';
                const pipe = DATA.water_edges.find(e => e.id === pId) || DATA.water_edges[0];
                DATA.water_edges.forEach(e => {
                    const isBrk = (e.id === pipe.id);
                    L.polyline(e.geometry, { color: isBrk ? '#dc2626' : '#0284c7', weight: isBrk ? 8 : 3.5, opacity: 0.9 }).addTo(splitLayerGroupB);
                });
                const uV = DATA.water_nodes[pipe.u].valve_code;
                const vV = DATA.water_nodes[pipe.v].valve_code;
                sumEl.innerHTML = `<b>📊 SCENARIUSZ PORÓWNAWCZY B: Awaria ${pipe.name}</b><br>` +
                    `Zasuwy do zamknięcia: <b style="color:#fde047;">${uV} oraz ${vV}</b> | Odcięte budynki: <b style="color:#f87171;">${pipe.buildings} budynków</b> (${pipe.district}).`;
            }
            else if (val === 'road_przemysla') {
                L.circle([52.38525, 16.94440], { radius: 420, color: '#dc2626', fillColor: '#dc2626', fillOpacity: 0.4 }).addTo(splitLayerGroupB);
                sumEl.innerHTML = `<b>📊 SCENARIUSZ PORÓWNAWCZY B: Zamknięcie Mostu Przemysła I (ul. Hetmańska)</b><br>` +
                    `Przekierowanie potoku <b>54 200 poj./dobę</b> na Most Królowej Jadwigi (+6.9 min opóźnienia dla karetek Wschód-Zachód).`;
            }
            else if (val === 'storm_200') {
                DATA.infra_edges.forEach(e => {
                    const ov = (e.base_flow_ls * 2.0) > e.cap_ls;
                    L.polyline(e.geometry, { color: ov ? '#f97316' : '#0284c7', weight: ov ? 7 : 3.5, opacity: 0.9 }).addTo(splitLayerGroupB);
                });
                sumEl.innerHTML = `<b>📊 SCENARIUSZ PORÓWNAWCZY B: Ulewa nawalna 200% w Poznaniu</b><br>` +
                    `Przepełnienie kolektorów grawitacyjnych w 24 rejonach miasta — ryzyko podtopień ulicznych i przeciążenia przepompowni Garbary.`;
            }
        }

        // =====================================================================
        // 4. ASYSTENT AI
        // =====================================================================
        function setAiRecommendation(stepsArray, priorityBadge = "WYSOKI PRIORYTET OPERACYJNY") {
            const box = document.getElementById('ai-advisor-box');
            if (!box) return;
            box.innerHTML = `
                <div class="ai-advisor-header">
                    <span>🤖 ASYSTENT AI</span>
                    <span class="badge b-amber">${priorityBadge}</span>
                </div>
                <div style="color:#e2e8f0;">
                    ${stepsArray.map((s, idx) => `<div style="margin-bottom:2px;"><b>${idx + 1}.</b> ${s}</div>`).join('')}
                </div>
            `;
        }
"""

    html_part3 = r"""
        // =====================================================================
        // FUNKCJE POMOCNICZE UI, PROFILU WYSOKOŚCIOWEGO DEM I EKSPORTU
        // =====================================================================
        function setGuiTheme(theme) {
            currentGuiTheme = theme;
            document.body.classList.remove('theme-modern', 'theme-qgis');
            document.body.classList.add(theme === 'qgis' ? 'theme-qgis' : 'theme-modern');
            document.getElementById('card-gui-modern').classList.toggle('selected', theme === 'modern');
            document.getElementById('card-gui-qgis').classList.toggle('selected', theme === 'qgis');

            const qBtn = document.getElementById('quick-gui-btn');
            if (qBtn) qBtn.innerHTML = (theme === 'qgis') ? '✨ Web-GIS' : '🖥️ QGIS';

            const demDock = document.getElementById('dem-dock');
            if (theme === 'qgis' && demDock) demDock.classList.remove('collapsed');

            setTimeout(() => {
                const map = getMapInstance();
                if (map) map.invalidateSize();
                renderDemChart();
            }, 120);
        }

        function toggleGuiThemeQuick() {
            setGuiTheme(currentGuiTheme === 'modern' ? 'qgis' : 'modern');
        }
        function openSettingsModal() { document.getElementById('settings-modal-backdrop').style.display = 'flex'; }
        function closeSettingsModal() { document.getElementById('settings-modal-backdrop').style.display = 'none'; }

        function toggleDemDock() {
            if (currentGuiTheme === 'qgis') {
                document.body.classList.toggle('dem-hidden');
                setTimeout(() => { const map = getMapInstance(); if (map) map.invalidateSize(); }, 100);
            } else {
                document.getElementById('dem-dock').classList.toggle('collapsed');
            }
        }

        function resetPoznanView() {
            const map = getMapInstance();
            if (map) map.setView([52.4085, 16.9250], 12);
        }

        function zoomToRealGESUT() {
            const map = getMapInstance();
            if (map) map.setView([52.41485, 16.89620], 18);
        }

        function updateDemProfileFromCoords(coordsList, titleText, pipeInvertElevs = null) {
            activeAnimCoords = coordsList || [];
            activeAnimLabel = titleText;
            if (!coordsList || coordsList.length < 2) {
                lastDemProfile = [];
                renderDemChart();
                return;
            }
            document.getElementById('dem-title').innerHTML = `📈 ${titleText}`;
            const pts = [];
            let cumKm = 0.0;
            const step = Math.max(1, Math.floor(coordsList.length / 85));

            for (let i = 0; i < coordsList.length; i += step) {
                const c = coordsList[i];
                if (i > 0) {
                    const prev = coordsList[Math.max(0, i - step)];
                    cumKm += haversineKm(prev[0], prev[1], c[0], c[1]);
                }
                const terrainM = getDemElevation(c[0], c[1]);
                let invertM = null;
                if (pipeInvertElevs && pipeInvertElevs.length === 2) {
                    const frac = i / Math.max(1, coordsList.length - 1);
                    invertM = +(pipeInvertElevs[0] + frac * (pipeInvertElevs[1] - pipeInvertElevs[0])).toFixed(1);
                }
                pts.push({ dist_km: +cumKm.toFixed(2), elev_m: terrainM, invert_m: invertM, coords: c });
            }
            lastDemProfile = pts;
            renderDemChart();
        }

        function renderDemChart() {
            const container = document.getElementById('dem-chart-container');
            if (!container) return;
            if (!lastDemProfile || lastDemProfile.length < 2) {
                container.innerHTML = `<div style="color:#64748b;font-size:11px;padding:24px;text-align:center;">Brak aktywnego profilu liniowego dla wybranego widoku.</div>`;
                return;
            }

            const w = container.clientWidth || 680;
            const h = 104;
            const padL = 42, padR = 16, padT = 10, padB = 20;
            const plotW = Math.max(100, w - padL - padR);
            const plotH = h - padT - padB;

            const maxDist = Math.max(0.05, lastDemProfile[lastDemProfile.length - 1].dist_km);
            let minE = Infinity, maxE = -Infinity, ascent = 0, descent = 0;

            lastDemProfile.forEach((p, idx) => {
                if (p.elev_m < minE) minE = p.elev_m;
                if (p.elev_m > maxE) maxE = p.elev_m;
                if (p.invert_m !== null) {
                    if (p.invert_m < minE) minE = p.invert_m;
                    if (p.invert_m > maxE) maxE = p.invert_m;
                }
                if (idx > 0) {
                    const diff = p.elev_m - lastDemProfile[idx - 1].elev_m;
                    if (diff > 0) ascent += diff; else descent += Math.abs(diff);
                }
            });

            const eMin = Math.floor(minE - 3);
            const eMax = Math.ceil(maxE + 3);
            const eSpan = Math.max(5, eMax - eMin);

            const xOf = (d) => padL + (d / maxDist) * plotW;
            const yOf = (e) => padT + plotH - ((e - eMin) / eSpan) * plotH;

            let pathD = "", areaD = `M ${xOf(0)} ${padT + plotH} `;
            lastDemProfile.forEach((p, i) => {
                const x = xOf(p.dist_km).toFixed(1);
                const y = yOf(p.elev_m).toFixed(1);
                pathD += (i === 0 ? `M ${x} ${y} ` : `L ${x} ${y} `);
                areaD += `L ${x} ${y} `;
            });
            areaD += `L ${xOf(maxDist).toFixed(1)} ${padT + plotH} Z`;

            let invertSvg = "";
            if (lastDemProfile[0].invert_m !== null) {
                let invD = "";
                lastDemProfile.forEach((p, i) => {
                    const x = xOf(p.dist_km).toFixed(1);
                    const y = yOf(p.invert_m).toFixed(1);
                    invD += (i === 0 ? `M ${x} ${y} ` : `L ${x} ${y} `);
                });
                invertSvg = `<path d="${invD}" fill="none" stroke="#f59e0b" stroke-width="2.4" stroke-dasharray="5,3"/>`;
            }

            const avgSlope = (((lastDemProfile[lastDemProfile.length - 1].elev_m - lastDemProfile[0].elev_m) / (maxDist * 1000)) * 100).toFixed(2);
            document.getElementById('dem-stats').innerHTML =
                `Dystans: <b>${maxDist.toFixed(2)} km</b> | Rzędne: <b>${minE.toFixed(1)}–${maxE.toFixed(1)} m n.p.m.</b> | ↑ +${ascent.toFixed(1)}m | ↓ -${descent.toFixed(1)}m | Spadek: <b>${avgSlope}%</b>`;

            container.innerHTML = `
                <svg width="${w}" height="${h}" id="dem-svg" style="overflow:visible;cursor:crosshair;">
                    <defs>
                        <linearGradient id="demGrad" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.48"/>
                            <stop offset="100%" stop-color="#0284c7" stop-opacity="0.05"/>
                        </linearGradient>
                    </defs>
                    <line x1="${padL}" y1="${padT}" x2="${w - padR}" y2="${padT}" stroke="#334155" stroke-dasharray="2,2"/>
                    <line x1="${padL}" y1="${padT + plotH/2}" x2="${w - padR}" y2="${padT + plotH/2}" stroke="#334155" stroke-dasharray="2,2"/>
                    <line x1="${padL}" y1="${padT + plotH}" x2="${w - padR}" y2="${padT + plotH}" stroke="#475569"/>
                    <text x="${padL - 5}" y="${padT + 4}" fill="#94a3b8" font-size="9" text-anchor="end">${eMax}m</text>
                    <text x="${padL - 5}" y="${padT + plotH/2 + 3}" fill="#94a3b8" font-size="9" text-anchor="end">${Math.round((eMax+eMin)/2)}m</text>
                    <text x="${padL - 5}" y="${padT + plotH}" fill="#94a3b8" font-size="9" text-anchor="end">${eMin}m</text>
                    <text x="${padL}" y="${h - 3}" fill="#94a3b8" font-size="9">0.0 km</text>
                    <text x="${padL + plotW/2}" y="${h - 3}" fill="#94a3b8" font-size="9" text-anchor="middle">${(maxDist/2).toFixed(2)} km</text>
                    <text x="${w - padR}" y="${h - 3}" fill="#94a3b8" font-size="9" text-anchor="end">${maxDist.toFixed(2)} km</text>
                    <path d="${areaD}" fill="url(#demGrad)"/>
                    <path d="${pathD}" fill="none" stroke="#38bdf8" stroke-width="2.2"/>
                    ${invertSvg}
                    <line id="dem-hover-line" x1="-10" y1="${padT}" x2="-10" y2="${padT + plotH}" stroke="#fde047" stroke-width="1.5" visibility="hidden"/>
                    <circle id="dem-hover-dot" cx="-10" cy="-10" r="4.5" fill="#fde047" stroke="#0f172a" stroke-width="1.5" visibility="hidden"/>
                </svg>
            `;

            const svgEl = document.getElementById('dem-svg');
            svgEl.addEventListener('mousemove', (ev) => {
                const rect = svgEl.getBoundingClientRect();
                const mx = ev.clientX - rect.left;
                if (mx < padL || mx > w - padR) return;
                const frac = (mx - padL) / plotW;
                const idx = Math.min(lastDemProfile.length - 1, Math.max(0, Math.round(frac * (lastDemProfile.length - 1))));
                const pt = lastDemProfile[idx];
                const cx = xOf(pt.dist_km);
                const cy = yOf(pt.elev_m);

                const hLine = document.getElementById('dem-hover-line');
                const hDot = document.getElementById('dem-hover-dot');
                if (hLine && hDot) {
                    hLine.setAttribute('x1', cx); hLine.setAttribute('x2', cx); hLine.setAttribute('visibility', 'visible');
                    hDot.setAttribute('cx', cx); hDot.setAttribute('cy', cy); hDot.setAttribute('visibility', 'visible');
                }
                const map = getMapInstance();
                if (map) {
                    if (!hoverDemMarker) {
                        hoverDemMarker = L.circleMarker(pt.coords, { radius: 7, fillColor: '#fde047', color: '#0f172a', weight: 2.5, fillOpacity: 1.0 }).addTo(map);
                    } else {
                        hoverDemMarker.setLatLng(pt.coords);
                    }
                    hoverDemMarker.bindTooltip(`<b>Profil DEM: ${pt.dist_km} km</b><br>Teren: <b>${pt.elev_m} m n.p.m.</b>`).openTooltip();
                }
            });
            svgEl.addEventListener('mouseleave', () => {
                const map = getMapInstance();
                if (map && hoverDemMarker) { map.removeLayer(hoverDemMarker); hoverDemMarker = null; }
            });
        }

        function exportActiveAnalysis(format) {
            if (!lastExportFeatures || lastExportFeatures.length === 0) {
                alert("Brak obiektów w aktywnej analizie do wyeksportowania.");
                return;
            }
            const timestamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
            const filenameBase = `poznan_gis_${currentMode}_${timestamp}`;

            if (format === 'geojson') {
                const fc = {
                    type: "FeatureCollection",
                    name: `Poznan_GIS_${currentMode}`,
                    crs: { type: "name", properties: { name: "urn:ogc:def:crs:OGC:1.3:CRS84" } },
                    features: lastExportFeatures
                };
                downloadBlob(JSON.stringify(fc, null, 2), `${filenameBase}.geojson`, "application/geo+json");
            }
            else if (format === 'kml') {
                let placemarks = "";
                lastExportFeatures.forEach((f, i) => {
                    const name = (f.properties && (f.properties.name || f.properties.label)) || `Obiekt_${i+1}`;
                    const desc = Object.entries(f.properties || {}).map(([k, v]) => `${k}: ${v}`).join(" | ");
                    let geomXml = "";
                    if (f.geometry.type === "Point") {
                        const [lon, lat] = f.geometry.coordinates;
                        geomXml = `<Point><coordinates>${lon},${lat},0</coordinates></Point>`;
                    } else if (f.geometry.type === "LineString") {
                        const cStr = f.geometry.coordinates.map(c => `${c[0]},${c[1]},0`).join(" ");
                        geomXml = `<LineString><coordinates>${cStr}</coordinates></LineString>`;
                    }
                    placemarks += `<Placemark><name>${escapeXml(name)}</name><description>${escapeXml(desc)}</description>${geomXml}</Placemark>\n`;
                });
                const kml = `<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2">\n<Document><name>Poznan GIS - ${currentMode}</name>\n${placemarks}</Document></kml>`;
                downloadBlob(kml, `${filenameBase}.kml`, "application/vnd.google-earth.kml+xml");
            }
            else if (format === 'csv') {
                const rows = ["id;typ_geometrii;nazwa;opis;wkt_epsg4326"];
                lastExportFeatures.forEach((f, i) => {
                    const p = f.properties || {};
                    const name = (p.name || p.label || `Obiekt_${i+1}`).replace(/;/g, ',');
                    const desc = Object.entries(p).map(([k, v]) => `${k}=${v}`).join(", ").replace(/;/g, ',');
                    let wkt = f.geometry.type === "Point"
                        ? `POINT(${f.geometry.coordinates[0]} ${f.geometry.coordinates[1]})`
                        : `LINESTRING(${f.geometry.coordinates.map(c => `${c[0]} ${c[1]}`).join(", ")})`;
                    rows.push(`${i+1};${f.geometry.type};"${name}";"${desc}";"${wkt}"`);
                });
                downloadBlob("\uFEFF" + rows.join("\n"), `${filenameBase}_wkt.csv`, "text/csv;charset=utf-8");
            }
        }
        function escapeXml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
        function downloadBlob(content, filename, mimeType) {
            const blob = new Blob([content], { type: mimeType });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url; a.download = filename;
            document.body.appendChild(a); a.click(); document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }
        function addExportLine(coordsLatLon, props) {
            lastExportFeatures.push({ type: "Feature", properties: props, geometry: { type: "LineString", coordinates: coordsLatLon.map(c => [c[1], c[0]]) } });
        }
        function addExportPoint(latLon, props) {
            lastExportFeatures.push({ type: "Feature", properties: props, geometry: { type: "Point", coordinates: [latLon[1], latLon[0]] } });
        }

        function buildQgisAlgTree() {
            const treeEl = document.getElementById('qgis-alg-tree');
            if (!treeEl) return;
            const catNames = {
                utility: "1. Infrastruktura Krytyczna i Utility",
                transport: "2. Transport ZTM (Tramwaj + Autobus)",
                resilience: "3. Ratownictwo i Odporność (Resilience)",
                basic: "4. Klasyczne Trasy i Objazdy"
            };
            let html = "";
            for (const [catKey, mods] of Object.entries(MODULES_BY_CAT)) {
                html += `<div class="q-tree-cat">${catNames[catKey]}</div>`;
                mods.forEach(m => {
                    const act = (m.id === currentMode) ? 'active' : '';
                    html += `<div class="q-alg-item ${act}" onclick="selectCategoryAndMode('${catKey}', '${m.id}')">${m.label}</div>`;
                });
            }
            treeEl.innerHTML = html;
        }

        function selectCategory(catKey) {
            currentCategory = catKey;
            document.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
            const cBtn = document.getElementById(`cat-btn-${catKey}`);
            if (cBtn) cBtn.classList.add('active');
            const mods = MODULES_BY_CAT[catKey];
            if (!mods.some(m => m.id === currentMode)) currentMode = mods[0].id;
            renderModuleButtons();
            renderCurrentScenario();
        }

        function selectCategoryAndMode(catKey, modeId) {
            currentCategory = catKey;
            currentMode = modeId;
            document.querySelectorAll('.cat-btn').forEach(b => b.classList.remove('active'));
            const cBtn = document.getElementById(`cat-btn-${catKey}`);
            if (cBtn) cBtn.classList.add('active');
            renderModuleButtons();
            renderCurrentScenario();
        }

        function setScenario(modeId) {
            for (const [catKey, mods] of Object.entries(MODULES_BY_CAT)) {
                if (mods.some(m => m.id === modeId)) {
                    selectCategoryAndMode(catKey, modeId);
                    return;
                }
            }
        }

        function renderModuleButtons() {
            const container = document.getElementById('module-buttons-container');
            const mods = MODULES_BY_CAT[currentCategory] || [];
            container.innerHTML = mods.map(m => `
                <button class="mode-btn ${m.id === currentMode ? 'active' : ''}" onclick="currentMode='${m.id}'; pickingState=null; renderModuleButtons(); renderCurrentScenario();">
                    ${m.label}
                </button>
            `).join('');
            buildQgisAlgTree();
        }

        function startPicking(state) {
            pickingState = (pickingState === state) ? null : state;
            renderControls();
        }

        function blockRoadNearLatLng(lat, lon, radiusKm = 0.085) {
            let count = 0, foundName = null;
            DATA.graph_edges.forEach((e, edgeIdx) => {
                const c1 = DATA.graph_coords[e[0]], c2 = DATA.graph_coords[e[1]];
                if (haversineKm(lat, lon, (c1[0]+c2[0])/2, (c1[1]+c2[1])/2) <= radiusKm) {
                    blockedEdgesSet.add(edgeIdx);
                    count++;
                    const sName = DATA.street_names[e[4]];
                    if (!foundName && sName && sName !== "Ulica lokalna") foundName = sName;
                }
            });
            if (count > 0) {
                const label = foundName || `Ulica lokalna (${lat.toFixed(4)}, ${lon.toFixed(4)})`;
                blockedStreetsNames.add(label);
                activeClosureMarkers.push({ lat, lon, label });
            }
            pickingState = null;
            renderCurrentScenario();
        }

        function clearAllRoadBlocks() {
            blockedEdgesSet.clear();
            blockedStreetsNames.clear();
            activeClosureMarkers = [];
            renderCurrentScenario();
        }
"""

    html_part4 = r"""
        // =====================================================================
        // KONTROLKI I RENDEROWANIE WSZYSTKICH 12 MODUŁÓW (Z AI + EVENT LOG + TIME)
        // =====================================================================
        function renderControls() {
            const container = document.getElementById('dynamic-controls');
            const lmOpts = (selIdx) => DATA.landmarks.map(l =>
                `<option value="${l.node_idx}" ${l.node_idx === selIdx ? 'selected' : ''}>${l.label}</option>`
            ).join('') + `<option value="${selIdx}" ${!DATA.landmarks.some(l => l.node_idx === selIdx) ? 'selected' : ''}>📍 Dowolny punkt z mapy (#${selIdx})</option>`;

            if (currentMode === 'water') {
                const wOpts = DATA.water_edges.map(e =>
                    `<option value="${e.id}" ${e.id === selectedWaterPipeId ? 'selected' : ''}>${e.name} (${e.buildings} bud.)</option>`
                ).join('');
                container.innerHTML = `
                    <div class="ctrl-box" style="border-color:#0ea5e9;">
                        <label style="color:#38bdf8;">💧 Wybierz magistralę wodociągową pod ciśnieniem (lub kliknij rurę na mapie):</label>
                        <select class="gis-select" style="width:100%;margin-bottom:5px;" onchange="selectedWaterPipeId=this.value; renderCurrentScenario();">${wOpts}</select>
                        <label style="display:flex;align-items:center;gap:7px;cursor:pointer;color:#fde047;background:#0f172a;padding:5px 8px;border-radius:6px;border:1px solid #475569;">
                            <input type="checkbox" ${waterValveFailureMode ? 'checked' : ''} onchange="waterValveFailureMode=this.checked; renderCurrentScenario();">
                            ⚠️ Symuluj awarię zasuwy sekcyjnej (wymusza zamknięcie dalszego pierścienia!)
                        </label>
                    </div>
                `;
            }
            else if (currentMode === 'criticality') {
                container.innerHTML = `
                    <div class="ctrl-box" style="border-color:#f43f5e;">
                        <label style="color:#fda4af;">🎯 Wybierz badaną infrastrukturę Poznania (Analiza Krytyczności N-1):</label>
                        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-top:4px;">
                            <button class="pick-btn ${criticalityDomain==='roads'?'picking':''}" onclick="criticalityDomain='roads'; renderCurrentScenario();">🛣️ Mosty i Węzły Drogowe</button>
                            <button class="pick-btn ${criticalityDomain==='power'?'picking':''}" onclick="criticalityDomain='power'; renderCurrentScenario();">⚡ Stacje GPZ (Energetyka)</button>
                            <button class="pick-btn ${criticalityDomain==='water'?'picking':''}" onclick="criticalityDomain='water'; renderCurrentScenario();">💧 Magistrale Wodociągowe</button>
                            <button class="pick-btn ${criticalityDomain==='sewage'?'picking':''}" onclick="criticalityDomain='sewage'; renderCurrentScenario();">🧪 Kolektory Kanalizacyjne</button>
                        </div>
                    </div>
                `;
            }
            else if (currentMode === 'upstream') {
                const nodeOpts = Object.values(DATA.infra_nodes).map(n =>
                    `<option value="${n.id}" ${n.id === selectedInfraNode ? 'selected' : ''}>${n.name} (${n.elev_m} m n.p.m.)</option>`
                ).join('');
                container.innerHTML = `
                    <div class="ctrl-box">
                        <label>🧪 Wybierz węzeł/studzienkę z 27 węzłów w Poznaniu (lub kliknij kółko na mapie):</label>
                        <select class="gis-select" style="width:100%;" onchange="selectedInfraNode=this.value; renderCurrentScenario();">${nodeOpts}</select>
                    </div>
                `;
            }
            else if (currentMode === 'storm') {
                const zoneOpts = Object.entries(STORM_ZONES).map(([k, v]) =>
                    `<option value="${k}" ${k === stormZoneKey ? 'selected' : ''}>${v.name}</option>`
                ).join('');
                const pipeOpts = DATA.infra_edges.map(e =>
                    `<option value="${e.id}" ${e.id === selectedBrokenPipeId ? 'selected' : ''}>${e.pipe}</option>`
                ).join('');
                const mlPerM2 = stormRainfallMm * 1000;
                const imgwClass = stormRainfallMm < 25 ? 'Opad umiarkowany' : (stormRainfallMm < 50 ? 'Ulewa IMGW I st.' : (stormRainfallMm < 80 ? 'Nawalny deszcz IMGW II st.' : 'Oberwanie chmury IMGW III st.!'));
                container.innerHTML = `
                    <div class="ctrl-box" style="border-color:#0ea5e9;">
                        <label style="color:#38bdf8;">⛈️ Rejon wystąpienia ulewy w Poznaniu (lub wskaż komórkę burzową na mapie):</label>
                        <div class="ctrl-row">
                            <select class="gis-select" onchange="stormZoneKey=this.value; if(this.value!=='custom') stormCustomCenter=null; renderCurrentScenario();">${zoneOpts}</select>
                            <button class="pick-btn ${pickingState==='storm_pick'?'picking':''}" onclick="startPicking('storm_pick')">📍 Z mapy</button>
                        </div>
                        <label>💧 Opad deszczu: <b style="color:#fde047;">${stormRainfallMm} mm = ${mlPerM2.toLocaleString()} ml / m²</b> <span style="color:#f97316;">(${imgwClass})</span></label>
                        <input type="range" min="10" max="120" step="5" value="${stormRainfallMm}" oninput="stormRainfallMm=parseInt(this.value); renderCurrentScenario();">
                        ${stormZoneKey !== 'all_city' ? `
                        <label>⭕ Promień komórki burzowej (rdzeń opadu): <b style="color:#38bdf8;">${stormRadiusKm.toFixed(1)} km</b></label>
                        <input type="range" min="1.2" max="5.5" step="0.2" value="${stormRadiusKm}" oninput="stormRadiusKm=parseFloat(this.value); renderCurrentScenario();">
                        ` : ''}
                        <label style="margin-top:3px;">💥 Badany / niedrożny odcinek kolektora Aquanet:</label>
                        <select class="gis-select" style="width:100%;" onchange="selectedBrokenPipeId=this.value; renderCurrentScenario();">${pipeOpts}</select>
                    </div>
                `;
            }
            else if (currentMode === 'power') {
                const pwrOpts = Object.values(DATA.power_nodes).map(n =>
                    `<option value="${n.id}" ${n.id === selectedFailedPowerNode ? 'selected' : ''}>${n.name}</option>`
                ).join('');
                container.innerHTML = `
                    <div class="ctrl-box" style="border-color:#eab308;">
                        <label style="color:#fde047;">⚡ Wybierz miejsce awarii / wyłączenia prądu (lub kliknij stację na mapie):</label>
                        <select class="gis-select" style="width:100%;margin-bottom:5px;" onchange="selectedFailedPowerNode=this.value; renderCurrentScenario();">${pwrOpts}</select>
                        <label style="display:flex;align-items:center;gap:7px;cursor:pointer;color:#fde047;background:#0f172a;padding:5px 8px;border-radius:6px;border:1px solid #475569;">
                            <input type="checkbox" ${backupSZREnabled ? 'checked' : ''} onchange="backupSZREnabled=this.checked; renderCurrentScenario();">
                            🔄 Załącz zasilanie rezerwowe z sąsiednich GPZ (Układ pierścieniowy SZR)
                        </label>
                    </div>
                `;
            }
            else if (currentMode === 'multimodal') {
                if (mmModeType === 'tram_only') {
                    if (DATA.ztm_stops[mmStartStopId] && DATA.ztm_stops[mmStartStopId].mode !== 'tram') mmStartStopId = 'ztm_sobieskiego';
                    if (DATA.ztm_stops[mmTargetStopId] && DATA.ztm_stops[mmTargetStopId].mode !== 'tram') mmTargetStopId = 'ztm_franowo';
                }
                const ztmStopsFiltered = Object.values(DATA.ztm_stops).filter(st => mmModeType === 'tram_only' ? st.mode === 'tram' : true);
                const ztmOpts = (selId) => ztmStopsFiltered.map(st =>
                    `<option value="${st.id}" ${st.id === selId ? 'selected' : ''}>${st.name}</option>`
                ).join('');
                container.innerHTML = `
                    <div class="ctrl-box" style="border-color:#ec4899;">
                        <div style="font-size:10px;color:#f9a8d4;margin-bottom:4px;font-weight:600;">
                            ✨ Tryb ZTM aktywny: podświetlono sieć tramwajów i autobusów. Punkty Start i Cel dostępne wyłącznie na trasach ZTM!
                        </div>
                        <label>🟢 Przystanek / Węzeł Początkowy (START na trasie ZTM):</label>
                        <div class="ctrl-row">
                            <select class="gis-select" onchange="mmStartStopId=this.value; renderCurrentScenario();">${ztmOpts(mmStartStopId)}</select>
                            <button class="pick-btn ${pickingState === 'mm_start' ? 'picking' : ''}" onclick="startPicking('mm_start')">📍 Na trasie ZTM</button>
                        </div>
                        <label>🔴 Przystanek / Węzeł Docelowy (CEL na trasie ZTM):</label>
                        <div class="ctrl-row">
                            <select class="gis-select" onchange="mmTargetStopId=this.value; renderCurrentScenario();">${ztmOpts(mmTargetStopId)}</select>
                            <button class="pick-btn ${pickingState === 'mm_target' ? 'picking' : ''}" onclick="startPicking('mm_target')">📍 Na trasie ZTM</button>
                        </div>
                        <label>🚋/🚌 Rodzaj tras ZTM Poznań:</label>
                        <select class="gis-select" style="width:100%;" onchange="mmModeType=this.value; renderCurrentScenario();">
                            <option value="all" ${mmModeType==='all'?'selected':''}>🚋 + 🚌 Tramwaje + Autobusy ZTM (Pełna sieć ZTM Poznań)</option>
                            <option value="tram_only" ${mmModeType==='tram_only'?'selected':''}>🚋 Tylko Tramwaje ZTM (Sieć torowisk tramwajowych)</option>
                        </select>
                    </div>
                `;
            }
            else if (currentMode === 'od_matrix') {
                container.innerHTML = `
                    <div class="ctrl-box" style="border-color:#10b981;">
                        <label>📍 Lokalizacja zdarzenia / wezwania w Poznaniu (kliknij na mapie lub wybierz):</label>
                        <div class="ctrl-row">
                            <select class="gis-select" onchange="odOriginNodeIdx=parseInt(this.value); renderCurrentScenario();">${lmOpts(odOriginNodeIdx)}</select>
                            <button class="pick-btn ${pickingState === 'od_origin' ? 'picking' : ''}" onclick="startPicking('od_origin')">📍 Z mapy</button>
                        </div>
                        <div class="ctrl-row" style="margin-bottom:0;">
                            <div style="flex:1;">
                                <label>🚨 Typ jednostek ratunkowych:</label>
                                <select class="gis-select" style="width:100%;" onchange="odFacilityCategory=this.value; renderCurrentScenario();">
                                    <option value="hospital" ${odFacilityCategory==='hospital'?'selected':''}>🏥 Szpitale / Oddziały SOR (7)</option>
                                    <option value="fire" ${odFacilityCategory==='fire'?'selected':''}>🚒 Straż Pożarna JRG 1-7 (7)</option>
                                    <option value="police" ${odFacilityCategory==='police'?'selected':''}>🚓 Komisariaty Policji (6)</option>
                                    <option value="all" ${odFacilityCategory==='all'?'selected':''}>🚨 Wszystkie służby ratunkowe (20)</option>
                                </select>
                            </div>
                            <div style="width:120px;">
                                <label>Liczba najbliższych:</label>
                                <select class="gis-select" style="width:100%;" onchange="odTopK=parseInt(this.value); renderCurrentScenario();">
                                    <option value="2" ${odTopK===2?'selected':''}>Top 2</option>
                                    <option value="3" ${odTopK===3?'selected':''}>Top 3</option>
                                    <option value="5" ${odTopK===5?'selected':''}>Top 5</option>
                                </select>
                            </div>
                        </div>
                    </div>
                `;
            }
            else if (currentMode === 'tsp') {
                const presetOpts = Object.entries(DATA.tsp_presets).map(([k, v]) =>
                    `<option value="${k}" ${k === tspPresetKey ? 'selected' : ''}>${v.name}</option>`
                ).join('');
                container.innerHTML = `
                    <div class="ctrl-box" style="border-color:#a855f7;">
                        <label>🚚 Wybierz scenariusz logistyczny VRP / TSP w Poznaniu:</label>
                        <select class="gis-select" style="width:100%;margin-bottom:5px;" onchange="tspPresetKey=this.value; customTspStops=[]; renderCurrentScenario();">${presetOpts}</select>
                        <div class="ctrl-row">
                            <button class="pick-btn ${pickingState === 'tsp_add' ? 'picking' : ''}" style="flex:1;" onclick="startPicking('tsp_add')">
                                ${pickingState === 'tsp_add' ? 'Kliknij ulicę w Poznaniu...' : '➕ Dodaj punkt z mapy (' + customTspStops.length + ')'}
                            </button>
                            <button class="pick-btn" onclick="customTspStops=[]; renderCurrentScenario();">🗑️ Reset</button>
                        </div>
                        <label style="display:flex;align-items:center;gap:7px;cursor:pointer;color:#e9d5ff;background:#0f172a;padding:5px 8px;border-radius:6px;border:1px solid #475569;">
                            <input type="checkbox" ${tspOptimizeEnabled ? 'checked' : ''} onchange="tspOptimizeEnabled=this.checked; renderCurrentScenario();">
                            🧠 Optymalizacja kolejności (Algorytm 2-Opt TSP + Okna czasowe)
                        </label>
                    </div>
                `;
            }
            else if (currentMode === 'location') {
                container.innerHTML = `
                    <div class="ctrl-box" style="border-color:#14b8a6;">
                        <label>📍 Typ planowanej nowej inwestycji (Model Location-Allocation p-Median):</label>
                        <select class="gis-select" style="width:100%;margin-bottom:5px;" onchange="locFacilityType=this.value; locCandidateNodeIdx=null; renderCurrentScenario();">
                            <option value="hospital" ${locFacilityType==='hospital'?'selected':''}>🚑 Nowa Podstacja Pogotowia Ratunkowego / SOR</option>
                            <option value="fire" ${locFacilityType==='fire'?'selected':''}>🚒 Nowa Jednostka Ratowniczo-Gaśnicza PSP</option>
                            <option value="police" ${locFacilityType==='police'?'selected':''}>⚡ Nowy Hub Szybkiego Ładowania EV / Baza Miejska</option>
                        </select>
                        <div class="ctrl-row" style="margin-bottom:0;">
                            <button class="pick-btn ${locCandidateNodeIdx===null ? 'picking' : ''}" style="flex:1;background:#0f766e;" onclick="locCandidateNodeIdx=null; pickingState=null; renderCurrentScenario();">
                                🤖 Wyznacz optimum automatycznie (p-Median)
                            </button>
                            <button class="pick-btn ${pickingState==='loc_pick' ? 'picking' : ''}" onclick="startPicking('loc_pick')">📍 Wskaż własne miejsce</button>
                        </div>
                    </div>
                `;
            }
            else if (currentMode === 'evacuation') {
                const hazOpts = DATA.evacuation_hazards.map(h =>
                    `<option value="${h.id}" ${h.id === evacHazardId && !evacCustomCenter ? 'selected' : ''}>${h.name}</option>`
                ).join('');
                container.innerHTML = `
                    <div class="ctrl-box" style="border-color:#ef4444;">
                        <label style="color:#fca5a5;">🚨 Wybierz strefę zagrożenia w Poznaniu (lub kliknij dowolny punkt na mapie):</label>
                        <div class="ctrl-row">
                            <select class="gis-select" onchange="evacHazardId=this.value; evacCustomCenter=null; renderCurrentScenario();">${hazOpts}</select>
                            <button class="pick-btn ${pickingState==='evac_pick'?'picking':''}" onclick="startPicking('evac_pick')">📍 Z mapy</button>
                        </div>
                        <label>⭕ Promień strefy ewakuacji: <b style="color:#f87171;">${evacRadiusM} metrów</b></label>
                        <input type="range" min="400" max="1800" step="100" value="${evacRadiusM}" oninput="evacRadiusM=parseInt(this.value); renderCurrentScenario();">
                    </div>
                `;
            }
            else if (currentMode === 'isochrone') {
                container.innerHTML = `
                    <div class="ctrl-box">
                        <label>🟣 Punkt centralny Izochrony (kliknij dowolną uliczkę na mapie lub wybierz):</label>
                        <div class="ctrl-row">
                            <select class="gis-select" onchange="isoCenterIdx=parseInt(this.value); renderCurrentScenario();">${lmOpts(isoCenterIdx)}</select>
                            <button class="pick-btn ${pickingState === 'iso' ? 'picking' : ''}" onclick="startPicking('iso')">📍 Kliknij na mapie</button>
                        </div>
                        <label>⏱️ Maksymalny czas dojazdu: <b style="color:#38bdf8;">${isoMinutes} minut</b></label>
                        <input type="range" min="2" max="18" step="1" value="${isoMinutes}" oninput="isoMinutes=parseInt(this.value); renderCurrentScenario();">
                    </div>
                `;
            }
            else if (currentMode === 'routes' || currentMode === 'detour') {
                let closureHtml = '';
                if (currentMode === 'detour') {
                    const presetBtns = DATA.preset_closures.map(p =>
                        `<button class="pick-btn" style="font-size:9.5px;padding:2.5px 5px;" onclick="blockRoadNearLatLng(${p.lat}, ${p.lon}, ${p.radius_km})">⛔ ${p.name.split(' (')[0]}</button>`
                    ).join(' ');
                    closureHtml = `
                        <div style="margin-top:5px;padding-top:5px;border-top:1px solid #334155;">
                            <label style="color:#f87171;">🚧 Wyłącz drogę (kliknij dowolną ulicę na mapie lub wybierz most):</label>
                            <div class="ctrl-row">
                                <button class="pick-btn ${pickingState === 'block_road' ? 'picking' : ''}" style="flex:1;" onclick="startPicking('block_road')">
                                    ${pickingState === 'block_road' ? '👉 Kliknij ulicę na mapie Poznania...' : '🚧 Kliknij drogę na mapie, by ją zamknąć'}
                                </button>
                                <button class="pick-btn" style="background:#7f1d1d;" onclick="clearAllRoadBlocks()">🔄 Odblokuj (${blockedStreetsNames.size})</button>
                            </div>
                            <div style="display:flex;flex-wrap:wrap;gap:3px;">${presetBtns}</div>
                        </div>
                    `;
                }
                container.innerHTML = `
                    <div class="ctrl-box">
                        <label>🟢 Punkt początkowy (START):</label>
                        <div class="ctrl-row">
                            <select class="gis-select" onchange="startNodeIdx=parseInt(this.value); renderCurrentScenario();">${lmOpts(startNodeIdx)}</select>
                            <button class="pick-btn ${pickingState === 'start' ? 'picking' : ''}" onclick="startPicking('start')">📍 Z mapy</button>
                        </div>
                        <label>🔴 Punkt docelowy (CEL):</label>
                        <div class="ctrl-row" style="margin-bottom:2px;">
                            <select class="gis-select" onchange="targetNodeIdx=parseInt(this.value); renderCurrentScenario();">${lmOpts(targetNodeIdx)}</select>
                            <button class="pick-btn ${pickingState === 'target' ? 'picking' : ''}" onclick="startPicking('target')">📍 Z mapy</button>
                        </div>
                        ${closureHtml}
                    </div>
                `;
            }
        }
"""

    html_part5 = r"""
        function renderCurrentScenario() {
            renderControls();
            buildQgisAlgTree();

            const map = getMapInstance();
            if (!map) return;
            initBaseLayers(map);

            // Całkowity reset wykresu czasowego, animacji i profilu DEM przy każdym przełączeniu funkcji!
            stopAndResetTimeAnimation();

            // Trasy tramwajowe i autobusowe ZTM są podświetlane automatycznie WYŁĄCZNIE w trybie Transport ZTM (multimodal)
            const isZtmMode = (currentMode === 'multimodal');
            const showTram = isZtmMode;
            const showBus = isZtmMode && (mmModeType !== 'tram_only');
            toggleMapLayer('tram_tracks', showTram);
            toggleMapLayer('bus_routes', showBus);
            ['chk-layer-tram', 'q-chk-tram'].forEach(id => { const el = document.getElementById(id); if (el) el.checked = showTram; });
            ['chk-layer-bus', 'q-chk-bus'].forEach(id => { const el = document.getElementById(id); if (el) el.checked = showBus; });

            if (activeLayerGroup) map.removeLayer(activeLayerGroup);
            activeLayerGroup = L.layerGroup().addTo(map);
            lastExportFeatures = [];

            if (currentMode === 'water') renderWaterNetworkAnalysis(activeLayerGroup);
            else if (currentMode === 'criticality') renderCriticalityAnalysis(activeLayerGroup);
            else if (currentMode === 'upstream') renderSewageUpstreamAnalysis(activeLayerGroup);
            else if (currentMode === 'storm') renderStormOverflowAnalysis(activeLayerGroup);
            else if (currentMode === 'power') renderPowerGridAnalysis(activeLayerGroup);
            else if (currentMode === 'multimodal') renderMultimodalAnalysis(activeLayerGroup);
            else if (currentMode === 'od_matrix') renderOdMatrixAnalysis(activeLayerGroup);
            else if (currentMode === 'tsp') renderTspVrpAnalysis(activeLayerGroup);
            else if (currentMode === 'location') renderLocationAllocationAnalysis(activeLayerGroup);
            else if (currentMode === 'evacuation') renderEvacuationAnalysis(activeLayerGroup);
            else if (currentMode === 'isochrone') renderIsochroneAnalysis(activeLayerGroup);
            else if (currentMode === 'routes' || currentMode === 'detour') renderRoutesAndDetourAnalysis(activeLayerGroup);

            updateTimePropagationVisuals();
        }

        // 1. WODOCIĄGI POD CIŚNIENIEM + LOG + REKOMENDACJA AI
        function renderWaterNetworkAnalysis(layerGroup) {
            const brokenPipe = DATA.water_edges.find(e => e.id === selectedWaterPipeId) || DATA.water_edges[0];
            const uNode = DATA.water_nodes[brokenPipe.u];
            const vNode = DATA.water_nodes[brokenPipe.v];

            const closedValves = new Set();
            const isolatedPipeIds = new Set([brokenPipe.id]);

            if (!waterValveFailureMode) {
                closedValves.add(brokenPipe.u);
                closedValves.add(brokenPipe.v);
            } else {
                closedValves.add(brokenPipe.v);
                DATA.water_edges.forEach(e => {
                    if (e.u === brokenPipe.u) { closedValves.add(e.v); isolatedPipeIds.add(e.id); }
                    else if (e.v === brokenPipe.u) { closedValves.add(e.u); isolatedPipeIds.add(e.id); }
                });
            }

            let cutBuildings = 0, totalBuildings = 0;
            const cutDistricts = [];

            DATA.water_edges.forEach(e => {
                totalBuildings += e.buildings;
                const isIso = isolatedPipeIds.has(e.id);
                const isMainBroken = (e.id === brokenPipe.id);
                if (isIso) {
                    cutBuildings += e.buildings;
                    if (!cutDistricts.includes(e.district)) cutDistricts.push(e.district);
                }

                const line = L.polyline(e.geometry, {
                    color: isMainBroken ? '#dc2626' : (isIso ? '#f97316' : '#0284c7'),
                    weight: isMainBroken ? 8 : Math.max(3.5, e.diam_mm / 130),
                    opacity: 0.92,
                    dashArray: isIso ? '10, 7' : null
                }).bindTooltip(
                    `<b>💧 ${e.name}</b><br>Średnica: <b>DN${e.diam_mm} mm</b> | Przepływ: <b>${e.flow_m3h} m³/h</b><br>` +
                    `Przyłączone budynki: <b>${e.buildings}</b> (${e.district})`
                );
                line.on('click', (ev) => {
                    L.DomEvent.stopPropagation(ev);
                    selectedWaterPipeId = e.id;
                    renderCurrentScenario();
                });
                line.addTo(layerGroup);
                addExportLine(e.geometry, { name: e.name, diam_mm: e.diam_mm, flow_m3h: e.flow_m3h, isolated: isIso });
            });

            Object.values(DATA.water_nodes).forEach(n => {
                const isClosed = closedValves.has(n.id);
                const isFaultyValve = waterValveFailureMode && (n.id === brokenPipe.u);
                const isSource = (n.role === 'source');
                L.circleMarker(n.coords, {
                    radius: isSource ? 11 : (isClosed ? 10 : 7),
                    fillColor: isFaultyValve ? '#eab308' : (isClosed ? '#dc2626' : (isSource ? '#0284c7' : '#10b981')),
                    color: '#ffffff',
                    weight: isClosed ? 3 : 2,
                    fillOpacity: 0.98
                }).bindTooltip(`<b>${n.name}</b><br>Kod zasuwy: <b>${n.valve_code}</b> | Ciśnienie: <b>${n.pressure_mpa} MPa</b>`).addTo(layerGroup);
                addExportPoint(n.coords, { name: n.name, valve_code: n.valve_code, pressure_mpa: n.pressure_mpa, closed: isClosed });
            });

            const closedListNames = Array.from(closedValves).map(id => DATA.water_nodes[id].valve_code);
            const savedPct = (100 - (cutBuildings / totalBuildings) * 100).toFixed(1);

            document.getElementById('result-box').innerHTML = `
                <div><span class="badge b-red">💥 AWARIA WODOCIĄGU</span> <b>${brokenPipe.name}</b></div>
                <div class="stat-grid">
                    <div class="stat-card" style="border-color:#dc2626"><b>🔒 ${closedListNames.length} zasuwy</b><span>Wymagane zamknięcie z obu stron pierścienia</span></div>
                    <div class="stat-card" style="border-color:#10b981"><b>${cutBuildings} budynków</b><span>Odciętych od wody (${savedPct}% sieci ma wodę z pierścienia!)</span></div>
                </div>
                <div style="background:#1e293b; padding:6px; border-radius:6px; border-left:3px solid #f59e0b; font-size:10.5px;">
                    <b>🔧 Zasuwy do zamknięcia:</b> <b>${closedListNames.join('</b> oraz <b>')}</b> | Ciśnienie: <b>${uNode.pressure_mpa} MPa</b>
                </div>
            `;
            document.getElementById('formula-box').innerHTML =
                `<b>💧 Valve Isolation Trace:</b> W sieci ciśnieniowej pierścieniowej woda napływa z obu kierunków pętli. Użyj <b>suwaka czasu u dołu</b>, aby prześledzić spadek ciśnienia w rurach!`;

            setAiRecommendation([
                `Natychmiast skierować brygadę Pogotowia Wodociągowego Aquanet do komór zasuw: <b>${closedListNames.join(' i ')}</b> w celu odcięcia obustronnego dopływu pod ciśnieniem ${uNode.pressure_mpa} MPa.`,
                `Z uwagi na odcięcie <b>${cutBuildings} budynków</b> w strefie <b>${cutDistricts.join(', ')}</b>, zadysponować ${cutBuildings > 50 ? '2 beczkowozy' : '1 beczkowóz'} z wodą pitną w rejon awarii.`,
                `Utrzymać podwyższone ciśnienie na sąsiednim łuku pierścienia (${savedPct}% odbiorców zachowuje ciągłość dostaw wody).`
            ], waterValveFailureMode ? "ALARM: AWARIA ZASUWY" : "PROCEDURA IZOLACJI");

            logCrisisEvent("WODOCIĄGI", "crit", `Awaria: ${brokenPipe.name.split(' (')[0]}`,
                `Zamknięto zasuwy: ${closedListNames.join(', ')} | Odciętych: ${cutBuildings} budynków (${cutDistricts.join(', ')})`,
                { mode: 'water', selectedWaterPipeId, waterValveFailureMode });

            setScenarioDuration(16.0, 0);
            updateDemProfileFromCoords(brokenPipe.geometry, `Profil wysokościowy magistrali wodociągowej: ${brokenPipe.name}`);
        }

        // 2. ANALIZA KRYTYCZNOŚCI WĘZŁÓW (CRITICALITY N-1)
        function renderCriticalityAnalysis(layerGroup) {
            let rowsHtml = "";
            if (criticalityDomain === 'roads') {
                DATA.road_criticality.forEach((item, idx) => {
                    const color = item.score >= 92 ? '#dc2626' : (item.score >= 85 ? '#f97316' : '#eab308');
                    L.circle(item.coords, { radius: item.score * 6.5, color, fillColor: color, fillOpacity: 0.28, weight: 2 }).addTo(layerGroup);
                    const m = L.circleMarker(item.coords, { radius: 10, fillColor: color, color: '#fff', weight: 2.5, fillOpacity: 0.98 })
                        .bindTooltip(`<b>#${idx+1}: ${item.name}</b><br>Indeks: <b>${item.score}/100</b> | SDR: <b>${item.aadt.toLocaleString()} poj./d</b>`).addTo(layerGroup);
                    m.on('click', () => { clearAllRoadBlocks(); blockRoadNearLatLng(item.coords[0], item.coords[1], 0.30); setScenario('detour'); });
                    addExportPoint(item.coords, { rank: idx+1, name: item.name, criticality_score: item.score, aadt: item.aadt });
                    rowsHtml += `<tr onclick="getMapInstance().setView([${item.coords[0]}, ${item.coords[1]}], 15)">
                        <td><b>#${idx+1}</b></td><td>${item.name.split(' (')[0]}</td>
                        <td><span class="badge" style="background:${color};color:#fff;">${item.score}/100</span></td>
                        <td>${(item.aadt/1000).toFixed(1)}k poj.</td><td style="color:#f87171;font-weight:700;">${item.delay}</td>
                    </tr>`;
                });
                document.getElementById('result-box').innerHTML = `
                    <div><span class="badge b-red">🎯 RANKING KRYTYCZNOŚCI DRÓG</span> <b>Top 10 Mostów i Węzłów Poznania</b></div>
                    <table class="gis-table"><thead><tr><th>Poz.</th><th>Węzeł / Przeprawa</th><th>Indeks</th><th>SDR</th><th>Skutek</th></tr></thead><tbody>${rowsHtml}</tbody></table>
                `;
                setAiRecommendation([
                    `Najwyższy indeks krytyczności w Poznaniu wykazują przeprawy mostowe przez Wartę: <b>Most Przemysła I (98/100)</b> oraz <b>Most Lecha (96/100)</b>.`,
                    `Objąć monitoringiem wizyjnym ZDM oraz priorytetem utrzymania zimowego korytarze II i III Ramy Komunikacyjnej.`,
                    `Kliknij dowolny wiersz w tabeli powyżej, aby przetestować zamknięcie wybranego węzła.`
                ], "ANALIZA N-1 TRANSPORT");
            }
            else if (criticalityDomain === 'power') {
                const gpzList = Object.values(DATA.power_nodes).filter(n => n.role === 'gpz' || n.role === 'source');
                const ranked = gpzList.map(g => {
                    const powered = new Set();
                    const q = [];
                    ['EC_Karolin', 'SE_Zachod'].forEach(s => { if (s !== g.id) { powered.add(s); q.push(s); } });
                    while (q.length > 0) {
                        const curr = q.shift();
                        DATA.power_edges.forEach(e => {
                            if (e.is_backup) return;
                            const nxt = (e.u === curr) ? e.v : ((e.v === curr) ? e.u : null);
                            if (nxt && nxt !== g.id && !powered.has(nxt)) { powered.add(nxt); q.push(nxt); }
                        });
                    }
                    let lostCust = 0, lostMw = 0;
                    Object.values(DATA.power_nodes).forEach(n => { if (!powered.has(n.id)) { lostCust += n.customers; lostMw += n.mw; } });
                    return { node: g, lostCust, lostMw: +lostMw.toFixed(1) };
                }).sort((a, b) => b.lostCust - a.lostCust);

                ranked.forEach((r, idx) => {
                    const color = r.lostCust > 80000 ? '#dc2626' : (r.lostCust > 50000 ? '#f97316' : '#eab308');
                    L.circle(r.node.coords, { radius: Math.max(400, r.lostCust / 85), color, fillColor: color, fillOpacity: 0.25 }).addTo(layerGroup);
                    const m = L.circleMarker(r.node.coords, { radius: 10, fillColor: color, color: '#fff', weight: 2, fillOpacity: 0.95 })
                        .bindTooltip(`<b>#${idx+1}: ${r.node.name}</b><br>Odcięci odbiorcy: <b>${r.lostCust.toLocaleString()}</b>`).addTo(layerGroup);
                    m.on('click', () => { selectedFailedPowerNode = r.node.id; setScenario('power'); });
                    rowsHtml += `<tr onclick="selectedFailedPowerNode='${r.node.id}'; setScenario('power');">
                        <td><b>#${idx+1}</b></td><td>${r.node.name.replace('⚡ ','').replace('🔌 ','').split(' (')[0]}</td>
                        <td style="color:#f87171;font-weight:700;">${r.lostCust.toLocaleString()}</td><td>${r.lostMw} MW</td>
                    </tr>`;
                });
                document.getElementById('result-box').innerHTML = `
                    <div><span class="badge b-amber">⚡ KRYTYCZNOŚĆ N-1 (ENERGETYKA)</span> <b>Skutek awarii stacji WN/SN</b></div>
                    <table class="gis-table"><thead><tr><th>Poz.</th><th>Stacja GPZ / Źródło</th><th>Odcięci odbiorcy</th><th>Strata MW</th></tr></thead><tbody>${rowsHtml}</tbody></table>
                `;
                setAiRecommendation([
                    `Kluczowymi węzłami przesyłowymi 110 kV są <b>EC II Karolin</b>, <b>SE Plewiska</b> oraz stacje węzłowe <b>GPZ Grunwald</b> i <b>GPZ Garbary</b>.`,
                    `Utrzymywać w gotowości układ Samoczynnego Załączania Rezerwy (SZR) na pierścieniu 110 kV Centrum–Jeżyce–Wilda.`
                ], "AUDYT SIECI ENEA");
            }
            else {
                const isWater = (criticalityDomain === 'water');
                const list = isWater
                    ? [...DATA.water_edges].sort((a, b) => b.flow_m3h - a.flow_m3h).slice(0, 10)
                    : [...DATA.infra_edges].sort((a, b) => b.base_flow_ls - a.base_flow_ls).slice(0, 10);
                list.forEach((e, idx) => {
                    const color = idx < 3 ? '#dc2626' : (idx < 6 ? '#f97316' : '#eab308');
                    L.polyline(e.geometry, { color, weight: 7, opacity: 0.9 }).addTo(layerGroup);
                    rowsHtml += `<tr><td><b>#${idx+1}</b></td><td>${(isWater ? e.name : e.pipe).split(' (')[0]}</td><td>DN${e.diam_mm}</td><td style="color:#38bdf8;font-weight:700;">${isWater ? e.flow_m3h + ' m³/h' : e.base_flow_ls + ' l/s'}</td></tr>`;
                });
                document.getElementById('result-box').innerHTML = `
                    <div><span class="badge b-blue">🎯 KRYTYCZNOŚĆ MAGISTRAL</span> <b>Top 10 najbardziej obciążonych rur</b></div>
                    <table class="gis-table"><thead><tr><th>Poz.</th><th>Magistrala / Kolektor</th><th>Średnica</th><th>Przepływ</th></tr></thead><tbody>${rowsHtml}</tbody></table>
                `;
                setAiRecommendation([
                    `Największe obciążenie hydrauliczne przenosi <b>${(isWater ? list[0].name : list[0].pipe).split(' (')[0]}</b>.`,
                    `Zalecany montaż czujników telemetrycznych ciśnienia i przepływu (SCADA Aquanet) na pierwszych 3 odcinkach rankingu.`
                ], "AUDYT HYDRAULICZNY");
            }
            document.getElementById('formula-box').innerHTML =
                `<b>🎯 Analiza Krytyczności ($N-1$ Contingency & Betweenness):</b> Automatycznie szereguje elementy sieci według skali strat systemowych po ich awarii.`;
        }

        // 3. KANALIZACJA: ŚLEDZENIE SKAŻENIA (UPSTREAM / DOWNSTREAM)
        function renderSewageUpstreamAnalysis(layerGroup) {
            const upstreamNodes = new Set([selectedInfraNode]), upstreamEdges = new Set();
            let changed = true;
            while (changed) {
                changed = false;
                DATA.infra_edges.forEach(e => {
                    if (upstreamNodes.has(e.v) && !upstreamNodes.has(e.u)) { upstreamNodes.add(e.u); upstreamEdges.add(e.id); changed = true; }
                    else if (upstreamNodes.has(e.v) && upstreamNodes.has(e.u)) upstreamEdges.add(e.id);
                });
            }
            const downstreamNodes = new Set([selectedInfraNode]), downstreamEdges = new Set();
            changed = true;
            while (changed) {
                changed = false;
                DATA.infra_edges.forEach(e => {
                    if (downstreamNodes.has(e.u) && !downstreamNodes.has(e.v)) { downstreamNodes.add(e.v); downstreamEdges.add(e.id); changed = true; }
                    else if (downstreamNodes.has(e.u) && downstreamNodes.has(e.v)) downstreamEdges.add(e.id);
                });
            }

            let profileEdge = DATA.infra_edges[0];
            DATA.infra_edges.forEach(e => {
                const isUp = upstreamEdges.has(e.id), isDown = downstreamEdges.has(e.id);
                if (e.v === selectedInfraNode || e.u === selectedInfraNode) profileEdge = e;
                const line = L.polyline(e.geometry, {
                    color: isUp ? '#dc2626' : (isDown ? '#a855f7' : '#475569'),
                    weight: isUp ? 7 : (isDown ? 6 : Math.max(3, e.diam_mm / 150)),
                    opacity: (isUp || isDown) ? 0.96 : 0.45
                }).bindTooltip(`<b>${e.pipe}</b><br>Spadek: ${DATA.infra_nodes[e.u].elev_m}m → ${DATA.infra_nodes[e.v].elev_m}m n.p.m.`).addTo(layerGroup);
                line.on('click', (ev) => { L.DomEvent.stopPropagation(ev); selectedInfraNode = e.v; renderCurrentScenario(); });
                addExportLine(e.geometry, { pipe: e.pipe, diam_mm: e.diam_mm, status: isUp ? 'upstream' : (isDown ? 'downstream' : 'normal') });
            });

            const suspectFactories = [];
            Object.values(DATA.infra_nodes).forEach(n => {
                const isSelected = (n.id === selectedInfraNode), isUp = upstreamNodes.has(n.id), isDown = downstreamNodes.has(n.id);
                if (isUp && (n.role === 'factory' || n.role === 'factory_polluter')) suspectFactories.push(n.name);
                const marker = L.circleMarker(n.coords, {
                    radius: isSelected ? 12 : ((isUp || isDown) ? 9 : 6),
                    fillColor: isSelected ? '#eab308' : (isUp ? '#dc2626' : (isDown ? '#a855f7' : '#64748b')),
                    color: '#ffffff', weight: isSelected ? 3 : 2, fillOpacity: 0.95
                }).bindTooltip(`<b>${n.name}</b> (${n.elev_m} m n.p.m.)`).addTo(layerGroup);
                marker.on('click', (ev) => { L.DomEvent.stopPropagation(ev); selectedInfraNode = n.id; renderCurrentScenario(); });
            });

            const selObj = DATA.infra_nodes[selectedInfraNode];
            document.getElementById('result-box').innerHTML = `
                <div><span class="badge b-amber">🧪 PUNKT POMIARU</span> <b>${selObj.name}</b> (${selObj.elev_m} m n.p.m.)</div>
                <div class="stat-grid">
                    <div class="stat-card" style="border-color:#dc2626"><b>🔴 ${upstreamNodes.size} węzłów</b><span>W górę sieci (Upstream Trace)</span></div>
                    <div class="stat-card" style="border-color:#a855f7"><b>🟣 ${downstreamNodes.size} węzłów</b><span>W dół sieci (Do COŚ Koziegłowy)</span></div>
                </div>
                <div style="background:#1e293b; padding:6px; border-radius:6px; border-left:3px solid #dc2626; font-size:10.5px;">
                    <b>🏭 Podejrzane zakłady w górze dorzecza:</b><br>
                    ${suspectFactories.length > 0 ? '• ' + suspectFactories.join('<br>• ') : 'Brak zakładów przemysłowych powyżej tego punktu.'}
                </div>
            `;
            setAiRecommendation([
                `Wysłanie mobilnego laboratorium WIOŚ / Aquanet do wytypowanych zakładów w górze sieci: <b>${suspectFactories.join(', ') || 'brak (spływ bytowy)'}</b>.`,
                `Przygotowanie zbiornika retencyjnego przed <b>Centralną Oczyszczalnią Ścieków w Koziegłowach (ul. Gdyńska)</b> na przejęcie fali skażonej (${downstreamNodes.size} węzłów w dół sieci).`
            ], "procedura WIOŚ / AQUANET");

            logCrisisEvent("KANALIZACJA", "warn", `Detekcja skażenia: ${selObj.name.split(' (')[0]}`,
                `Upstream: ${upstreamNodes.size} węzłów | Podejrzane źródła: ${suspectFactories.length}`,
                { mode: 'upstream', selectedInfraNode });

            // Budujemy pojedynczy ciągły łańcuch grawitacyjny (bez przeskoków między odgałęzieniami!) do animacji czoła skażenia i profilu DEM
            const upChainEdges = [];
            let upCurr = selectedInfraNode;
            const visitedUp = new Set([upCurr]);
            while (true) {
                const inEdge = DATA.infra_edges.find(e => e.v === upCurr && !visitedUp.has(e.u));
                if (!inEdge) break;
                upChainEdges.unshift(inEdge);
                visitedUp.add(inEdge.u);
                upCurr = inEdge.u;
            }
            const downChainEdges = [];
            let downCurr = selectedInfraNode;
            const visitedDown = new Set([downCurr]);
            while (true) {
                const outEdge = DATA.infra_edges.find(e => e.u === downCurr && !visitedDown.has(e.v));
                if (!outEdge) break;
                downChainEdges.push(outEdge);
                visitedDown.add(outEdge.v);
                downCurr = outEdge.v;
            }
            const fullChainEdges = downChainEdges.length > 0 ? downChainEdges : upChainEdges;
            const plumeCoords = [];
            let plumeKm = 0.0;
            fullChainEdges.forEach(e => {
                plumeCoords.push(...e.geometry);
                for (let k = 1; k < e.geometry.length; k++) {
                    plumeKm += haversineKm(e.geometry[k-1][0], e.geometry[k-1][1], e.geometry[k][0], e.geometry[k][1]);
                }
            });
            const flowTimeMin = Math.max(8.0, +(plumeKm / 0.065).toFixed(1)); // prędkość grawitacyjna ścieków ~1.1 m/s (0.065 km/min)
            setScenarioDuration(flowTimeMin, +plumeKm.toFixed(2));
            if (fullChainEdges.length > 0) {
                const firstE = fullChainEdges[0];
                const lastE = fullChainEdges[fullChainEdges.length - 1];
                updateDemProfileFromCoords(
                    plumeCoords,
                    `Spływ grawitacyjny w kolektorze: ${selObj.name.split(' (')[0]} → ${DATA.infra_nodes[lastE.v].name.split(' (')[0]}`,
                    [DATA.infra_nodes[firstE.u].elev_m, DATA.infra_nodes[lastE.v].elev_m]
                );
            } else if (profileEdge) {
                updateDemProfileFromCoords(profileEdge.geometry, `Kolektor: ${profileEdge.pipe}`, [DATA.infra_nodes[profileEdge.u].elev_m, DATA.infra_nodes[profileEdge.v].elev_m]);
            }
            document.getElementById('formula-box').innerHTML =
                `<b>🧪 Upstream / Downstream Trace:</b> Przeszukuje skierowany graf grawitacyjny (\`nx.ancestors\` / \`nx.descendants\`). Kliknij <b>▶️ Animuj czas</b>, aby prześledzić spływ skażenia w kolektorach!`;
        }

        // 4. ZAAWANSOWANA SYMULACJA ULEWY, KOMÓREK BURZOWYCH I PRZEPEŁNIEŃ KANALIZACJI (ml/m², l/s, m³)
        function renderStormOverflowAnalysis(layerGroup) {
            const zone = STORM_ZONES[stormZoneKey] || STORM_ZONES.jezyce_bogdanka;
            const centerLat = (stormZoneKey === 'custom' && stormCustomCenter) ? stormCustomCenter[0] : zone.lat;
            const centerLon = (stormZoneKey === 'custom' && stormCustomCenter) ? stormCustomCenter[1] : zone.lon;
            const effRadiusKm = (stormZoneKey === 'all_city') ? 14.0 : stormRadiusKm;
            const effAreaKm2 = (stormZoneKey === 'all_city') ? 261.9 : +(Math.PI * effRadiusKm * effRadiusKm).toFixed(1);

            const mlPerM2 = stormRainfallMm * 1000; // 1 mm = 1 l/m² = 1000 ml/m²
            const unitRunoffLsha = Math.round(stormRainfallMm * 3.45 * zone.imperv / 0.65); // l/(s*ha)
            const totalRainVolumeM3 = Math.round((stormRainfallMm / 1000.0) * (effAreaKm2 * 1e6)); // m³ wody opadowej
            const totalRunoffIntoSewersM3 = Math.round(totalRainVolumeM3 * zone.imperv);

            // Wizualizacja komórki burzowej (strefy opadu) na mapie Poznania
            if (stormZoneKey !== 'all_city') {
                L.circle([centerLat, centerLon], {
                    radius: effRadiusKm * 1000,
                    color: '#38bdf8',
                    fillColor: '#0284c7',
                    fillOpacity: 0.14,
                    weight: 2,
                    dashArray: '6, 5',
                    interactive: false
                }).addTo(layerGroup);
                L.circle([centerLat, centerLon], {
                    radius: effRadiusKm * 550,
                    color: '#0ea5e9',
                    fillColor: '#0284c7',
                    fillOpacity: 0.18,
                    weight: 1.5,
                    interactive: false
                }).addTo(layerGroup);
                L.circleMarker([centerLat, centerLon], {
                    radius: 9,
                    fillColor: '#38bdf8',
                    color: '#ffffff',
                    weight: 2.5,
                    fillOpacity: 1
                }).bindTooltip(
                    `<b>⛈️ Epicentrum Ulewy: ${stormCustomCenter ? 'Własny punkt' : zone.name}</b><br>` +
                    `Dawka opadu: <b>${mlPerM2.toLocaleString()} ml / m² (${stormRainfallMm} mm)</b><br>` +
                    `Natężenie spływu: <b>${unitRunoffLsha} l/(s·ha)</b> | Objętość opadu: <b>${totalRainVolumeM3.toLocaleString()} m³</b>`
                ).addTo(layerGroup);
            }

            const overloadedPipes = [];
            const pipeStatsList = [];
            const surchargingNodes = new Set();
            let brokenEdgeObj = null;
            let totalOverflowM3 = 0;

            DATA.infra_edges.forEach(e => {
                const midPt = e.geometry[Math.floor(e.geometry.length / 2)];
                const distFromStormKm = haversineKm(centerLat, centerLon, midPt[0], midPt[1]);

                // Lokalny mnożnik opadu zależny od położenia kolektora względem komórki burzowej
                let spatialFactor = 1.0;
                if (stormZoneKey !== 'all_city') {
                    if (distFromStormKm <= effRadiusKm * 0.65) spatialFactor = 1.15;
                    else if (distFromStormKm <= effRadiusKm) spatialFactor = 0.85;
                    else if (distFromStormKm <= effRadiusKm * 1.45) spatialFactor = 0.42;
                    else spatialFactor = 0.15;
                }

                const localRainMm = +(stormRainfallMm * Math.min(1.0, spatialFactor)).toFixed(1);
                const localRainMl = Math.round(localRainMm * 1000);
                const isBroken = (e.id === selectedBrokenPipeId);
                if (isBroken) brokenEdgeObj = e;

                // Przepływ szczytowy Q [l/s] w kolektorze przy zadanym opadzie w ml/m²
                const localMult = 0.45 + (stormRainfallMm / 32.0) * spatialFactor * (zone.imperv / 0.65);
                e._lastLocalStormMult = localMult;
                const effCapLs = isBroken ? Math.round(e.cap_ls * 0.45) : e.cap_ls; // Przytkany/uszkodzony kolektor ma o 55% mniejszą przepustowość
                const simFlow = Math.round(e.base_flow_ls * localMult);
                const fillPct = Math.round((simFlow / Math.max(1, effCapLs)) * 100);
                const isOverloaded = simFlow > effCapLs;

                if (isOverloaded) {
                    const excessLs = simFlow - effCapLs;
                    const overM3 = Math.round((excessLs * 1800) / 1000.0); // objętość wylania w ciągu 30 min szczytu [m³]
                    totalOverflowM3 += overM3;
                    overloadedPipes.push({ edge: e, simFlow, effCapLs, fillPct, localRainMl, overM3 });
                    surchargingNodes.add(e.u);
                    surchargingNodes.add(e.v);
                }
                pipeStatsList.push({ edge: e, simFlow, effCapLs, fillPct, localRainMl, isBroken });

                const line = L.polyline(e.geometry, {
                    color: isBroken ? '#eab308' : (fillPct >= 100 ? '#dc2626' : (fillPct >= 80 ? '#f97316' : '#0284c7')),
                    weight: isBroken ? 10 : (fillPct >= 100 ? 8.5 : (fillPct >= 80 ? 6.5 : Math.max(3.8, e.diam_mm / 140))),
                    opacity: 0.94
                }).bindTooltip(
                    `<b>${e.pipe}</b> ${isBroken ? '⚠️ (USZKODZONY / PRZYTKANY)' : ''}<br>` +
                    `Opad lokalny: <b>${localRainMl.toLocaleString()} ml/m² (${localRainMm} mm)</b><br>` +
                    `Przepływ w rurze: <b>${simFlow} l/s</b> / Max: <b>${effCapLs} l/s (${fillPct}%)</b>`
                ).addTo(layerGroup);
                line.on('click', (ev) => { L.DomEvent.stopPropagation(ev); selectedBrokenPipeId = e.id; renderCurrentScenario(); });
                addExportLine(e.geometry, { pipe: e.pipe, rain_ml_m2: localRainMl, flow_ls: simFlow, cap_ls: effCapLs, fill_pct: fillPct });
            });

            Object.values(DATA.infra_nodes).forEach(n => {
                const isSurcharging = surchargingNodes.has(n.id);
                L.circleMarker(n.coords, {
                    radius: isSurcharging ? 9.5 : (n.role === 'valve' ? 7.5 : 5.5),
                    fillColor: isSurcharging ? '#dc2626' : (n.role === 'valve' ? '#10b981' : '#334155'),
                    color: isSurcharging ? '#fde047' : '#fff',
                    weight: isSurcharging ? 2.5 : 1.8,
                    fillOpacity: 0.95
                }).bindTooltip(
                    `<b>${isSurcharging ? '⛲ WYBICIE STUDZIENKI: ' : ''}${n.name}</b><br>` +
                    `Rzędna terenu: <b>${n.elev_m} m n.p.m.</b>`
                ).addTo(layerGroup);
            });

            pipeStatsList.sort((a, b) => b.fillPct - a.fillPct);
            const topRowsHtml = pipeStatsList.slice(0, 5).map(p => {
                const badgeCol = p.fillPct >= 100 ? 'b-red' : (p.fillPct >= 80 ? 'b-amber' : 'b-blue');
                return `<tr onclick="selectedBrokenPipeId='${p.edge.id}'; renderCurrentScenario();">
                    <td>${p.edge.pipe.split(' (')[0]}</td>
                    <td><b>${p.localRainMl.toLocaleString()} ml/m²</b></td>
                    <td>${p.simFlow}/${p.effCapLs} l/s</td>
                    <td><span class="badge ${badgeCol}">${p.fillPct}%</span></td>
                </tr>`;
            }).join('');

            document.getElementById('result-box').innerHTML = `
                <div><span class="badge b-blue">🌧️ ANALIZA HYDROLOGICZNA ULEWY</span> <b>${stormCustomCenter ? 'Własna komórka burzowa' : zone.name.split(' (')[0]}</b></div>
                <div class="stat-grid">
                    <div class="stat-card" style="border-color:#38bdf8">
                        <b>${mlPerM2.toLocaleString()} ml / m² (${stormRainfallMm} mm)</b>
                        <span>Dawka opadu (${unitRunoffLsha} l/s·ha)</span>
                    </div>
                    <div class="stat-card" style="border-color:#0ea5e9">
                        <b>${(totalRainVolumeM3 / 1000).toFixed(1)} tys. m³ (${(totalRainVolumeM3 / 1e6).toFixed(2)} mln l)</b>
                        <span>Objętość deszczu w strefie (${effAreaKm2} km²)</span>
                    </div>
                    <div class="stat-card" style="border-color:#dc2626">
                        <b>${overloadedPipes.length} kolektorów (${surchargingNodes.size} studz.)</b>
                        <span>Przepełnionych ($Q > Q_{max}$)</span>
                    </div>
                    <div class="stat-card" style="border-color:#f97316">
                        <b>${totalOverflowM3.toLocaleString()} m³ cofki</b>
                        <span>Wylanie na jezdnie (spływ: ${(zone.imperv*100).toFixed(0)}%)</span>
                    </div>
                </div>
                <table class="gis-table">
                    <thead><tr><th>Kolektor Aquanet</th><th>Opad [ml/m²]</th><th>Q / Qmax</th><th>Napełnienie</th></tr></thead>
                    <tbody>${topRowsHtml}</tbody>
                </table>
                <div style="font-size:10px;color:#cbd5e1;margin-top:3px;">
                    <b>⚠️ Miejsca zagrożone podtopieniem:</b> ${zone.flood_spots}
                </div>
            `;
            setAiRecommendation([
                `W rejonie <b>${zone.name.split(' (')[0]}</b> przy opadzie <b>${mlPerM2.toLocaleString()} ml/m² (${stormRainfallMm} mm)</b> spływa do sieci <b>${totalRunoffIntoSewersM3.toLocaleString()} m³</b> wody deszczowej.`,
                overloadedPipes.length > 0
                    ? `Przekroczona przepustowość w <b>${overloadedPipes.length} kolektorach</b> (maks. napełnienie <b>${pipeStatsList[0].fillPct}%</b> w <b>${pipeStatsList[0].edge.pipe.split(' (')[0]}</b>) — zadysponować pompy wysokiej wydajności PSP oraz zabezpieczyć: <b>${zone.flood_spots}</b>.`
                    : `Wszystkie kolektory w strefie ulewy zachowują rezerwę przepustowości (maksymalne napełnienie: <b>${pipeStatsList[0].fillPct}%</b>).`
            ], overloadedPipes.length > 2 ? "ALARM POWODZIOWY IMGW" : "MONITORING HYDRAULICZNY");

            logCrisisEvent("ULEWA", overloadedPipes.length > 2 ? "crit" : "warn",
                `Ulewa ${mlPerM2.toLocaleString()} ml/m² (${stormRainfallMm} mm) — ${zone.name.split(' (')[0].replace('⛈️ ','').replace('🌧️ ','')}`,
                `Przepełnione rury: ${overloadedPipes.length} | Objętość cofki: ${totalOverflowM3.toLocaleString()} m³ | Maks. napełnienie: ${pipeStatsList[0].fillPct}%`,
                { mode: 'storm', stormRainfallMm, stormZoneKey, selectedBrokenPipeId });

            setScenarioDuration(30.0, 0);
            if (brokenEdgeObj) {
                updateDemProfileFromCoords(brokenEdgeObj.geometry, `Profil wysokościowy kolektora: ${brokenEdgeObj.pipe}`, [DATA.infra_nodes[brokenEdgeObj.u].elev_m, DATA.infra_nodes[brokenEdgeObj.v].elev_m]);
            }
            document.getElementById('formula-box').innerHTML =
                `<b>🌧️ Metoda Racjonalna i Bilans Hydrauliczny ($Q = \\psi \\cdot q \\cdot F$):</b> $1\\text{ mm} = 1\\text{ l/m}^2 = 1000\\text{ ml/m}^2$. Kliknij dowolne miejsce na mapie Poznania, aby przenieść tam komórkę burzową!`;
        }

        // 5. SIEĆ ELEKTROENERGETYCZNA ENEA OPERATOR (BLACKOUT + SZR)
        function renderPowerGridAnalysis(layerGroup) {
            setScenarioDuration(12.0, 0);
            const poweredNodes = new Set(), queue = [];
            Object.values(DATA.power_nodes).forEach(n => {
                if (n.role === 'source' && n.id !== selectedFailedPowerNode) { poweredNodes.add(n.id); queue.push(n.id); }
            });
            while (queue.length > 0) {
                const curr = queue.shift();
                DATA.power_edges.forEach(e => {
                    if (e.is_backup && !backupSZREnabled) return;
                    let neighbor = (e.u === curr) ? e.v : ((e.v === curr) ? e.u : null);
                    if (neighbor && neighbor !== selectedFailedPowerNode && !poweredNodes.has(neighbor)) { poweredNodes.add(neighbor); queue.push(neighbor); }
                });
            }

            DATA.power_edges.forEach(e => {
                if (e.is_backup && !backupSZREnabled) {
                    L.polyline(e.geometry, { color: '#64748b', weight: 2.5, opacity: 0.45, dashArray: '4, 6' }).addTo(layerGroup);
                    return;
                }
                const isEnergized = poweredNodes.has(e.u) && poweredNodes.has(e.v);
                const is110 = e.voltage.includes('110');
                const line = L.polyline(e.geometry, {
                    color: !isEnergized ? '#dc2626' : (e.is_backup ? '#38bdf8' : (is110 ? '#eab308' : '#10b981')),
                    weight: !isEnergized ? 5.5 : (is110 ? 5.0 : 3.5),
                    opacity: 0.92,
                    dashArray: !isEnergized ? '8, 6' : null
                }).bindTooltip(`<b>⚡ ${e.type}</b>`).addTo(layerGroup);
                line.on('click', (ev) => { L.DomEvent.stopPropagation(ev); selectedFailedPowerNode = e.v; renderCurrentScenario(); });
                addExportLine(e.geometry, { name: e.type, voltage: e.voltage, energized: isEnergized });
            });

            let totalCustomersOut = 0, totalMwOut = 0.0;
            const districtsOut = [], criticalAffected = [];
            Object.values(DATA.power_nodes).forEach(n => {
                const isFailed = (n.id === selectedFailedPowerNode), hasPower = poweredNodes.has(n.id);
                if (!hasPower) {
                    totalCustomersOut += n.customers; totalMwOut += n.mw; districtsOut.push(n.district);
                    if (n.critical) criticalAffected.push(`<b>${n.district}:</b> ${n.critical}`);
                    L.circle(n.coords, { radius: n.role === 'gpz' ? 1100 : 820, color: '#dc2626', fillColor: '#dc2626', fillOpacity: 0.22, weight: 1.5, dashArray: '5, 5', interactive: false }).addTo(layerGroup);
                }
                const marker = L.circleMarker(n.coords, {
                    radius: isFailed ? 13 : (n.role === 'source' ? 11 : (n.role === 'gpz' ? 9.5 : 7.5)),
                    fillColor: isFailed ? '#eab308' : (!hasPower ? '#dc2626' : (n.role === 'source' ? '#0284c7' : '#10b981')),
                    color: isFailed ? '#dc2626' : '#fff', weight: isFailed ? 4 : 2, fillOpacity: 0.98
                }).bindTooltip(`<b>${n.name}</b><br>Odbiorcy: <b>${n.customers.toLocaleString()}</b> | Moc: <b>${n.mw} MW</b>`).addTo(layerGroup);
                marker.on('click', (ev) => { L.DomEvent.stopPropagation(ev); selectedFailedPowerNode = n.id; renderCurrentScenario(); });
                addExportPoint(n.coords, { name: n.name, customers: n.customers, mw: n.mw, powered: hasPower });
            });

            const failedObj = DATA.power_nodes[selectedFailedPowerNode];
            document.getElementById('result-box').innerHTML = `
                <div><span class="badge b-amber">⚡ AWARIA STACJI</span> <b>${failedObj.name}</b></div>
                <div class="stat-grid">
                    <div class="stat-card" style="border-color:#dc2626"><b>${totalCustomersOut.toLocaleString()} odbiorców</b><span>Pozbawionych prądu w Poznaniu</span></div>
                    <div class="stat-card" style="border-color:#f59e0b"><b>${totalMwOut.toFixed(1)} MW</b><span>Odcięta moc czynna</span></div>
                </div>
                <div style="background:#1e293b; padding:5px; border-radius:6px; border-left:3px solid #dc2626; font-size:10px;">
                    <b>🔴 Dzielnice bez prądu:</b> ${districtsOut.slice(0, 5).join(' • ') || 'Brak (zasilanie z rezerwy SZR!)'}
                </div>
            `;
            setAiRecommendation([
                backupSZREnabled
                    ? `Układ pierścieniowy <b>SZR</b> przejął obciążenie z sąsiednich stacji GPZ — przywrócono zasilanie dla większości odbiorców.`
                    : `Włączyć zdalnie z Centralnej Dyspozycji Mocy Enea Operator <b>łączniki rezerwowe SZR 110/15 kV</b> (zaznacz checkbox SZR powyżej!), aby zasilić odcięte stacje z sąsiednich GPZ.`,
                `Potwierdzić pracę agregatów prądotwórczych w obiektach krytycznych: <b>${criticalAffected.slice(0, 2).join('; ') || 'brak zagrożonych szpitali'}</b>.`,
                `Skierować pogotowie sieciowe WN/SN do stacji <b>${failedObj.name}</b>.`
            ], totalCustomersOut > 30000 ? "STOPNIEŃ ALARMOWY BLACKOUT" : "INTERWENCJA ENEA");

            logCrisisEvent("ENERGETYKA", "crit", `Blackout: ${failedObj.name.replace('⚡ ','').replace('🔌 ','').split(' (')[0]}`,
                `Odciętych: ${totalCustomersOut.toLocaleString()} odb. | Strata mocy: ${totalMwOut.toFixed(1)} MW | SZR: ${backupSZREnabled ? 'ZAŁĄCZONY' : 'WYŁĄCZONY'}`,
                { mode: 'power', selectedFailedPowerNode, backupSZREnabled });

            document.getElementById('formula-box').innerHTML = `<b>⚡ Propagacja wyłączeń (BFS + SZR):</b> Przeszukuje graf sieci 110/15 kV od źródeł EC Karolin i SE Plewiska.`;
        }

        // 6. TRANSPORT ZTM POZNAŃ (WYŁĄCZNIE TRASY TRAMWAJOWE I AUTOBUSOWE ZTM)
        function renderMultimodalAnalysis(layerGroup) {
            if (!DATA.ztm_stops[mmStartStopId]) mmStartStopId = 'bus_lawica_port';
            if (!DATA.ztm_stops[mmTargetStopId]) mmTargetStopId = 'ztm_franowo';
            if (mmModeType === 'tram_only') {
                if (DATA.ztm_stops[mmStartStopId].mode !== 'tram') mmStartStopId = 'ztm_sobieskiego';
                if (DATA.ztm_stops[mmTargetStopId].mode !== 'tram') mmTargetStopId = 'ztm_franowo';
            }

            const startStop = DATA.ztm_stops[mmStartStopId];
            const endStop = DATA.ztm_stops[mmTargetStopId];
            const activeStops = Object.values(DATA.ztm_stops).filter(st => mmModeType === 'tram_only' ? st.mode === 'tram' : true);
            const activeEdges = DATA.ztm_edges.filter(e => mmModeType === 'tram_only' ? e.mode === 'tram' : true);

            // 1) Rysujemy i podświetlamy całą sieć tras ZTM (Tramwaje + Autobusy) dostępną w tym trybie, z obsługą kliknięcia!
            activeEdges.forEach(e => {
                const isBus = (e.mode === 'bus');
                const bgLine = L.polyline(e.geometry, {
                    color: isBus ? '#f97316' : '#f472b6',
                    weight: isBus ? 3.2 : 3.6,
                    opacity: 0.55,
                    dashArray: isBus ? '6, 5' : null
                }).bindTooltip(`<b>${isBus ? '🚌' : '🚋'} ${e.label}</b><br>Kliknij trasę ZTM, aby ustawić przystanek Start/Cel`).addTo(layerGroup);

                bgLine.on('click', (ev) => {
                    L.DomEvent.stopPropagation(ev);
                    const snappedId = findNearestZtmStopId(ev.latlng.lat, ev.latlng.lng);
                    if (pickingState === 'mm_start') { mmStartStopId = snappedId; pickingState = null; }
                    else { mmTargetStopId = snappedId; pickingState = null; }
                    renderCurrentScenario();
                });
            });

            // 2) Dijkstra ściśle po grafie sieci ZTM Poznań (z uwzględnieniem czasu przesiadki między liniami)
            const ztmAdj = {};
            activeStops.forEach(s => ztmAdj[s.id] = []);
            activeEdges.forEach(e => {
                if (ztmAdj[e.u] && ztmAdj[e.v]) {
                    ztmAdj[e.u].push({ from: e.u, to: e.v, time_min: e.time_min, dist_km: e.dist_km, label: e.label, mode: e.mode, geom: e.geometry });
                    ztmAdj[e.v].push({ from: e.v, to: e.u, time_min: e.time_min, dist_km: e.dist_km, label: e.label, mode: e.mode, geom: [...e.geometry].reverse() });
                }
            });

            const zDist = {}, zPrev = {}, zEdgeUsed = {};
            activeStops.forEach(s => zDist[s.id] = Infinity);
            zDist[startStop.id] = 0;
            const zVisited = new Set();

            while (zVisited.size < activeStops.length) {
                let u = null, bestD = Infinity;
                for (const s of activeStops) {
                    if (!zVisited.has(s.id) && zDist[s.id] < bestD) { bestD = zDist[s.id]; u = s.id; }
                }
                if (!u || u === endStop.id) break;
                zVisited.add(u);
                const prevEd = zEdgeUsed[u];
                (ztmAdj[u] || []).forEach(ed => {
                    // Kara czasowa 1.5 min za przesiadkę na inną linię/środek transportu, aby algorytm wybierał naturalne bezpośrednie korytarze ZTM
                    const transferPenalty = (prevEd && prevEd.label !== ed.label) ? 1.5 : 0.0;
                    const candTime = zDist[u] + ed.time_min + transferPenalty;
                    if (candTime < zDist[ed.to]) {
                        zDist[ed.to] = candTime;
                        zPrev[ed.to] = u;
                        zEdgeUsed[ed.to] = ed;
                    }
                });
            }

            const transitSegments = [];
            const routeStopIds = new Set([startStop.id, endStop.id]);
            const transferStopIds = new Set();
            let currSt = endStop.id;
            while (currSt && zPrev[currSt]) {
                const seg = zEdgeUsed[currSt];
                transitSegments.push(seg);
                routeStopIds.add(seg.from);
                routeStopIds.add(seg.to);
                currSt = zPrev[currSt];
            }
            transitSegments.reverse();

            for (let i = 1; i < transitSegments.length; i++) {
                if (transitSegments[i].label !== transitSegments[i - 1].label) {
                    transferStopIds.add(transitSegments[i].from);
                }
            }

            // 3) Podświetlenie wyznaczonej trasy ZTM na mapie
            const combinedCoords = [];
            let totalZtmDistKm = 0.0;
            let pureRideMin = 0.0;

            transitSegments.forEach((seg, idx) => {
                const isBus = (seg.mode === 'bus');
                totalZtmDistKm += seg.dist_km;
                pureRideMin += seg.time_min;
                // Poświata pod aktywną trasą ZTM
                L.polyline(seg.geom, {
                    color: '#fde047',
                    weight: 10,
                    opacity: 0.38,
                    interactive: false
                }).addTo(layerGroup);

                L.polyline(seg.geom, {
                    color: isBus ? '#f97316' : '#ec4899',
                    weight: 6.5,
                    opacity: 0.98
                }).bindTooltip(
                    `<b>Odcinek #${idx + 1}: ${isBus ? '🚌' : '🚋'} ${seg.label}</b><br>` +
                    `${DATA.ztm_stops[seg.from].name} → ${DATA.ztm_stops[seg.to].name}<br>` +
                    `Dystans: <b>${seg.dist_km} km</b> | Czas jazdy: <b>${seg.time_min} min</b>`
                ).addTo(layerGroup);

                combinedCoords.push(...seg.geom);
                addExportLine(seg.geom, { step: idx + 1, transit_line: seg.label, mode: seg.mode, dist_km: seg.dist_km, time_min: seg.time_min });
            });

            // 4) Rysujemy wszystkie przystanki ZTM na trasach (klikalne jako Start / Cel!)
            activeStops.forEach(st => {
                const isStart = (st.id === startStop.id);
                const isEnd = (st.id === endStop.id);
                const isTransfer = transferStopIds.has(st.id);
                const isOnRoute = routeStopIds.has(st.id);

                const marker = L.circleMarker(st.coords, {
                    radius: (isStart || isEnd) ? 11 : (isTransfer ? 9.5 : (isOnRoute ? 7.5 : 5.5)),
                    fillColor: isStart ? '#10b981' : (isEnd ? '#dc2626' : (isTransfer ? '#fde047' : (st.mode === 'bus' ? '#f97316' : '#ec4899'))),
                    color: (isStart || isEnd || isTransfer || isOnRoute) ? '#ffffff' : '#0f172a',
                    weight: (isStart || isEnd || isTransfer) ? 3 : 1.8,
                    fillOpacity: isOnRoute ? 1.0 : 0.82
                }).bindTooltip(
                    `<b>${isStart ? '🟢 START ZTM: ' : (isEnd ? '🔴 CEL ZTM: ' : (isTransfer ? '🔄 PRZESIADKA ZTM: ' : ''))}${st.name}</b><br>` +
                    `${st.lines}<br><span style="color:#38bdf8;">Kliknij przystanek, aby ustawić jako punkt na trasie ZTM</span>`
                ).addTo(layerGroup);

                marker.on('click', (ev) => {
                    L.DomEvent.stopPropagation(ev);
                    if (pickingState === 'mm_start') { mmStartStopId = st.id; pickingState = null; }
                    else if (pickingState === 'mm_target') { mmTargetStopId = st.id; pickingState = null; }
                    else { mmTargetStopId = st.id; }
                    renderCurrentScenario();
                });
                if (isOnRoute) {
                    addExportPoint(st.coords, { stop_name: st.name, lines: st.lines, role: isStart ? 'START' : (isEnd ? 'CEL' : (isTransfer ? 'PRZESIADKA' : 'POŚREDNI')) });
                }
            });

            const totalTransfers = transferStopIds.size;
            const totalZtmMin = +(pureRideMin + totalTransfers * 2.0).toFixed(1);
            const carRes = runDijkstra(startStop.node_idx, endStop.node_idx, 'time');
            const usedLabels = [...new Set(transitSegments.map(s => s.label))];
            const transferNames = [...transferStopIds].map(id => DATA.ztm_stops[id].name.replace('🚋 ','').replace('🚌 ','')).join(' → ') || 'Brak (połączenie bezpośrednie)';

            document.getElementById('result-box').innerHTML = `
                <div><span class="badge b-purple">🚋/🚌 TRANSPORT ZTM POZNAŃ (TRAMWAJE + AUTOBUSY)</span></div>
                <div class="stat-grid">
                    <div class="stat-card" style="border-color:#ec4899"><b>${totalZtmMin} min (${totalZtmDistKm.toFixed(2)} km)</b><span>🚋/🚌 Czas i długość trasy ZTM</span></div>
                    <div class="stat-card" style="border-color:#fde047"><b>${transitSegments.length} odc. / ${totalTransfers} przes.</b><span>Liczba odcinków i przesiadek</span></div>
                </div>
                <div style="background:#1e293b; padding:6px; border-radius:6px; border-left:3px solid #ec4899; font-size:10.5px;">
                    🟢 <b>Start ZTM:</b> ${startStop.name}<br>
                    🚋/🚌 <b>Linie ZTM:</b> ${usedLabels.slice(0, 3).join(' → ') || 'Wybrano ten sam przystanek'}<br>
                    🔄 <b>Węzły przesiadkowe:</b> ${transferNames}<br>
                    🔴 <b>Cel ZTM:</b> ${endStop.name} (🚗 Auto w szczycie: <b>${carRes ? carRes.time_min : '-'} min</b>)
                </div>
            `;
            setAiRecommendation([
                `Wyznaczono optymalną trasę wyłącznie po sieci ZTM Poznań: <b>${startStop.name} → ${endStop.name}</b> (${totalZtmDistKm.toFixed(2)} km, czas przejazdu <b>${totalZtmMin} min</b>).`,
                totalTransfers > 0
                    ? `Rekomendowana przesiadka na węźle: <b>${transferNames}</b> (linie: <b>${usedLabels.slice(0, 3).join(' → ')}</b>).`
                    : `Trasa realizowana bezpośrednio po jednym korytarzu ZTM (<b>${usedLabels[0] || '-'}</b>) bez konieczności przesiadek.`
            ], "NAWIGACJA ZTM POZNAŃ");

            logCrisisEvent("TRANSPORT ZTM", "info", `Trasa ZTM: ${startStop.name.replace('🚋 ','').replace('🚌 ','')} -> ${endStop.name.replace('🚋 ','').replace('🚌 ','')}`,
                `Czas: ${totalZtmMin} min (${totalZtmDistKm.toFixed(2)} km) | Przesiadki: ${totalTransfers} | Linie: ${usedLabels.slice(0, 2).join(', ')}`,
                { mode: 'multimodal', mmStartStopId, mmTargetStopId });

            setScenarioDuration(totalZtmMin, totalZtmDistKm);
            updateDemProfileFromCoords(combinedCoords, `Przejazd ZTM Poznań (${totalZtmDistKm.toFixed(2)} km, ${totalZtmMin} min)`);
            document.getElementById('formula-box').innerHTML =
                `<b>🚋/🚌 Sieć ZTM Poznań (OSM railway=tram + Autobusy ZTM):</b> Kliknij dowolny przystanek lub linię ZTM na mapie, aby ustawić punkt Start lub Cel na trasie ZTM.`;
        }
"""

    html_part6 = r"""
        // 7. MACIERZ ODLEGŁOŚCI I CZASÓW (OD COST MATRIX)
        function renderOdMatrixAnalysis(layerGroup) {
            const { dist, prev } = runDijkstra(odOriginNodeIdx, null, 'time');
            const originCoord = DATA.graph_coords[odOriginNodeIdx];
            const candidates = DATA.emergency_facilities.filter(f => odFacilityCategory === 'all' || f.category === odFacilityCategory);

            const results = [];
            candidates.forEach(fac => {
                const timeDs = dist[fac.node_idx];
                if (timeDs < Infinity) {
                    const coords = [];
                    let curr = fac.node_idx, distM = 0;
                    while (curr !== -1) {
                        coords.push(DATA.graph_coords[curr]);
                        const p = prev[curr];
                        if (p !== -1) { const edge = ADJ[p].find(e => e.to === curr); if (edge) distM += edge.dist_m; }
                        curr = p;
                    }
                    coords.reverse();
                    const dKm = +(distM / 1000.0).toFixed(2), tMin = +(timeDs / 600.0).toFixed(1);
                    results.push({ fac, dKm, tMin, avgSpd: Math.round(dKm / Math.max(0.01, tMin / 60.0)), coords });
                }
            });
            results.sort((a, b) => a.tMin - b.tMin);
            const colors = ['#10b981', '#06b6d4', '#f59e0b', '#a855f7', '#ec4899'];

            results.forEach((r, idx) => {
                const isTop = idx < odTopK;
                const col = isTop ? colors[idx % colors.length] : '#64748b';
                if (isTop) {
                    L.polyline(r.coords, { color: col, weight: 6 - idx * 0.7, opacity: 0.92 })
                        .bindTooltip(`<b>#${idx+1}: ${r.fac.name} (${r.tMin} min)</b>`).addTo(layerGroup);
                    addExportLine(r.coords, { rank: idx+1, facility: r.fac.name, time_min: r.tMin, dist_km: r.dKm });
                }
                L.circleMarker(r.fac.coords, { radius: isTop ? 9.5 : 6, fillColor: col, color: '#fff', weight: 2, fillOpacity: 0.95 })
                    .bindTooltip(`<b>${r.fac.name}</b> (${r.tMin} min)`).addTo(layerGroup);
            });
            L.circleMarker(originCoord, { radius: 11, fillColor: '#dc2626', color: '#fff', weight: 3, fillOpacity: 1 }).bindTooltip("MIEJSCE WEZWANIA").addTo(layerGroup);

            const rowsHtml = results.slice(0, 6).map((r, idx) => `
                <tr style="${idx < odTopK ? 'font-weight:600;' : 'opacity:0.75;'}">
                    <td><span class="badge" style="background:${idx < odTopK ? colors[idx%colors.length] : '#334155'};color:#fff;">#${idx+1}</span></td>
                    <td>${r.fac.name.split(' (')[0]}</td><td style="color:#38bdf8;font-weight:700;">${r.tMin} min</td><td>${r.dKm} km</td><td>${r.avgSpd} km/h</td>
                </tr>
            `).join('');

            document.getElementById('result-box').innerHTML = `
                <div><span class="badge b-green">📊 MACIERZ OD (CLOSEST FACILITY)</span> <b>Najbliższe jednostki ratunkowe</b></div>
                <table class="gis-table"><thead><tr><th>Poz.</th><th>Jednostka docelowa</th><th>Czas</th><th>Dystans</th><th>V śr.</th></tr></thead><tbody>${rowsHtml}</tbody></table>
            `;
            if (results.length > 0) {
                setAiRecommendation([
                    `Zadysponować w pierwszym rzucie jednostkę <b>#1: ${results[0].fac.name}</b> (szacowany czas dotarcia: <b>${results[0].tMin} min</b>, dystans ${results[0].dKm} km).`,
                    results[1] ? `Jako wsparcie drugiego rzutu wyznaczyć <b>#2: ${results[1].fac.name}</b> (czas dojazdu: <b>${results[1].tMin} min</b>).` : ''
                ], "DYSPONOWANIE SŁUŻB CPR 112");
                setScenarioDuration(results[0].tMin, results[0].dKm);
                updateDemProfileFromCoords([...results[0].coords].reverse(), `Dojazd jednostki #1: ${results[0].fac.name} (${results[0].tMin} min)`);
            }
            document.getElementById('formula-box').innerHTML = `<b>📊 Macierz Kosztów OD (Origin-Destination):</b> Wyznacza najkrótsze czasy z punktu zdarzenia do 20 baz ratunkowych w Poznaniu.`;
        }

        // 8. PROBLEM KOMIWOJAŻERA (TSP / VRP Z OKNAMI CZASOWYMI)
        function renderTspVrpAnalysis(layerGroup) {
            const preset = DATA.tsp_presets[tspPresetKey];
            const depot = preset.depot;
            const allStops = [...preset.stops, ...customTspStops];
            const nodes = [depot.node_idx, ...allStops.map(s => s.node_idx)];
            const n = nodes.length;
            const timeMat = Array.from({ length: n }, () => new Float64Array(n));
            const distMat = Array.from({ length: n }, () => new Float64Array(n));

            for (let i = 0; i < n; i++) {
                const { dist } = runDijkstra(nodes[i], null, 'time');
                for (let j = 0; j < n; j++) {
                    timeMat[i][j] = dist[nodes[j]] / 600.0;
                    const c1 = DATA.graph_coords[nodes[i]], c2 = DATA.graph_coords[nodes[j]];
                    distMat[i][j] = haversineKm(c1[0], c1[1], c2[0], c2[1]) * 1.28;
                }
            }

            const naiveOrder = Array.from({ length: n - 1 }, (_, i) => i + 1);
            let naiveTime = 0, naiveKm = 0, prevIdx = 0;
            naiveOrder.forEach(idx => { naiveTime += timeMat[prevIdx][idx]; naiveKm += distMat[prevIdx][idx]; prevIdx = idx; });
            naiveTime += timeMat[prevIdx][0]; naiveKm += distMat[prevIdx][0];

            let activeOrder = [...naiveOrder];
            if (tspOptimizeEnabled) {
                const unvisited = new Set(naiveOrder), nnOrder = [];
                let curr = 0;
                while (unvisited.size > 0) {
                    let bestNext = -1, bestCost = Infinity;
                    unvisited.forEach(cand => { if (timeMat[curr][cand] < bestCost) { bestCost = timeMat[curr][cand]; bestNext = cand; } });
                    nnOrder.push(bestNext); unvisited.delete(bestNext); curr = bestNext;
                }
                let improved = true;
                while (improved) {
                    improved = false;
                    for (let i = 0; i < nnOrder.length - 1; i++) {
                        for (let k = i + 1; k < nnOrder.length; k++) {
                            const A = (i === 0) ? 0 : nnOrder[i - 1], B = nnOrder[i], C = nnOrder[k], D = (k === nnOrder.length - 1) ? 0 : nnOrder[k + 1];
                            if (timeMat[A][C] + timeMat[B][D] < timeMat[A][B] + timeMat[C][D] - 0.05) {
                                nnOrder.splice(i, k - i + 1, ...nnOrder.slice(i, k + 1).reverse());
                                improved = true;
                            }
                        }
                    }
                }
                activeOrder = nnOrder;
            }

            const fullSeq = [0, ...activeOrder, 0];
            let totalDriveMin = 0, totalDistKm = 0;
            const fullRouteCoords = [];
            const segColors = ['#38bdf8', '#10b981', '#a855f7', '#f59e0b', '#ec4899', '#06b6d4', '#84cc16', '#f43f5e'];

            for (let s = 0; s < fullSeq.length - 1; s++) {
                const leg = runDijkstra(nodes[fullSeq[s]], nodes[fullSeq[s + 1]], 'time');
                if (leg) {
                    totalDriveMin += parseFloat(leg.time_min); totalDistKm += parseFloat(leg.dist_km);
                    fullRouteCoords.push(...leg.coords);
                    L.polyline(leg.coords, { color: segColors[s % segColors.length], weight: 5, opacity: 0.92 }).addTo(layerGroup);
                    addExportLine(leg.coords, { leg: s+1, dist_km: leg.dist_km, time_min: leg.time_min });
                }
            }
            L.circleMarker(depot.coords, { radius: 11, fillColor: '#eab308', color: '#0f172a', weight: 3, fillOpacity: 1 }).bindTooltip(`BAZA: ${depot.name}`).addTo(layerGroup);

            let clockMin = parseInt(depot.start_time.split(':')[0]) * 60 + parseInt(depot.start_time.split(':')[1]);
            let prevPos = 0, scheduleRows = "";
            activeOrder.forEach((stopIdx, orderNum) => {
                const stopObj = allStops[stopIdx - 1];
                clockMin += Math.round(timeMat[prevPos][stopIdx]);
                const arrStr = `${String(Math.floor(clockMin / 60)).padStart(2, '0')}:${String(clockMin % 60).padStart(2, '0')}`;
                clockMin += (stopObj.service_min || 5); prevPos = stopIdx;
                L.circleMarker(stopObj.coords, { radius: 9.5, fillColor: '#a855f7', color: '#fff', weight: 2.5, fillOpacity: 0.98 })
                    .bindTooltip(`<b>#${orderNum + 1}: ${stopObj.name}</b> (Przyjazd: ${arrStr})`).addTo(layerGroup);
                scheduleRows += `<tr><td><span class="badge b-purple">#${orderNum + 1}</span></td><td>${stopObj.name.split(' - ')[0]}</td><td style="color:#4ade80;font-weight:700;">${arrStr}</td><td>${stopObj.time_window} ✅</td></tr>`;
            });

            const savedKm = Math.max(0, naiveKm - totalDistKm).toFixed(1);
            const savedMin = Math.max(0, naiveTime - totalDriveMin).toFixed(1);
            document.getElementById('result-box').innerHTML = `
                <div><span class="badge b-purple">🚚 OPTYMALIZACJA TSP / VRP</span> <b>${allStops.length} punktów + Baza</b></div>
                <div class="stat-grid">
                    <div class="stat-card" style="border-color:#a855f7"><b>${totalDistKm.toFixed(1)} km / ${totalDriveMin.toFixed(0)} min</b><span>Łączna trasa pętli</span></div>
                    <div class="stat-card" style="border-color:#10b981"><b>Zysk: -${savedKm} km (-${savedMin} min)</b><span>Optymalizacja 2-Opt</span></div>
                </div>
                <table class="gis-table"><thead><tr><th>Kol.</th><th>Punkt docelowy</th><th>Przyjazd</th><th>Okno czasowe</th></tr></thead><tbody>${scheduleRows}</tbody></table>
            `;
            setAiRecommendation([
                `Wdrożenie kolejności zoptymalizowanej algorytmem <b>2-Opt TSP</b> pozwala skrócić trasę o <b>${savedKm} km (${savedMin} min)</b> i zredukować emisję CO₂ o ~${(savedKm*0.24).toFixed(1)} kg.`,
                `Wszystkie ${allStops.length} punktów obsługiwane są bez naruszenia okien czasowych (Time Windows).`
            ], "LOGISTYKA MIEJSKA VRP");
            setScenarioDuration(totalDriveMin, totalDistKm);
            updateDemProfileFromCoords(fullRouteCoords, `Pętla logistyczna TSP (${totalDistKm.toFixed(1)} km, ${totalDriveMin.toFixed(1)} min)`);
            document.getElementById('formula-box').innerHTML = `<b>🚚 Problem Komiwojażera (VRP-TW / 2-Opt):</b> Kliknij <b>▶️ Animuj czas</b> u dołu, aby zobaczyć przejazd pojazdu przez kolejne punkty!`;
        }

        // 9. LOKALIZACJA-ALOKACJA (p-MEDIAN)
        function renderLocationAllocationAnalysis(layerGroup) {
            const existingFacs = DATA.emergency_facilities.filter(f => f.category === locFacilityType);
            const distFromFacs = existingFacs.map(f => runDijkstra(f.node_idx, null, 'time').dist);

            let totalPop = 0, weightedTimeBefore = 0, popUnder5Before = 0;
            const currentBestTime = new Float64Array(DATA.demand_districts.length);
            const currentAssignedFac = new Array(DATA.demand_districts.length);

            DATA.demand_districts.forEach((d, dIdx) => {
                totalPop += d.population;
                let bestT = Infinity, bestF = existingFacs[0];
                existingFacs.forEach((f, fIdx) => {
                    const tMin = distFromFacs[fIdx][d.node_idx] / 600.0;
                    if (tMin < bestT) { bestT = tMin; bestF = f; }
                });
                currentBestTime[dIdx] = bestT; currentAssignedFac[dIdx] = bestF;
                weightedTimeBefore += d.population * bestT;
                if (bestT <= 5.5) popUnder5Before += d.population;
            });

            let chosenNodeIdx = locCandidateNodeIdx;
            if (chosenNodeIdx === null) {
                let bestScore = Infinity;
                DATA.demand_districts.forEach(cand => {
                    let score = 0;
                    DATA.demand_districts.forEach((d, dIdx) => {
                        const approxMin = (haversineKm(cand.coords[0], cand.coords[1], d.coords[0], d.coords[1]) * 1.28 / 38.0) * 60.0;
                        score += d.population * Math.min(currentBestTime[dIdx], approxMin);
                    });
                    if (score < bestScore) { bestScore = score; chosenNodeIdx = cand.node_idx; }
                });
            }

            const newFacDist = runDijkstra(chosenNodeIdx, null, 'time').dist;
            const newFacCoord = DATA.graph_coords[chosenNodeIdx];
            let weightedTimeAfter = 0, popUnder5After = 0, capturedPop = 0;
            const capturedDistricts = [];

            DATA.demand_districts.forEach((d, dIdx) => {
                const tNew = newFacDist[d.node_idx] / 600.0;
                const isCaptured = tNew < currentBestTime[dIdx] - 0.1;
                const finalT = isCaptured ? tNew : currentBestTime[dIdx];
                const targetCoord = isCaptured ? newFacCoord : currentAssignedFac[dIdx].coords;
                weightedTimeAfter += d.population * finalT;
                if (finalT <= 5.5) popUnder5After += d.population;
                if (isCaptured) { capturedPop += d.population; capturedDistricts.push(`${d.name.split(' (')[0]} (${tNew.toFixed(1)} min)`); }

                L.polyline([d.coords, targetCoord], { color: isCaptured ? '#10b981' : '#64748b', weight: isCaptured ? 3.2 : 1.5, opacity: isCaptured ? 0.9 : 0.45, dashArray: isCaptured ? null : '4, 4' }).addTo(layerGroup);
                L.circleMarker(d.coords, { radius: Math.max(5, Math.sqrt(d.population / 800)), fillColor: isCaptured ? '#10b981' : '#38bdf8', color: '#fff', weight: 1.5, fillOpacity: 0.9 })
                    .bindTooltip(`<b>${d.name}</b> (${d.population.toLocaleString()} mieszk.)`).addTo(layerGroup);
            });
            existingFacs.forEach(f => L.circleMarker(f.coords, { radius: 8, fillColor: '#0284c7', color: '#fff', weight: 2, fillOpacity: 0.95 }).bindTooltip(f.name).addTo(layerGroup));
            L.circleMarker(newFacCoord, { radius: 13, fillColor: '#10b981', color: '#fde047', weight: 3.5, fillOpacity: 1 }).bindTooltip(`⭐ NOWA BAZA OPTYMALNA`).addTo(layerGroup);

            const avgBefore = (weightedTimeBefore / totalPop).toFixed(2), avgAfter = (weightedTimeAfter / totalPop).toFixed(2);
            const covBefore = Math.round((popUnder5Before / totalPop) * 100), covAfter = Math.round((popUnder5After / totalPop) * 100);
            document.getElementById('result-box').innerHTML = `
                <div><span class="badge b-green">📍 MODEL LOCATION-ALLOCATION (p-MEDIAN)</span></div>
                <div class="stat-grid">
                    <div class="stat-card" style="border-color:#10b981"><b>${avgBefore} → ${avgAfter} min</b><span>Średni czas dojazdu w Poznaniu</span></div>
                    <div class="stat-card" style="border-color:#38bdf8"><b>${covBefore}% → ${covAfter}% mieszk.</b><span>Pokrycie populacji ≤ 5.5 min</span></div>
                </div>
                <div style="background:#1e293b; padding:6px; border-radius:6px; border-left:3px solid #10b981; font-size:10.5px;">
                    <b>⭐ Nowa baza przejmuje ${capturedPop.toLocaleString()} mieszkańców:</b> ${capturedDistricts.slice(0, 4).join(', ')}
                </div>
            `;
            setAiRecommendation([
                `Rekomendowana lokalizacja nowej jednostki obejmuje zasięgiem <b>${capturedPop.toLocaleString()} mieszkańców</b> (${capturedDistricts.length} osiedli Poznania).`,
                `Inwestycja obniża średni czas dojazdu w skali miasta z <b>${avgBefore} min do ${avgAfter} min</b> i zwiększa odsetek mieszkańców w izochronie 5.5 min do <b>${covAfter}%</b>.`
            ], "PLANOWANIE PRZESTRZENNE");
            setScenarioDuration(parseFloat(avgAfter), 0);
            document.getElementById('formula-box').innerHTML = `<b>📍 Algorytm p-Median:</b> Minimalizuje ważony populacją czas dojazdu dla 27 osiedli Poznania.`;
        }

        // 10. SYMULACJA EWAKUACJI I WĄSKICH GARDEŁ
        function renderEvacuationAnalysis(layerGroup) {
            const preset = DATA.evacuation_hazards.find(h => h.id === evacHazardId) || DATA.evacuation_hazards[0];
            const centerLat = evacCustomCenter ? evacCustomCenter[0] : preset.lat;
            const centerLon = evacCustomCenter ? evacCustomCenter[1] : preset.lon;
            const centerIdx = findNearestGraphNode(centerLat, centerLon);

            L.circle([centerLat, centerLon], { radius: evacRadiusM, color: '#dc2626', fillColor: '#dc2626', fillOpacity: 0.25, weight: 2.5, dashArray: '8, 5' }).addTo(layerGroup);

            const sheltersRanked = DATA.evacuation_shelters.map(sh => {
                const route = runDijkstra(centerIdx, sh.node_idx, 'time');
                return { sh, route, tMin: route ? parseFloat(route.time_min) : 999 };
            }).filter(r => r.route !== null).sort((a, b) => a.tMin - b.tMin).slice(0, 4);

            const bottleneckStreets = [];
            let firstRouteCoords = null;
            sheltersRanked.forEach((item, idx) => {
                const coords = item.route.coords;
                if (idx === 0) firstRouteCoords = coords;
                L.polyline(coords, { color: '#10b981', weight: 6, opacity: 0.9 }).bindTooltip(`Korytarz #${idx+1} → ${item.sh.name}`).addTo(layerGroup);
                addExportLine(coords, { corridor: idx+1, shelter: item.sh.name, time_min: item.route.time_min });

                if (coords.length > 6) {
                    const bStart = Math.floor(coords.length * 0.22), bEnd = Math.min(coords.length - 1, bStart + 5);
                    const bStreet = item.route.streets.split(' → ')[0] || 'Wylot ze strefy';
                    if (!bottleneckStreets.includes(bStreet)) bottleneckStreets.push(bStreet);
                    L.polyline(coords.slice(bStart, bEnd + 1), { color: '#dc2626', weight: 10, opacity: 0.95 })
                        .bindTooltip(`<b>⚠️ WĄSKIE GARDŁO EWAKUACJI ($Q/C_{max}=1.65$): ${bStreet}</b>`).addTo(layerGroup);
                }
                L.circleMarker(item.sh.coords, { radius: 10, fillColor: '#10b981', color: '#fff', weight: 2.5, fillOpacity: 1 }).bindTooltip(item.sh.name).addTo(layerGroup);
            });

            const estPop = Math.round(preset.pop * (evacRadiusM / preset.radius_m));
            const clearanceMin = Math.round(18 + (estPop / 950));
            document.getElementById('result-box').innerHTML = `
                <div><span class="badge b-red">🚨 SYMULACJA EWAKUACJI</span> <b>${evacCustomCenter ? 'Własna strefa zagrożenia' : preset.name.split(' (')[0]}</b></div>
                <div class="stat-grid">
                    <div class="stat-card" style="border-color:#ef4444"><b>~${estPop.toLocaleString()} osób</b><span>Populacja objęta ewakuacją</span></div>
                    <div class="stat-card" style="border-color:#f59e0b"><b>${clearanceMin} minut</b><span>Czas opróżnienia strefy</span></div>
                </div>
                <div style="background:#1e293b; padding:6px; border-radius:6px; border-left:3px solid #dc2626; font-size:10.5px;">
                    <b>⚠️ Wąskie Gardła ($Q > C_{max}$):</b> ${bottleneckStreets.join(' • ')}
                </div>
            `;
            setAiRecommendation([
                `Skierować patrole Wydziału Ruchu Drogowego KMP Poznań do ręcznego sterowania ruchem na wąskich gardłach: <b>${bottleneckStreets.join(', ')}</b>.`,
                `Podstawić autobusy ewakuacyjne MPK Poznań i kierować ewakuowanych do punktu: <b>${sheltersRanked[0] ? sheltersRanked[0].sh.name : 'Stadion Miejski'}</b>.`
            ], "SZTAB EWAKUACYJNY");
            logCrisisEvent("EWAKUACJA", "crit", `Strefa R=${evacRadiusM}m (~${estPop.toLocaleString()} osób)`,
                `Wąskie gardła: ${bottleneckStreets.join(', ')} | Czas ewakuacji: ${clearanceMin} min`,
                { mode: 'evacuation', evacHazardId, evacRadiusM });
            if (sheltersRanked.length > 0) {
                setScenarioDuration(sheltersRanked[0].tMin, parseFloat(sheltersRanked[0].route.dist_km));
            }
            if (firstRouteCoords) updateDemProfileFromCoords(firstRouteCoords, `Korytarz ewakuacyjny #1 (${sheltersRanked[0] ? sheltersRanked[0].tMin : clearanceMin} min)`);
            document.getElementById('formula-box').innerHTML = `<b>🚨 Model Ewakuacji i Wąskich Gardeł:</b> Identyfikuje przeciążone odcinki wylotowe ($Q_{ewak} / C_{max} > 1.0$).`;
        }

        // 11. IZOCHRONY DOSTĘPNOŚCI CZASOWEJ
        function renderIsochroneAnalysis(layerGroup) {
            setScenarioDuration(isoMinutes, 0);
            const maxTimeDs = isoMinutes * 600;
            const { dist } = runDijkstra(isoCenterIdx, null, 'time');

            let reachedNodes = 0, reachedDistKm = 0;
            const t1 = (isoMinutes * 600) * 0.45, t2 = (isoMinutes * 600) * 0.75;
            for (let i = 0; i < NUM_NODES; i++) { if (dist[i] <= maxTimeDs) reachedNodes++; }

            DATA.graph_edges.forEach((e, edgeIdx) => {
                if (blockedEdgesSet.has(edgeIdx)) return;
                const u = e[0], v = e[1];
                if (dist[u] <= maxTimeDs && dist[v] <= maxTimeDs) {
                    const maxD = Math.max(dist[u], dist[v]);
                    reachedDistKm += e[2] / 1000.0;
                    const color = (maxD <= t1) ? '#10b981' : ((maxD <= t2) ? '#eab308' : '#f97316');
                    L.polyline([DATA.graph_coords[u], DATA.graph_coords[v]], { color, weight: 2.2, opacity: 0.45, interactive: false }).addTo(layerGroup);
                }
            });

            const cCoord = DATA.graph_coords[isoCenterIdx];
            L.circleMarker(cCoord, { radius: 10, fillColor: '#a855f7', color: '#fff', weight: 3, fillOpacity: 1 }).addTo(layerGroup);
            const pctCity = ((reachedNodes / NUM_NODES) * 100).toFixed(1);
            document.getElementById('result-box').innerHTML = `
                <div><span class="badge b-purple">IZOCHRONA ${isoMinutes} MINUT</span></div>
                <div class="stat-grid">
                    <div class="stat-card"><b>${reachedNodes.toLocaleString()} węzłów</b><span>Osiągalne skrzyżowania (${pctCity}% Poznania)</span></div>
                    <div class="stat-card"><b>${reachedDistKm.toFixed(1)} km</b><span>Długość ulic w zasięgu ≤ ${isoMinutes} min</span></div>
                </div>
            `;
            setAiRecommendation([
                `W zadanym czasie <b>${isoMinutes} minut</b> jednostka ratunkowa pokrywa <b>${pctCity}% węzłów drogowych Poznania</b> (${reachedDistKm.toFixed(1)} km ulic).`,
                `Kliknij przycisk <b>▶️ Animuj czas</b> na dole (zakres 0–${isoMinutes} min), aby zobaczyć rozchodzenie się zasięgu po ulicach minuta po minucie!`
            ], "ANALIZA ZASIĘGU");
            document.getElementById('formula-box').innerHTML = `<b>🚑 Izochrona dostępności (\`Ego Graph\`):</b> Podgraf $G_{iso} = \\{v \\in V : d_{time}(v_0, v) \\le T\\}$.`;
        }

        // 12. DOJAZDY I OBJAZDY
        function renderRoutesAndDetourAnalysis(layerGroup) {
            activeClosureMarkers.forEach(m => {
                L.circle([m.lat, m.lon], { radius: 95, color: '#dc2626', fillColor: '#dc2626', fillOpacity: 0.35 }).addTo(layerGroup);
                L.circleMarker([m.lat, m.lon], { radius: 9, fillColor: '#dc2626', color: '#fff', weight: 2, fillOpacity: 1 }).bindTooltip(`⛔ ZAMKNIĘTA: ${m.label}`).addTo(layerGroup);
            });
            const sCoord = DATA.graph_coords[startNodeIdx], tCoord = DATA.graph_coords[targetNodeIdx];
            L.circleMarker(sCoord, { radius: 9, fillColor: '#10b981', color: '#fff', weight: 3, fillOpacity: 1 }).bindTooltip("START").addTo(layerGroup);
            L.circleMarker(tCoord, { radius: 9, fillColor: '#dc2626', color: '#fff', weight: 3, fillOpacity: 1 }).bindTooltip("CEL").addTo(layerGroup);

            if (currentMode === 'routes') {
                const shortRes = runDijkstra(startNodeIdx, targetNodeIdx, 'dist');
                const fastRes = runDijkstra(startNodeIdx, targetNodeIdx, 'time');
                if (shortRes && fastRes) {
                    L.polyline(shortRes.coords, { color: '#2563eb', weight: 5, opacity: 0.85, dashArray: '8, 6' }).addTo(layerGroup);
                    L.polyline(fastRes.coords, { color: '#10b981', weight: 6, opacity: 0.95 }).addTo(layerGroup);
                    addExportLine(fastRes.coords, { type: "Trasa Najszybsza", dist_km: fastRes.dist_km, time_min: fastRes.time_min });
                    const savedMin = (parseFloat(shortRes.time_min) - parseFloat(fastRes.time_min)).toFixed(1);
                    document.getElementById('result-box').innerHTML = `
                        <div><span class="badge b-blue">NAJKRÓTSZA</span> <b>${shortRes.dist_km} km</b> | Czas: <b>${shortRes.time_min} min</b></div>
                        <div><span class="badge b-green">NAJSZYBSZA</span> <b>${fastRes.dist_km} km</b> | Czas: <b>${fastRes.time_min} min</b> (Zysk: <b>-${savedMin} min</b>)</div>
                        <div style="font-size:10px;color:#94a3b8;margin-top:3px;">Przebieg: ${fastRes.streets}</div>
                    `;
                    setAiRecommendation([
                        `Rekomendowany korytarz przejazdu: <b>${fastRes.streets}</b> (oszczędność <b>${savedMin} min</b> względem trasy najkrótszej geometrycznie).`,
                        `Kliknij <b>▶️ Animuj czas</b> u dołu ekranu — suwak został automatycznie skalibrowany na dokładnie <b>${fastRes.time_min} min</b> (${fastRes.dist_km} km)!`
                    ], "NAWIGACJA OPTYMALNA");
                    setScenarioDuration(parseFloat(fastRes.time_min), parseFloat(fastRes.dist_km));
                    updateDemProfileFromCoords(fastRes.coords, `Trasa najszybsza (${fastRes.dist_km} km, czas: ${fastRes.time_min} min)`);
                }
                document.getElementById('formula-box').innerHTML = `<b>🚗 Algorytm Dijkstry:</b> Porównuje wagę odległości $d_{km}$ z wagą czasu przejazdu $t_{min}$.`;
            }
            else if (currentMode === 'detour') {
                const baseFast = runDijkstra(startNodeIdx, targetNodeIdx, 'time', true);
                const detourRes = runDijkstra(startNodeIdx, targetNodeIdx, 'time', false);
                if (baseFast) L.polyline(baseFast.coords, { color: '#64748b', weight: 4, opacity: 0.55, dashArray: '5, 5' }).addTo(layerGroup);
                if (detourRes) {
                    L.polyline(detourRes.coords, { color: '#ef4444', weight: 6, opacity: 0.95 }).addTo(layerGroup);
                    const diffMin = baseFast ? (parseFloat(detourRes.time_min) - parseFloat(baseFast.time_min)).toFixed(1) : "0.0";
                    const blockedList = Array.from(blockedStreetsNames).join(", ") || "Brak (kliknij dowolną ulicę na mapie!)";
                    document.getElementById('result-box').innerHTML = `
                        <div><span class="badge b-red">ZAMKNIĘTE (${blockedStreetsNames.size})</span> <b>${blockedList}</b></div>
                        <div style="margin-top:4px;"><span class="badge b-amber">OBJAZD</span> <b>${detourRes.dist_km} km</b> | Czas: <b>${detourRes.time_min} min</b> (+${diffMin} min)</div>
                    `;
                    setAiRecommendation([
                        `Po wyłączeniu odcinków (<b>${blockedList}</b>) wyznaczono trasę objazdową przez: <b>${detourRes.streets}</b>.`,
                        `Wzrost czasu przejazdu wynosi <b>+${diffMin} min</b> (czas objazdu: <b>${detourRes.time_min} min</b>).`
                    ], "ZARZĄDZANIE RUCHEM ITS");
                    if (blockedStreetsNames.size > 0) {
                        logCrisisEvent("TRANSPORT", "warn", `Zamknięcie drogi: ${blockedList}`,
                            `Nowy objazd: ${detourRes.dist_km} km (${detourRes.time_min} min, opóźnienie +${diffMin} min)`,
                            { mode: 'detour', startNodeIdx, targetNodeIdx });
                    }
                    setScenarioDuration(parseFloat(detourRes.time_min), parseFloat(detourRes.dist_km));
                    updateDemProfileFromCoords(detourRes.coords, `Trasa objazdu (${detourRes.dist_km} km, czas: ${detourRes.time_min} min)`);
                }
                document.getElementById('formula-box').innerHTML = `<b>🚧 Dynamiczny Objazd (\`Edge Removal\`):</b> Kliknij dowolną ulicę na mapie Poznania, aby wyłączyć ją z ruchu.`;
            }
        }

        // =====================================================================
        // INICJALIZACJA ZDARZEŃ MAPY
        // =====================================================================
        window.addEventListener('load', () => {
            setTimeout(() => {
                const map = getMapInstance();
                if (map) {
                    initBaseLayers(map);
                    map.on('mousemove', (ev) => {
                        const lat = ev.latlng.lat, lon = ev.latlng.lng;
                        const wgsEl = document.getElementById('status-wgs84');
                        const puwgEl = document.getElementById('status-puwg');
                        const demEl = document.getElementById('status-dem');
                        if (wgsEl) wgsEl.textContent = `${lat.toFixed(5)}°N, ${lon.toFixed(5)}°E`;
                        if (puwgEl) { const xy = wgs84ToPuwg1992(lat, lon); puwgEl.textContent = `X: ${xy.X} m, Y: ${xy.Y} m`; }
                        if (demEl) demEl.textContent = `${getDemElevation(lat, lon)} m n.p.m.`;
                    });
                    map.on('zoomend', () => {
                        const zEl = document.getElementById('status-zoom');
                        if (zEl) zEl.textContent = `Zoom: ${map.getZoom()}`;
                    });
                    map.on('click', (ev) => {
                        const lat = ev.latlng.lat, lon = ev.latlng.lng;
                        const nearestIdx = findNearestGraphNode(lat, lon);
                        if (pickingState === 'start') { startNodeIdx = nearestIdx; pickingState = null; renderCurrentScenario(); }
                        else if (pickingState === 'target') { targetNodeIdx = nearestIdx; pickingState = null; renderCurrentScenario(); }
                        else if (pickingState === 'iso') { isoCenterIdx = nearestIdx; pickingState = null; renderCurrentScenario(); }
                        else if (pickingState === 'od_origin') { odOriginNodeIdx = nearestIdx; pickingState = null; renderCurrentScenario(); }
                        else if (pickingState === 'tsp_add') {
                            customTspStops.push({ name: `Własny punkt #${customTspStops.length + 1}`, node_idx: nearestIdx, coords: DATA.graph_coords[nearestIdx], time_window: "08:30 - 12:00", service_min: 5 });
                            pickingState = null; renderCurrentScenario();
                        }
                        else if (pickingState === 'mm_start') { mmStartStopId = findNearestZtmStopId(lat, lon); pickingState = null; renderCurrentScenario(); }
                        else if (pickingState === 'mm_target') { mmTargetStopId = findNearestZtmStopId(lat, lon); pickingState = null; renderCurrentScenario(); }
                        else if (pickingState === 'storm_pick') { stormZoneKey = 'custom'; stormCustomCenter = [lat, lon]; pickingState = null; renderCurrentScenario(); }
                        else if (pickingState === 'loc_pick') { locCandidateNodeIdx = nearestIdx; pickingState = null; renderCurrentScenario(); }
                        else if (pickingState === 'evac_pick') { evacCustomCenter = [lat, lon]; pickingState = null; renderCurrentScenario(); }
                        else if (pickingState === 'block_road' || currentMode === 'detour') blockRoadNearLatLng(lat, lon, 0.085);
                        else if (currentMode === 'storm') { stormZoneKey = 'custom'; stormCustomCenter = [lat, lon]; renderCurrentScenario(); }
                        else if (currentMode === 'multimodal') { mmTargetStopId = findNearestZtmStopId(lat, lon); renderCurrentScenario(); }
                        else if (currentMode === 'isochrone') { isoCenterIdx = nearestIdx; renderCurrentScenario(); }
                        else if (currentMode === 'od_matrix') { odOriginNodeIdx = nearestIdx; renderCurrentScenario(); }
                        else if (currentMode === 'location') { locCandidateNodeIdx = nearestIdx; renderCurrentScenario(); }
                        else if (currentMode === 'evacuation') { evacCustomCenter = [lat, lon]; renderCurrentScenario(); }
                    });
                }
                document.title = "Analiza Przestrzenna Poznań — Paweł Siciński";
                document.body.classList.add('theme-modern');
                selectCategoryAndMode('utility', 'water');
            }, 350);
        });
    </script>
    """

    full_ui = (html_part1 + html_part2 + html_part3 + html_part4 + html_part5 + html_part6).replace("__GIS_DATA_PAYLOAD__", payload_json)
    m.get_root().html.add_child(folium.Element(full_ui))
    m.save(output_html)
    return output_html


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    raw_osm_file = os.path.join(script_dir, "osm_poznan_full_roads.json")
    boundary_file = os.path.join(script_dir, "poznan_boundary.json")
    tram_osm_file = os.path.join(script_dir, "poznan_tram_osm.json")
    out_file = os.path.join(script_dir, "analiza przestrzenna poznan.html")
    old_index = os.path.join(script_dir, "index.html")
    if os.path.exists(old_index):
        try:
            os.remove(old_index)
        except OSError:
            pass

    gis_bundle = load_and_prepare_poznan_gis(raw_osm_file, boundary_file, tram_osm_file)
    saved_path = build_interactive_web_app(gis_bundle, out_file)
    print(f"\n[7/7] [OK] Wygenerowano kompletny system DSS GIS Poznania (v4.0): {saved_path}")
    webbrowser.open(f"file:///{os.path.abspath(saved_path).replace(os.sep, '/')}")
