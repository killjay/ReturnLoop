import { useEffect, useRef, useMemo, useCallback } from 'react';
import Map, { Source, Layer, Marker, NavigationControl } from 'react-map-gl/mapbox';
import 'mapbox-gl/dist/mapbox-gl.css';

const MAPBOX_TOKEN = import.meta.env.VITE_MAPBOX_TOKEN;

const DEFAULT_WAREHOUSE = { lat: 39.8283, lon: -98.5795 };

const INITIAL_VIEW = {
  longitude: -98.5,
  latitude: 39.8,
  zoom: 3.5,
};

export default function ReturnMap({ returnUpdates }) {
  const mapRef = useRef(null);

  // Auto-fit bounds when returnUpdates change
  useEffect(() => {
    if (!returnUpdates?.length || !mapRef.current) return;
    const coords = [];
    returnUpdates.forEach(u => {
      if (u.source) coords.push([u.source.lon, u.source.lat]);
      if (u.target) coords.push([u.target.lon, u.target.lat]);
      if (u.warehouse) coords.push([u.warehouse.lon, u.warehouse.lat]);
    });
    if (coords.length === 0) return;

    const lngs = coords.map(c => c[0]);
    const lats = coords.map(c => c[1]);
    mapRef.current.fitBounds(
      [[Math.min(...lngs), Math.min(...lats)], [Math.max(...lngs), Math.max(...lats)]],
      { padding: 60, duration: 1000 }
    );
  }, [returnUpdates]);

  // Register arrow image on map load
  const onMapLoad = useCallback((e) => {
    const map = e.target;
    if (map.hasImage('arrow-triangle')) return;
    const size = 16;
    const canvas = document.createElement('canvas');
    canvas.width = size;
    canvas.height = size;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#10b981';
    ctx.beginPath();
    ctx.moveTo(size / 2, 0);
    ctx.lineTo(size, size);
    ctx.lineTo(0, size);
    ctx.closePath();
    ctx.fill();
    map.addImage('arrow-triangle', { width: size, height: size, data: ctx.getImageData(0, 0, size, size).data });
  }, []);

  // GeoJSON for avoided warehouse routes (dashed red)
  const avoidedRouteGeoJSON = useMemo(() => ({
    type: 'FeatureCollection',
    features: (returnUpdates || [])
      .filter(u => u.source && u.target)
      .map(u => {
        const wh = u.warehouse || DEFAULT_WAREHOUSE;
        return {
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: [
              [u.source.lon, u.source.lat],
              [wh.lon, wh.lat],
              [u.target.lon, u.target.lat],
            ],
          },
        };
      }),
  }), [returnUpdates]);

  // GeoJSON for direct reroute lines (solid green)
  const directRouteGeoJSON = useMemo(() => ({
    type: 'FeatureCollection',
    features: (returnUpdates || [])
      .filter(u => u.source && u.target)
      .map(u => ({
        type: 'Feature',
        geometry: {
          type: 'LineString',
          coordinates: [
            [u.source.lon, u.source.lat],
            [u.target.lon, u.target.lat],
          ],
        },
      })),
  }), [returnUpdates]);

  // Warehouse location from first update or default
  const warehouse = returnUpdates?.[0]?.warehouse || DEFAULT_WAREHOUSE;

  if (!MAPBOX_TOKEN) {
    return (
      <div className="p-4 h-full flex items-center justify-center">
        <div className="text-center">
          <p className="text-sm text-red-400 font-medium">MapBox token not configured</p>
          <p className="text-xs text-gray-500 mt-1">Add VITE_MAPBOX_TOKEN to frontend/.env</p>
        </div>
      </div>
    );
  }

  return (
    <div className="p-4 h-full">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-400">Reroute Map</h3>
        <div className="flex items-center gap-4 text-xs text-gray-500">
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 bg-emerald-500 inline-block rounded" /> Direct reroute
          </span>
          <span className="flex items-center gap-1.5">
            <span className="w-3 h-0.5 bg-red-500/50 inline-block rounded" /> Avoided warehouse route
          </span>
        </div>
      </div>

      <div className="relative rounded-lg overflow-hidden" style={{ height: 'calc(100vh - 260px)' }}>
        <Map
          ref={mapRef}
          initialViewState={INITIAL_VIEW}
          style={{ width: '100%', height: '100%' }}
          mapStyle="mapbox://styles/mapbox/dark-v11"
          mapboxAccessToken={MAPBOX_TOKEN}
          onLoad={onMapLoad}
        >
          <NavigationControl position="top-right" />

          {/* Avoided warehouse routes - dashed red */}
          <Source id="avoided-routes" type="geojson" data={avoidedRouteGeoJSON}>
            <Layer
              id="avoided-routes-layer"
              type="line"
              paint={{
                'line-color': '#ef4444',
                'line-opacity': 0.5,
                'line-width': 1.5,
                'line-dasharray': [4, 4],
              }}
            />
          </Source>

          {/* Direct reroute lines - solid green */}
          <Source id="direct-routes" type="geojson" data={directRouteGeoJSON}>
            <Layer
              id="direct-routes-layer"
              type="line"
              paint={{
                'line-color': '#10b981',
                'line-opacity': 1,
                'line-width': 2.5,
              }}
            />
            <Layer
              id="direct-routes-arrows"
              type="symbol"
              layout={{
                'symbol-placement': 'line',
                'symbol-spacing': 100,
                'icon-image': 'arrow-triangle',
                'icon-size': 0.6,
                'icon-allow-overlap': true,
              }}
            />
          </Source>

          {/* Warehouse marker */}
          <Marker longitude={warehouse.lon} latitude={warehouse.lat} anchor="center">
            <div className="flex flex-col items-center">
              <div className="w-4 h-4 rounded-full bg-gray-500 border-2 border-gray-400" />
              <span className="text-[10px] text-gray-400 mt-1">Warehouse</span>
            </div>
          </Marker>

          {/* Source (returner) markers - orange */}
          {returnUpdates?.map((u, i) => u.source && (
            <Marker key={`src-${i}`} longitude={u.source.lon} latitude={u.source.lat} anchor="center">
              <div className="flex flex-col items-center">
                <div className="w-3 h-3 rounded-full bg-amber-500 border border-amber-400" />
                <span className="text-[11px] text-gray-300 mt-0.5">Returner</span>
              </div>
            </Marker>
          ))}

          {/* Target (recipient) markers - green */}
          {returnUpdates?.map((u, i) => u.target && (
            <Marker key={`tgt-${i}`} longitude={u.target.lon} latitude={u.target.lat} anchor="center">
              <div className="flex flex-col items-center">
                <div className="w-3 h-3 rounded-full bg-emerald-500 border border-emerald-400" />
                <span className="text-[11px] text-gray-300 mt-0.5">{u.target.name || 'Recipient'}</span>
              </div>
            </Marker>
          ))}

          {/* Savings badges at route midpoints */}
          {returnUpdates?.map((u, i) => u.savings && u.source && u.target && (
            <Marker
              key={`badge-${i}`}
              longitude={(u.source.lon + u.target.lon) / 2}
              latitude={(u.source.lat + u.target.lat) / 2}
              anchor="bottom"
            >
              <div className="bg-emerald-950/80 border border-emerald-800 rounded px-2 py-0.5 text-[10px] font-bold text-emerald-400 whitespace-nowrap">
                {u.savings.miles_saved} mi saved | {u.savings.co2_saved_kg} kg CO₂
              </div>
            </Marker>
          ))}
        </Map>

        {/* Empty state overlay */}
        {(!returnUpdates || returnUpdates.length === 0) && (
          <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none z-10">
            <p className="text-sm text-gray-500">Reroute paths will appear here</p>
            <p className="text-xs text-gray-600 mt-1">Trigger a return to see the map in action</p>
          </div>
        )}
      </div>
    </div>
  );
}
