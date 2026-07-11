import React, { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

const COASTLINES_LAYER = {
  id: 'coastlines',
  type: 'line',
  source: 'coastlines',
  'source-layer': 'coastlines',
  paint: {
    'line-color': '#000000',
    'line-width': 0.5,
    'line-opacity': 1,
  },
};

const BASE_STYLE = {
  version: 8,
  sources: {
    coastlines: {
      type: 'vector',
      tiles: ['https://tiles.obis.org/coastlines_tiles/{z}/{x}/{y}.pbf'],
      minzoom: 0,
      maxzoom: 14,
    },
  },
  layers: [
    {
      id: 'background',
      type: 'background',
      paint: {
        'background-color': '#ffffff',
      },
    },
  ],
};

// Soft warm ramp: fades in at low density, muted coral at high.
const DENSITY_FILL_COLOR = [
  'interpolate',
  ['linear'],
  ['coalesce', ['get', 'density'], 0],
  0, '#fff5f0',
  0.2, '#fde6d8',
  0.4, '#f8cdb8',
  0.6, '#efab8a',
  0.8, '#e08b6d',
  1, '#c96f55',
];

const DENSITY_FILL_OPACITY = [
  'interpolate',
  ['linear'],
  ['coalesce', ['get', 'density'], 0],
  0, 0,
  0.08, 0.18,
  0.3, 0.42,
  0.6, 0.58,
  0.85, 0.68,
  1, 0.72,
];

function coordsFromGeometry(geometry) {
  if (geometry.type === 'Polygon') {
    return geometry.coordinates[0];
  }
  if (geometry.type === 'MultiPolygon') {
    return geometry.coordinates.flatMap((polygon) => polygon[0]);
  }
  return [];
}

function boundsFromGeoJSON(geojson) {
  const bounds = new maplibregl.LngLatBounds();
  for (const feature of geojson.features) {
    for (const coord of coordsFromGeometry(feature.geometry)) {
      bounds.extend(coord);
    }
  }
  return bounds;
}

function DensityMap({ geojson, records, aphiaid, lon, lat }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const [mapError, setMapError] = useState(null);

  useEffect(() => {
    if (!containerRef.current || !geojson) {
      return undefined;
    }

    setMapError(null);

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASE_STYLE,
      center: [lon ?? 0, lat ?? 0],
      zoom: lon != null && lat != null ? 2 : 1,
      attributionControl: false,
    });

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    map.on('load', () => {
      map.addSource('density', { type: 'geojson', data: geojson });

      map.addLayer({
        id: 'density-fill',
        type: 'fill',
        source: 'density',
        paint: {
          'fill-color': DENSITY_FILL_COLOR,
          'fill-opacity': DENSITY_FILL_OPACITY,
        },
      });

      map.addLayer({
        id: 'density-outline',
        type: 'line',
        source: 'density',
        paint: {
          'line-color': [
            'interpolate',
            ['linear'],
            ['coalesce', ['get', 'density'], 0],
            0, 'rgba(255, 255, 255, 0)',
            0.5, 'rgba(255, 255, 255, 0.12)',
            1, 'rgba(201, 111, 85, 0.25)',
          ],
          'line-width': 0.4,
        },
      });

      map.addLayer(COASTLINES_LAYER);

      if (records?.features?.length) {
        map.addSource('speciesgrids-records', {
          type: 'geojson',
          data: { type: 'FeatureCollection', features: records.features },
        });

        map.addLayer({
          id: 'speciesgrids-records',
          type: 'circle',
          source: 'speciesgrids-records',
          paint: {
            'circle-radius': [
              'interpolate',
              ['linear'],
              ['zoom'],
              3, 2,
              6, 3.5,
              9, 5,
            ],
            'circle-color': '#c0392b',
            'circle-opacity': 0.75,
          },
        });
      }

      if (lon != null && lat != null) {
        map.addSource('occurrence-point', {
          type: 'geojson',
          data: {
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [lon, lat] },
            properties: {},
          },
        });

        map.addLayer({
          id: 'occurrence-point',
          type: 'circle',
          source: 'occurrence-point',
          paint: {
            'circle-radius': 6,
            'circle-color': '#1a6b5c',
            'circle-stroke-color': '#ffffff',
            'circle-stroke-width': 2.5,
          },
        });
      }

      try {
        const bounds = boundsFromGeoJSON(geojson);
        if (lon != null && lat != null) {
          bounds.extend([lon, lat]);
        }
        map.fitBounds(bounds, { padding: 48, maxZoom: 5, duration: 0 });
      } catch (e) {
        console.error('Failed to fit map bounds', e);
      }
    });

    map.on('error', (e) => {
      console.error('MapLibre error', e);
      setMapError('Failed to render map tiles.');
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, [geojson, records, aphiaid, lon, lat]);

  return (
    <div className="density-map-panel">
      <div className="density-map-header">
        <span>Density map · AphiaID {aphiaid}</span>
        <span className="density-map-legend">
          Density
          <span className="density-map-legend-bar" aria-hidden="true" />
          {records?.features?.length ? (
            <>
              <span className="density-map-legend-dot" aria-hidden="true" />
              Records
            </>
          ) : null}
        </span>
      </div>
      {mapError && <div className="density-map-error">{mapError}</div>}
      <div ref={containerRef} className="density-map" />
    </div>
  );
}

export default DensityMap;
