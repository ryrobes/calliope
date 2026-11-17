# SQL-backed Stage maps

Call `publish_map` for a standalone analytical map. No HTML, JavaScript, embedded
result rows, or Mapbox token belongs in the call. The appliance supplies its
configured public token, vendored Mapbox runtime, and theme basemap at render time.

```json
{
  "title": "Customer locations by annual revenue",
  "queries": {
    "customers": "SELECT longitude, latitude, name, annual_revenue FROM reporting.customers WHERE longitude IS NOT NULL AND latitude IS NOT NULL"
  },
  "sources": {
    "customers": {
      "dataset": "customers",
      "longitude": "longitude",
      "latitude": "latitude",
      "tooltip": ["name", "annual_revenue"]
    }
  },
  "layers": [{
    "id": "customers",
    "type": "circle",
    "source": "customers",
    "paint": {
      "circle-color": "@accent",
      "circle-opacity": 0.7,
      "circle-radius": ["interpolate", ["linear"], ["to-number", ["get", "annual_revenue"], 0], 0, 4, 1000000, 22]
    }
  }],
  "viewport": {"fit": "data"},
  "size": "wide",
  "height": 420
}
```

Replace the illustrative relation/columns with validated data. SQL runs under the
viewer when a visible map opens or is refreshed, sharing the dashboard query
cache and read-only gate. Publication itself does not execute and persist rows.
Use `row_limit` (default 5000, maximum 20000) and aggregate larger data in SQL.
Truncation and skipped invalid geometry are shown in the tile. A nonempty source
with no valid geometries fails visibly rather than producing an apparently valid
empty map. Null/blank coordinates never become 0,0.

For regions/routes, replace longitude/latitude with `"geometry":"geometry_column"`.
The column must contain a GeoJSON **geometry** object or JSON string, not a Feature
or FeatureCollection. Supported geometries: Point, MultiPoint, LineString,
MultiLineString, Polygon, MultiPolygon. Polygon rings must be closed. A PostGIS
query can return `ST_AsGeoJSON(ST_Transform(geom,4326)) AS geometry_column` when
that extension and geometry data exist. The tool does not geocode addresses or
supply region boundaries automatically.

Layers use native Mapbox JSON: circle, heatmap, line, fill, fill-extrusion, symbol;
paint, layout, filter, minzoom, maxzoom. Use native expressions for data-driven
styling. Colors can use `@accent`, `@positive`, `@warning`, `@danger`, `@text`.
The Stage supplies basic theme defaults for omitted paint. Text labels require a
symbol layer's native `text-field` layout property. Arbitrary icon images, custom
JS layers, external data sources and HTML popup templates are not supported.
Tooltip fields render as escaped text. Sources can enable native point clustering
with `cluster`, `clusterRadius`, `clusterMaxZoom`; define cluster and unclustered
layers using native `point_count` expressions/filters.

`size` is compact (one column), wide (two), or full (all). Viewport `height` is
240–900 pixels, default 420; the packer measures the rest naturally. `viewport`
can instead specify center `[longitude,latitude]`, zoom, pitch and bearing. Fit
handles points, line/polygon bounds and crossings of the antimeridian. Dragging,
zoom controls, cooperative scroll zoom and text popups stay inside the map;
clicking the map content does not pin/unpin the Stage tile.

`read_map(surface_id)` returns the saved contract, latest revision ID and owner
browser render receipt/error. `update_map(surface_id, ...)` retains omitted fields
and replaces supplied objects whole. Updates create immutable revisions in the
same lineage; only the latest is visible. Read the latest before updating; stale
writers are rejected. No automatic chat turn is caused by normal publication.

Query/runtime/render errors use bounded, deduplicated feedback to Calliope, as
Stage charts do. A missing receipt means no owner browser reported yet. Successful
publication is not proof of rendering or analytical correctness. Offscreen maps
release GPU contexts; at most four remain active. Pan/zoom is preserved across
packing, refresh, theme changes and suspension. Refresh obtains new data; Fit data
explicitly resets the camera to the returned geometry.
