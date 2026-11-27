import { cellToBoundary } from 'h3-js';

function h3CellPolygon(h3Index) {
  const boundary = cellToBoundary(h3Index, true);
  if (!boundary || boundary.length < 3) {
    return null;
  }

  const ring = new Array(boundary.length + 1);
  for (let i = 0; i < boundary.length; i++) {
    let lon = boundary[i][0];
    const lat = boundary[i][1];
    if (i > 0) {
      const prev = ring[i - 1][0];
      if (lon - prev > 180) lon -= 360;
      else if (prev - lon > 180) lon += 360;
    }
    ring[i] = [lon, lat];
  }
  ring[boundary.length] = [ring[0][0], ring[0][1]];
  return { type: 'Polygon', coordinates: [ring] };
}

export function cellsToFeatureCollection(cells, valueKey) {
  const features = [];
  if (!cells?.length) {
    return { type: 'FeatureCollection', features };
  }

  for (const cell of cells) {
    const h3Index = cell[0];
    let geometry = null;
    try {
      geometry = h3CellPolygon(h3Index);
    } catch (error) {
      continue;
    }
    if (!geometry) {
      continue;
    }
    features.push({
      type: 'Feature',
      geometry,
      properties: {
        h3: h3Index,
        [valueKey]: cell[1],
      },
    });
  }

  return { type: 'FeatureCollection', features };
}
