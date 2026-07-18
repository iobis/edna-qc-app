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

function addRecordsLayer(map, records) {
  if (!records?.features?.length) {
    return;
  }

  const data = { type: 'FeatureCollection', features: records.features };
  const source = map.getSource('speciesgrids-records');
  if (source) {
    source.setData(data);
  } else {
    map.addSource('speciesgrids-records', { type: 'geojson', data });
    map.addLayer({
      id: 'speciesgrids-records',
      type: 'circle',
      source: 'speciesgrids-records',
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 3, 2, 6, 3.5, 9, 5],
        'circle-color': '#c0392b',
        'circle-opacity': 0.75,
      },
    });
  }
  map.moveLayer('speciesgrids-records');
}

function ExternalLinkIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M13.5 6H5.25A2.25 2.25 0 0 0 3 8.25v10.5A2.25 2.25 0 0 0 5.25 21h10.5A2.25 2.25 0 0 0 18 18.75V10.5m-10.5 6L21 3m0 0h-5.25M21 3v5.25"
      />
    </svg>
  );
}

function DensityMap({ geojson, records, aphiaid, scientificName, lon, lat }) {
  const containerRef = useRef(null);
  const mapRef = useRef(null);
  const layersReadyRef = useRef(false);
  const [mapError, setMapError] = useState(null);

  useEffect(() => {
    if (!containerRef.current || !geojson) {
      return undefined;
    }

    setMapError(null);
    layersReadyRef.current = false;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASE_STYLE,
      center: [lon ?? 0, lat ?? 0],
      zoom: lon != null && lat != null ? 2 : 1,
      attributionControl: false,
    });

    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');

    // Avoid accidental zoom while the user is scrolling the page
    map.scrollZoom.disable();
    const enableScrollZoom = () => map.scrollZoom.enable();
    const disableScrollZoom = () => map.scrollZoom.disable();
    map.on('click', enableScrollZoom);
    map.getCanvas().addEventListener('mouseleave', disableScrollZoom);

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

      layersReadyRef.current = true;

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
      map.off('click', enableScrollZoom);
      map.getCanvas().removeEventListener('mouseleave', disableScrollZoom);
      map.remove();
      mapRef.current = null;
      layersReadyRef.current = false;
    };
  }, [geojson, aphiaid, lon, lat]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || records === undefined) {
      return undefined;
    }

    const apply = () => addRecordsLayer(map, records);
    if (layersReadyRef.current) {
      apply();
    } else {
      map.once('load', apply);
    }
  }, [records]);

  const gbifQuery = encodeURIComponent(scientificName || String(aphiaid)).replace(/%20/g, '+');

  return (
    <div className="density-map-panel">
      <div className="density-map-header">
        <span className="density-map-title">
          Density map
          <span className="density-map-links">
            <a
              href={`https://www.marinespecies.org/aphia.php?p=taxdetails&id=${aphiaid}#distributions`}
              target="_blank"
              rel="noopener noreferrer"
            >
              WoRMS
              <ExternalLinkIcon />
            </a>
            <a
              href={`https://obis.org/taxon/${aphiaid}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              OBIS
              <ExternalLinkIcon />
            </a>
            <a
              href={`https://www.gbif.org/taxon/search?q=${gbifQuery}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              GBIF
              <ExternalLinkIcon />
            </a>
          </span>
        </span>
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
