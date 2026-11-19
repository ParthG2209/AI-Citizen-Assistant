# `mp_districts.geojson` — Madhya Pradesh districts

**Real geography, not synthetic.** 55 features, one per current MP district, sorted by `name`.

## Source and licence

District boundaries for the 50 districts that existed at Census 2011 come from
[DataMeet India — `Districts/Census_2011/2011_Dist.shp`](https://github.com/datameet/maps/tree/master/Districts/Census_2011),
© DataMeet India community, licensed under
[Creative Commons Attribution 2.5 India (CC BY 2.5 IN)](https://creativecommons.org/licenses/by/2.5/in/).
The DataMeet README states the boundaries were derived from taluk boundaries (GeoCommons / Bhuvan) and
the Census of India *Administrative Atlas of India 2011*.

Changes made (derivative work under the same attribution requirement):

1. Filtered to `ST_NM == "Madhya Pradesh"` (50 polygons).
2. Simplified with Shapely `simplify(0.005, preserve_topology=True)` (≈500 m) and coordinates rounded to
   4 decimal places to keep the file small (~185 KB). Not suitable for precise area/boundary work.
3. Renamed to current official names: Hoshangabad → **Narmadapuram**, East Nimar → **Khandwa**,
   West Nimar → **Khargone**, Narsimhapur → **Narsinghpur** (original kept in `census_2011_name`).
4. Added 5 districts formed after Census 2011 as **Point** features only (no boundary available in the
   source): Agar Malwa (from Shajapur, 2013), Niwari (from Tikamgarh, 2018), Maihar (from Satna, 2023),
   Mauganj (from Rewa, 2023), Pandhurna (from Chhindwara, 2023). Their points are *approximate* district
   headquarters town coordinates (2 decimal places). The 2011 polygons of their parent districts still
   include the carved-out territory.

## Feature properties

| Property | Type | Notes |
|---|---|---|
| `district_id` | string | snake_case slug of the current name (`narmadapuram`, `agar_malwa`) — the id used by analytics outputs |
| `name` | string | current district name |
| `census_2011_name` | string \| null | name in the source shapefile; `null` for post-2011 districts |
| `census_2011_code` | int \| null | `DT_CEN_CD` from the source |
| `formed_after_2011` | bool | |
| `formed_year`, `parent_district_2011` | int, string | only on post-2011 districts (`parent_district_2011` is `null` otherwise) |
| `centroid` | `[lat, lng]` | polygon centroid (or representative point if the centroid falls outside), or approximate HQ point |
| `centroid_method` | string | how `centroid` was derived |
| `boundary_source` | string \| null | `null` when the geometry is only a Point |

Geometry is `Polygon`, `MultiPolygon` or `Point` (WGS 84, `[lng, lat]` order as per GeoJSON).

## Regenerating

Download `2011_Dist.{shp,shx,dbf,prj}` from the DataMeet link above, then with
[pyshp](https://pypi.org/project/pyshp/) + [Shapely](https://pypi.org/project/shapely/) read the shapefile,
keep MP records, apply steps 1–4 above and write a `FeatureCollection` with the properties listed here.
