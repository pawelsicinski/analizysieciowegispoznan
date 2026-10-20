"""
Pobiera z OpenStreetMap (Overpass API):
1. Dokładną granicę administracyjną miasta Poznania (poligon granicy miasta),
2. Pełną sieć drogową Poznania wraz z mniejszymi ulicami osiedlowymi i lokalnymi
   (motorway, trunk, primary, secondary, tertiary, unclassified, residential, living_street).
"""
import json
import urllib.parse
import urllib.request

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter"
]

def query_overpass(query_str, desc=""):
    data = urllib.parse.urlencode({"data": query_str}).encode("utf-8")
    for url in OVERPASS_ENDPOINTS:
        try:
            print(f"Pobieranie ({desc}) z {url} ...")
            req = urllib.request.Request(url, data=data, headers={"User-Agent": "PoznanGISFull/2.0"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                print(f"  -> Pobrano {len(result.get('elements', []))} elementów!")
                return result
        except Exception as e:
            print(f"  Błąd {url}: {e}")
    raise RuntimeError(f"Nie udało się pobrać: {desc}")

def main():
    # 1. Granica administracyjna miasta Poznania (relacja admin_level=6 lub 8)
    boundary_query = """
    [out:json][timeout:60];
    relation["boundary"="administrative"]["name"="Poznań"]["admin_level"="6"];
    out geom;
    """
    boundary_data = query_overpass(boundary_query, "Granica administracyjna Poznania")
    with open("poznan_boundary.json", "w", encoding="utf-8") as f:
        json.dump(boundary_data, f)

    # 2. Pełna sieć drogowa w granicach Poznania (wraz z residential i unclassified!) + korytarz do COŚ Koziegłowy
    roads_query = """
    [out:json][timeout:90];
    area["boundary"="administrative"]["name"="Poznań"]["admin_level"="6"]->.poznan;
    (
      way["highway"~"^(motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|tertiary|tertiary_link|unclassified|residential|living_street)$"](area.poznan);
      way["highway"~"^(trunk|primary|secondary|tertiary)$"](52.435,16.955,52.452,16.995);
    );
    out body;
    >;
    out skel qt;
    """
    roads_data = query_overpass(roads_query, "Pełna sieć dróg Poznania (z mniejszymi ulicami)")
    with open("osm_poznan_full_roads.json", "w", encoding="utf-8") as f:
        json.dump(roads_data, f)
    print("Zapisano poznan_boundary.json oraz osm_poznan_full_roads.json!")

if __name__ == "__main__":
    main()
