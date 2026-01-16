import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { interpolateSpectral } from 'd3-scale-chromatic';

// Use relative URL if REACT_APP_API_URL is empty, otherwise use the provided URL
// Empty string means use relative URLs (works with nginx proxy)
const API_URL = process.env.REACT_APP_API_URL || '';

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
    backgroundColor: backgroundColor,
    color: '#000000',
    padding: '4px 8px',
    borderRadius: '12px',
    fontSize: '0.875rem',
    fontWeight: '500',
    display: 'inline-block',
    minWidth: '40px',
    textAlign: 'center'
  };
};

function App() {
  const [status, setStatus] = useState('loading');
  const [files, setFiles] = useState([]);
  const [url, setUrl] = useState('');
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [annotations, setAnnotations] = useState({});
  const [annotationsLoaded, setAnnotationsLoaded] = useState(false);

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
          // Migrate old format (string values) to new format (object values)
          const migrated = {};
          for (const [key, value] of Object.entries(parsed)) {
            if (typeof value === 'string') {
              // Old format: just annotation string
              migrated[key] = { annotation: value, alternative: '', comments: '' };
            } else {
              // New format: object with annotation, alternative, comments
              migrated[key] = {
                annotation: value?.annotation || '',
                alternative: value?.alternative || '',
                comments: value?.comments || ''
              };
            }
          }
          setAnnotations(migrated);
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
      const current = next[key] || { annotation: '', alternative: '', comments: '' };
      
      if (field === 'annotation') {
        if (!value) {
          // If annotation is cleared and all fields are empty, remove the entry
          if (!current.alternative && !current.comments) {
            delete next[key];
          } else {
            next[key] = { ...current, annotation: '' };
          }
        } else {
          next[key] = { ...current, annotation: value };
        }
      } else {
        // For alternative or comments
        next[key] = { ...current, [field]: value || '' };
        // If all fields are empty, remove the entry
        if (!next[key].annotation && !next[key].alternative && !next[key].comments) {
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

          // Handle both old format (string) and new format (object)
          const annotationValue = typeof annotationData === 'string' 
            ? annotationData 
            : annotationData?.annotation || '';
          
          // Only include annotations with "accept" or "reject"
          if (annotationValue !== 'accept' && annotationValue !== 'reject') {
            return null;
          }

          const alternative = typeof annotationData === 'object' 
            ? (annotationData?.alternative || '') 
            : '';
          const comments = typeof annotationData === 'object' 
            ? (annotationData?.comments || '') 
            : '';

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
            alternative: alternative || null,
            comments: comments || null,
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

  const handleDownloadAnnotationsOldFormat = () => {
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

          // Handle both old format (string) and new format (object)
          const annotationValue = typeof annotationData === 'string' 
            ? annotationData 
            : annotationData?.annotation || '';
          
          // Only include annotations with "accept" or "reject"
          if (annotationValue !== 'accept' && annotationValue !== 'reject') {
            return null;
          }

          const alternative = typeof annotationData === 'object' 
            ? (annotationData?.alternative || '') 
            : '';
          const comments = typeof annotationData === 'object' 
            ? (annotationData?.comments || '') 
            : '';

          // Convert alternative to integer if it's a valid number
          let newAphiaID = null;
          if (alternative) {
            const parsed = parseInt(alternative, 10);
            if (!isNaN(parsed)) {
              newAphiaID = parsed;
            }
          }

          const result = {
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
            AphiaID: aphiaid, // Use existing aphiaid field (renamed to AphiaID)
            new_AphiaID: newAphiaID, // Convert alternative to integer
            comments: comments || null,
          };

          // Add remove field based on annotation value
          if (annotationValue === 'reject') {
            // If reject and there is an alternative, remove should be false
            // If reject and there is no alternative, remove should be true (or not set, depending on requirement)
            result.remove = alternative ? false : true;
          }
          // If accept, don't add remove field

          return result;
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
      link.download = 'annotations_old_format.json';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
    } catch (e) {
      console.error('Failed to download annotations (old format)', e);
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
  };

  const handleUpload = async (event) => {
    event.preventDefault();
    if (!files.length && !url.trim()) {
      return;
    }

    setLoading(true);
    setUploadResult(null);
    setUploadError(null); // Clear errors when starting a new analysis

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

  return (
    <div className="container mt-5">
      <style>{`
        .dwca-term {
          font-family: monospace;
          color: var(--bs-body-color);
          background-color: #dff5eb;
          padding: 2px 6px;
          border-radius: 4px;
        }
        a {
          text-decoration: none;
        }
        a:hover {
          text-decoration: underline;
        }
      `}</style>
      <div className="row">
        <div className="col-md-12">
          <h1 className="mb-2">Geographic outlier detection</h1>
          <p>
            This app performs spatial and environmental outlier detection on species occurrence data.
            Upload Darwin Core text separated data files or a Darwin Core Archive, or point to a hosted Darwin Core Archive using a URL.
            Datasets should always include coordinates in the <code className="dwca-term">decimalLongitude</code> and <code className="dwca-term">decimalLatitude</code> columns.
            If no WoRMS LSIDs are provided in the <code className="dwca-term">scientificNameID</code> column, taxon matching is performed against WoRMS, which can slow down processing.
            Processing can be sped up by providing a <code className="dwca-term">taxonRank</code> column, as only species level occurrences are evaluated.
            For a typical dataset, the analysis should finish within one minute.
            Click on the <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    setUrl('https://ipt.obis.org/secretariat/archive.do?r=edna-wadden-sea&v=2.0');
                  }}
                  className="text-decoration-none"
                >
                  (example)
                </a> link to load an example dataset.</p>
        </div>
      </div>
      <div className="row mt-3">
        <div className="col-md-12">
          <form onSubmit={handleUpload}>
            <div className="mb-3">
              <label htmlFor="fileUpload" className="form-label">
                Upload files
              </label>
              <input
                id="fileUpload"
                type="file"
                className="form-control"
                multiple
                accept=".txt,.csv,.tsv,.zip"
                onChange={handleFileChange}
                disabled={loading}
              />
            </div>
            <div className="mb-3">
              <label htmlFor="urlInput" className="form-label">
                Or provide a URL to a zip file{' '}
                <a
                  href="#"
                  onClick={(e) => {
                    e.preventDefault();
                    setUrl('https://ipt.obis.org/secretariat/archive.do?r=edna-wadden-sea&v=2.0');
                    setFiles([]);
                    // Clear the file input element
                    const fileInput = document.getElementById('fileUpload');
                    if (fileInput) {
                      fileInput.value = '';
                    }
                    setUploadResult(null);
                    setUploadError(null);
                  }}
                  className="text-decoration-none"
                >
                  (example)
                </a>
              </label>
              <input
                id="urlInput"
                type="url"
                className="form-control"
                placeholder=""
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
              {loading ? 'Analyzing...' : 'Upload'}
            </button>
          </form>
          {loading && (
            <div className="mt-3">
              <div className="spinner-border text-primary me-2" role="status">
                <span className="visually-hidden">Loading...</span>
              </div>
              <span>Running analysis...</span>
            </div>
          )}
          {uploadError && (
            <div className="alert alert-danger mt-3">
              {uploadError}
            </div>
          )}
          {uploadResult && (
            <div className="mt-4">
              {uploadResult.processing?.analysis_error && (
                <div className="alert alert-warning">
                  {uploadResult.processing.analysis_error}
                </div>
              )}
              {uploadResult.processing?.analyzed_occurrences && (
                <div className="mt-5">
                  <div className="d-flex justify-content-between align-items-center mb-2">
                    <h5 className="mb-0">Analysis Results</h5>
                    <div>
                      <button
                        type="button"
                        className="btn btn-outline-primary btn-sm me-2"
                        onClick={handleDownloadAnnotations}
                        disabled={Object.keys(annotations).length === 0}
                      >
                        Download annotations
                      </button>
                      <button
                        type="button"
                        className="btn btn-outline-success btn-sm me-2"
                        onClick={handleDownloadAnnotationsOldFormat}
                        disabled={Object.keys(annotations).length === 0}
                      >
                        Download annotations (old format)
                      </button>
                      <button
                        type="button"
                        className="btn btn-outline-danger btn-sm"
                        onClick={handleClearAnnotations}
                      >
                        Clear annotations
                      </button>
                    </div>
                  </div>
                  <div className="table-responsive">
                    <table className="table table-striped">
                      <thead className="">
                        <tr>
                          <th>Species</th>
                          <th>Phylum</th>
                          <th>Class</th>
                          <th>AphiaID</th>
                          <th>Coordinates</th>
                          <th>Density</th>
                          <th>Suitability</th>
                          <th>Annotation</th>
                          <th>Alternative</th>
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
                          .map((occurrence, index) => {
                            const rowKey = getAnnotationKey(occurrence);
                            const annotationData = annotations[rowKey] || { annotation: '', alternative: '', comments: '' };
                            const annotationValue = typeof annotationData === 'string' 
                              ? annotationData 
                              : annotationData.annotation || '';
                            const alternativeValue = typeof annotationData === 'object' 
                              ? (annotationData.alternative || '') 
                              : '';
                            const commentsValue = typeof annotationData === 'object' 
                              ? (annotationData.comments || '') 
                              : '';
                            return (
                          <tr key={index}>
                            <td>
                              {occurrence.aphiaid ? (
                                <a
                                  href={`https://obis.org/taxon/${occurrence.aphiaid}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {occurrence.scientificName || occurrence.aphiaid}
                                </a>
                              ) : (
                                occurrence.scientificName || '-'
                              )}
                            </td>
                            <td>{occurrence.phylum || '-'}</td>
                            <td>{occurrence.class || '-'}</td>
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
                                '-'
                              )}
                            </td>
                            <td>
                              {occurrence.decimalLongitude !== null &&
                              occurrence.decimalLongitude !== undefined &&
                              occurrence.decimalLatitude !== null &&
                              occurrence.decimalLatitude !== undefined ? (
                                <a
                                  href={`https://wktmap.com/?wkt=${encodeURIComponent(
                                    `POINT (${occurrence.decimalLongitude} ${occurrence.decimalLatitude})`
                                  )}`}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                >
                                  {`${occurrence.decimalLongitude}, ${occurrence.decimalLatitude}`}
                                </a>
                              ) : (
                                '-'
                              )}
                            </td>
                            <td>
                              {occurrence.density !== null && occurrence.density !== undefined ? (
                                <span style={getBadgeStyle(occurrence.density, interpolateSpectral)}>
                                  {Number(occurrence.density).toFixed(4)}
                                </span>
                              ) : (
                                '-'
                              )}
                            </td>
                            <td>
                              {occurrence.suitability !== null && occurrence.suitability !== undefined ? (
                                <span style={getBadgeStyle(occurrence.suitability, interpolateSpectral)}>
                                  {Number(occurrence.suitability).toFixed(4)}
                                </span>
                              ) : (
                                '-'
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
                                <option value="">-</option>
                                <option value="accept">Accept</option>
                                <option value="reject">Reject</option>
                              </select>
                            </td>
                            <td>
                              <input
                                type="text"
                                className="form-control form-control-sm"
                                value={alternativeValue}
                                onChange={(e) =>
                                  handleAnnotationChange(rowKey, 'alternative', e.target.value)
                                }
                                placeholder=""
                              />
                            </td>
                            <td>
                              <input
                                type="text"
                                className="form-control form-control-sm"
                                value={commentsValue}
                                onChange={(e) =>
                                  handleAnnotationChange(rowKey, 'comments', e.target.value)
                                }
                                placeholder=""
                              />
                            </td>
                          </tr>
                        );})}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;

