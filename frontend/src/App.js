import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { interpolateSpectral } from 'd3-scale-chromatic';
import DensityMap from './DensityMap';
import CoordinatePopover from './CoordinatePopover';
import './App.css';

// Use relative URL if REACT_APP_API_URL is empty, otherwise use the provided URL
// Empty string means use relative URLs (works with nginx proxy)
const API_URL = process.env.REACT_APP_API_URL || '';
const MAP_GEOMETRY_VERSION = 3;

const ALLOWED_EXTENSIONS = ['.txt', '.csv', '.tsv', '.zip'];

const getBadgeStyle = (value, colorScale) => {
  const numValue = parseFloat(value) || 0;
  const color = colorScale(numValue);
  let backgroundColor = 'rgba(200, 200, 200, 0.3)';
  
  try {
    if (color && color.startsWith('#')) {
      const hex = color.replace('#', '');
      if (hex.length === 6) {
        const r = parseInt(hex.substr(0, 2), 16);
        const g = parseInt(hex.substr(2, 2), 16);
        const b = parseInt(hex.substr(4, 2), 16);
        backgroundColor = `rgba(${r}, ${g}, ${b}, 0.4)`;
      }
    } else if (color && color.startsWith('rgb')) {
      const rgbMatch = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
      if (rgbMatch) {
        const r = parseInt(rgbMatch[1]);
        const g = parseInt(rgbMatch[2]);
        const b = parseInt(rgbMatch[3]);
        backgroundColor = `rgba(${r}, ${g}, ${b}, 0.4)`;
      }
    }
  } catch (error) {
    console.error('Color parsing error:', error);
  }
  
  return {
    backgroundColor,
    color: '#1a1d21',
    padding: '0.2rem 0.5rem',
    borderRadius: '6px',
    fontSize: '0.75rem',
    fontWeight: '500',
    display: 'inline-block',
    minWidth: '3rem',
    textAlign: 'center',
    fontVariantNumeric: 'tabular-nums',
  };
};

function App() {
  const [files, setFiles] = useState([]);
  const [url, setUrl] = useState('');
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [annotations, setAnnotations] = useState({});
  const [annotationsLoaded, setAnnotationsLoaded] = useState(false);
  const [expandedRow, setExpandedRow] = useState(null);
  const [mapCache, setMapCache] = useState({});
  const [mapLoading, setMapLoading] = useState(null);
  const [mapErrors, setMapErrors] = useState({});

  const COLUMN_COUNT = 10;

  const ANNOTATIONS_STORAGE_KEY = 'occurrenceAnnotations';

  const getAnnotationKey = (occurrence) => {
    const aphiaid = occurrence.aphiaid ?? 'na';
    const lon = occurrence.decimalLongitude ?? 'na';
    const lat = occurrence.decimalLatitude ?? 'na';
    return `${aphiaid}|${lon}|${lat}`;
  };

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(ANNOTATIONS_STORAGE_KEY);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (parsed && typeof parsed === 'object') {
          setAnnotations(parsed);
        }
      }
      setAnnotationsLoaded(true);
    } catch (e) {
      console.error('Failed to load annotations from localStorage', e);
      setAnnotationsLoaded(true);
    }
  }, []);

  useEffect(() => {
    if (!annotationsLoaded) {
      return;
    }
    try {
      window.localStorage.setItem(
        ANNOTATIONS_STORAGE_KEY,
        JSON.stringify(annotations)
      );
    } catch (e) {
      console.error('Failed to save annotations to localStorage', e);
    }
  }, [annotations]);

  const handleAnnotationChange = (key, field, value) => {
    setAnnotations((prev) => {
      const next = { ...prev };
      const current = next[key] || { annotation: '', comments: '' };

      if (field === 'annotation') {
        if (!value) {
          if (!current.comments) {
            delete next[key];
          } else {
            next[key] = { ...current, annotation: '' };
          }
        } else {
          next[key] = { ...current, annotation: value };
        }
      } else {
        next[key] = { ...current, [field]: value || '' };
        if (!next[key].annotation && !next[key].comments) {
          delete next[key];
        }
      }
      return next;
    });
  };

  const handleClearAnnotations = () => {
    setAnnotations({});
    try {
      window.localStorage.removeItem(ANNOTATIONS_STORAGE_KEY);
    } catch (e) {
      console.error('Failed to clear annotations from localStorage', e);
    }
  };

  const handleDownloadAnnotations = () => {
    try {
      if (!uploadResult?.processing?.analyzed_occurrences) {
        return;
      }

      const occurrenceMap = {};
      uploadResult.processing.analyzed_occurrences.forEach((occ) => {
        const key = getAnnotationKey(occ);
        occurrenceMap[key] = occ;
      });

      const annotationsList = Object.entries(annotations)
        .map(([key, annotationData]) => {
          const [aphiaidStr, lonStr, latStr] = key.split('|');
          const aphiaid = aphiaidStr !== 'na' ? parseInt(aphiaidStr, 10) : null;
          const lon = lonStr !== 'na' ? parseFloat(lonStr) : null;
          const lat = latStr !== 'na' ? parseFloat(latStr) : null;

          const occurrence = occurrenceMap[key] || null;

          // Only include annotations that match occurrences in the current dataset
          if (!occurrence) {
            return null;
          }

          const annotationValue = annotationData?.annotation || '';

          if (annotationValue !== 'accept' && annotationValue !== 'reject') {
            return null;
          }

          return {
            aphiaid: aphiaid,
            scientificName: occurrence?.scientificName || null,
            scientificNameID: occurrence?.scientificNameID || null,
            phylum: occurrence?.phylum || null,
            class: occurrence?.class || null,
            decimalLongitude: lon,
            decimalLatitude: lat,
            footprintWKT: occurrence?.footprintWKT || null,
            density: occurrence?.density || null,
            suitability: occurrence?.suitability || null,
            annotation: annotationValue || null,
            comments: annotationData?.comments || null,
          };
        })
        .filter(item => item !== null) // Remove annotations that don't match current dataset or don't have accept/reject
        .sort((a, b) => {
          // Sort by aphiaid, then by coordinates
          if (a.aphiaid !== b.aphiaid) {
            return (a.aphiaid || 0) - (b.aphiaid || 0);
          }
          if (a.decimalLongitude !== b.decimalLongitude) {
            return (a.decimalLongitude || 0) - (b.decimalLongitude || 0);
          }
          return (a.decimalLatitude || 0) - (b.decimalLatitude || 0);
        });

      const dataStr = JSON.stringify(annotationsList, null, 2);
      const dataBlob = new Blob([dataStr], { type: 'application/json' });
      const url = URL.createObjectURL(dataBlob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'annotations.json';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('Failed to download annotations', e);
    }
  };

  const isValidFile = (filename) => {
    const ext = filename.toLowerCase().substring(filename.lastIndexOf('.'));
    return ALLOWED_EXTENSIONS.includes(ext);
  };

  const handleFileChange = (event) => {
    const selectedFiles = Array.from(event.target.files);
    const invalidFiles = selectedFiles.filter(f => !isValidFile(f.name));
    
    if (invalidFiles.length > 0) {
      setUploadError(`Invalid file type. Only ${ALLOWED_EXTENSIONS.join(', ')} files are allowed.`);
      setFiles([]);
      setUploadResult(null);
      event.target.value = '';
      return;
    }
    
    setFiles(selectedFiles);
    setUrl('');
    setUploadResult(null);
    setUploadError(null);
    setExpandedRow(null);
  };

  const handleUrlChange = (event) => {
    setUrl(event.target.value);
    setFiles([]);
    // Clear the file input element
    const fileInput = document.getElementById('fileUpload');
    if (fileInput) {
      fileInput.value = '';
    }
    setUploadResult(null);
    setUploadError(null);
    setExpandedRow(null);
  };

  const handleUpload = async (event) => {
    event.preventDefault();
    if (!files.length && !url.trim()) {
      return;
    }

    setLoading(true);
    setUploadResult(null);
    setUploadError(null);
    setExpandedRow(null);

    const formData = new FormData();
    if (files.length > 0) {
      files.forEach((file) => {
        formData.append('files', file);
      });
    }
    if (url.trim()) {
      formData.append('url', url.trim());
    }

    try {
      const response = await axios.post(`${API_URL}/api/upload`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setUploadResult(response.data);
      setUploadError(null);
    } catch (error) {
      console.error('Upload error:', error);
      const errorMessage = error.response?.data?.detail || error.message || 'Upload failed';
      setUploadError(errorMessage);
      setUploadResult(null);
    } finally {
      setLoading(false);
    }
  };

  const loadExample = () => {
    setUrl('https://ipt.obis.org/secretariat/archive.do?r=edna-wadden-sea&v=2.0');
    setFiles([]);
    const fileInput = document.getElementById('fileUpload');
    if (fileInput) {
      fileInput.value = '';
    }
    setUploadResult(null);
    setUploadError(null);
    setExpandedRow(null);
  };

  const toggleRowExpand = async (rowKey, occurrence) => {
    if (expandedRow === rowKey) {
      setExpandedRow(null);
      return;
    }

    setExpandedRow(rowKey);

    if (!occurrence.aphiaid || mapCache[occurrence.aphiaid]) {
      return;
    }

    setMapLoading(rowKey);
    try {
      const params = new URLSearchParams({ v: String(MAP_GEOMETRY_VERSION) });
      if (occurrence.decimalLongitude != null) {
        params.set('lon', occurrence.decimalLongitude);
      }
      if (occurrence.decimalLatitude != null) {
        params.set('lat', occurrence.decimalLatitude);
      }
      const query = params.toString();
      const response = await axios.get(
        `${API_URL}/api/density-map/${occurrence.aphiaid}${query ? `?${query}` : ''}`
      );
      setMapCache((prev) => ({ ...prev, [occurrence.aphiaid]: response.data }));
      setMapErrors((prev) => {
        const next = { ...prev };
        delete next[rowKey];
        return next;
      });
    } catch (error) {
      const message = error.response?.data?.detail || 'Failed to load density map';
      setMapErrors((prev) => ({ ...prev, [rowKey]: message }));
    } finally {
      setMapLoading(null);
    }
  };

  return (
    <div className="app">
      <header className="header">
        <h1>Geographic outlier detection</h1>
        <p>
          This app performs spatial and environmental outlier detection on species occurrence data.
          Upload Darwin Core text separated data files or a Darwin Core Archive, or point to a hosted Darwin Core Archive using a URL.
          Datasets should always include coordinates in the <code className="dwca-term">decimalLongitude</code> and <code className="dwca-term">decimalLatitude</code> columns.
          If no WoRMS LSIDs are provided in the <code className="dwca-term">scientificNameID</code> column, taxon matching is performed against WoRMS, which can slow down processing.
          Processing can be sped up by providing a <code className="dwca-term">taxonRank</code> column, as only species level occurrences are evaluated.
          For a typical dataset, the analysis should finish within one minute.
          Click the{' '}
          <button type="button" onClick={loadExample} className="example-link">
            example
          </button>{' '}
          to load a sample dataset.
        </p>
      </header>

      <div className="card">
        <form onSubmit={handleUpload}>
          <div className="form-group">
            <label htmlFor="fileUpload" className="form-label">
              Upload files
            </label>
            <input
              id="fileUpload"
              type="file"
              className="form-input"
              multiple
              accept=".txt,.csv,.tsv,.zip"
              onChange={handleFileChange}
              disabled={loading}
            />
          </div>
          <div className="form-group">
            <label htmlFor="urlInput" className="form-label">
              Or provide a URL to a zip file{' '}
              <button type="button" onClick={loadExample} className="example-link">
                (example)
              </button>
            </label>
            <input
              id="urlInput"
              type="url"
              className="form-input"
              value={url}
              onChange={handleUrlChange}
              disabled={loading}
            />
          </div>
          <button
            type="submit"
            className="btn btn-primary"
            disabled={(!files.length && !url.trim()) || loading}
          >
            {loading ? 'Analyzing…' : 'Upload'}
          </button>
        </form>

        {loading && (
          <div className="loading">
            <div className="spinner" role="status" aria-label="Loading" />
            <span>Running analysis…</span>
          </div>
        )}

        {uploadError && (
          <div className="alert alert-danger">
            {uploadError}
          </div>
        )}
      </div>

      {uploadResult && (
        <>
          {uploadResult.processing?.analysis_error && (
            <div className="alert alert-warning">
              {uploadResult.processing.analysis_error}
            </div>
          )}
          {uploadResult.processing?.analyzed_occurrences && (
            <section className="results">
              <div className="results-header">
                <h2>Analysis results</h2>
                <div className="btn-group">
                  <button
                    type="button"
                    className="btn btn-outline btn-sm"
                    onClick={handleDownloadAnnotations}
                    disabled={Object.keys(annotations).length === 0}
                  >
                    Download annotations
                  </button>
                  <button
                    type="button"
                    className="btn btn-outline btn-outline-danger btn-sm"
                    onClick={handleClearAnnotations}
                  >
                    Clear annotations
                  </button>
                </div>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th className="col-toggle" aria-label="Expand" />
                      <th>Species</th>
                      <th>Phylum</th>
                      <th>Class</th>
                      <th>AphiaID</th>
                      <th>Coordinates</th>
                      <th>Density</th>
                      <th>Suitability</th>
                      <th>Annotation</th>
                      <th>Comments</th>
                    </tr>
                  </thead>
                  <tbody>
                    {uploadResult.processing.analyzed_occurrences
                      .slice()
                      .sort((a, b) => {
                        const da = a.density ?? Infinity;
                        const db = b.density ?? Infinity;
                        return da - db;
                      })
                      .map((occurrence) => {
                        const rowKey = getAnnotationKey(occurrence);
                        const annotationData = annotations[rowKey] || { annotation: '', comments: '' };
                        const annotationValue = annotationData.annotation || '';
                        const commentsValue = annotationData.comments || '';
                        const isExpanded = expandedRow === rowKey;
                        const cachedMap = occurrence.aphiaid ? mapCache[occurrence.aphiaid] : null;
                        return (
                          <React.Fragment key={rowKey}>
                            <tr className={isExpanded ? 'row-expanded' : undefined}>
                              <td className="col-toggle">
                                {occurrence.aphiaid ? (
                                  <button
                                    type="button"
                                    className={`row-toggle${isExpanded ? ' row-toggle-open' : ''}`}
                                    onClick={() => toggleRowExpand(rowKey, occurrence)}
                                    aria-expanded={isExpanded}
                                    aria-label={isExpanded ? 'Hide density map' : 'Show density map'}
                                  />
                                ) : null}
                              </td>
                            <td className="species">
                              {occurrence.aphiaid ? (
                                <a
                                  href={`https://obis.org/taxon/${occurrence.aphiaid}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {occurrence.scientificName || occurrence.aphiaid}
                                </a>
                              ) : (
                                occurrence.scientificName || <span className="empty-cell">—</span>
                              )}
                            </td>
                            <td>{occurrence.phylum || <span className="empty-cell">—</span>}</td>
                            <td>{occurrence.class || <span className="empty-cell">—</span>}</td>
                            <td>
                              {occurrence.aphiaid ? (
                                <a
                                  href={`https://www.marinespecies.org/aphia.php?p=taxdetails&id=${occurrence.aphiaid}#distributions`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {occurrence.aphiaid}
                                </a>
                              ) : (
                                <span className="empty-cell">—</span>
                              )}
                            </td>
                            <td>
                              {occurrence.decimalLongitude != null && occurrence.decimalLatitude != null ? (
                                <CoordinatePopover
                                  lon={occurrence.decimalLongitude}
                                  lat={occurrence.decimalLatitude}
                                />
                              ) : (
                                <span className="empty-cell">—</span>
                              )}
                            </td>
                            <td>
                              {occurrence.density != null ? (
                                <span className="badge" style={getBadgeStyle(occurrence.density, interpolateSpectral)}>
                                  {Number(occurrence.density).toFixed(4)}
                                </span>
                              ) : (
                                <span className="empty-cell">—</span>
                              )}
                            </td>
                            <td>
                              {occurrence.suitability != null ? (
                                <span className="badge" style={getBadgeStyle(occurrence.suitability, interpolateSpectral)}>
                                  {Number(occurrence.suitability).toFixed(4)}
                                </span>
                              ) : (
                                <span className="empty-cell">—</span>
                              )}
                            </td>
                            <td>
                              <select
                                className="form-select form-select-sm"
                                value={annotationValue}
                                onChange={(e) =>
                                  handleAnnotationChange(rowKey, 'annotation', e.target.value)
                                }
                              >
                                <option value="">—</option>
                                <option value="accept">Accept</option>
                                <option value="reject">Reject</option>
                              </select>
                            </td>
                            <td>
                              <input
                                type="text"
                                className="form-input form-input-sm"
                                value={commentsValue}
                                onChange={(e) =>
                                  handleAnnotationChange(rowKey, 'comments', e.target.value)
                                }
                              />
                            </td>
                          </tr>
                          {isExpanded && (
                            <tr className="expand-row">
                              <td colSpan={COLUMN_COUNT}>
                                {mapLoading === rowKey && (
                                  <div className="density-map-loading">
                                    <div className="spinner" role="status" aria-label="Loading map" />
                                    <span>Loading density map…</span>
                                  </div>
                                )}
                                {mapErrors[rowKey] && (
                                  <div className="density-map-error">{mapErrors[rowKey]}</div>
                                )}
                                {cachedMap && mapLoading !== rowKey && !mapErrors[rowKey] && (
                                  <DensityMap
                                    geojson={cachedMap}
                                    records={cachedMap.records}
                                    aphiaid={occurrence.aphiaid}
                                    lon={occurrence.decimalLongitude}
                                    lat={occurrence.decimalLatitude}
                                  />
                                )}
                              </td>
                            </tr>
                          )}
                          </React.Fragment>
                        );
                      })}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}

export default App;

