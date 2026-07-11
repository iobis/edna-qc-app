import React, { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';

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
      paint: { 'background-color': '#ffffff' },
    },
    {
      id: 'coastlines',
      type: 'line',
      source: 'coastlines',
      'source-layer': 'coastlines',
      paint: {
        'line-color': '#000000',
        'line-width': 0.5,
      },
    },
  ],
};

function LocationMiniMap({ lon, lat }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (!containerRef.current || lon == null || lat == null) {
      return undefined;
    }

    const center = [Number(lon), Number(lat)];

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: BASE_STYLE,
      center,
      zoom: 3.5,
      attributionControl: false,
      interactive: true,
    });

    map.scrollZoom.disable();
    map.boxZoom.disable();
    map.dragRotate.disable();
    map.touchZoomRotate.disableRotation();

    map.on('load', () => {
      map.addSource('location', {
        type: 'geojson',
        data: {
          type: 'Feature',
          geometry: { type: 'Point', coordinates: center },
          properties: {},
        },
      });

      map.addLayer({
        id: 'location-point',
        type: 'circle',
        source: 'location',
        paint: {
          'circle-radius': 5,
          'circle-color': '#1a6b5c',
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 2,
        },
      });
    });

    return () => {
      map.remove();
    };
  }, [lon, lat]);

  return <div ref={containerRef} className="location-mini-map" />;
}

export default LocationMiniMap;
