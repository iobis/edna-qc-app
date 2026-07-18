import React, { useState, useEffect, useCallback, useRef } from 'react';
import axios from 'axios';
import { interpolateSpectral } from 'd3-scale-chromatic';
import DensityMap from './DensityMap';
import CoordinatePopover from './CoordinatePopover';
import './App.css';

// Use relative URL if REACT_APP_API_URL is empty, otherwise use the provided URL
// Empty string means use relative URLs (works with nginx proxy)
const API_URL = process.env.REACT_APP_API_URL || '';
const MAP_GEOMETRY_VERSION = 7;

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

function GlobeIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="m20.893 13.393-1.135-1.135a2.252 2.252 0 0 1-.421-.585l-1.08-2.16a.414.414 0 0 0-.663-.107.827.827 0 0 1-.812.21l-1.273-.363a.89.89 0 0 0-.738 1.595l.587.39c.59.395.674 1.23.172 1.732l-.2.2c-.212.212-.33.498-.33.796v.41c0 .409-.11.809-.32 1.158l-1.315 2.191a2.11 2.11 0 0 1-1.81 1.025 1.055 1.055 0 0 1-1.055-1.055v-1.172c0-.92-.56-1.747-1.414-2.089l-.655-.261a2.25 2.25 0 0 1-1.383-2.46l.007-.042a2.25 2.25 0 0 1 .29-.787l.09-.15a2.25 2.25 0 0 1 2.37-1.048l1.178.236a1.125 1.125 0 0 0 1.302-.795l.208-.73a1.125 1.125 0 0 0-.578-1.315l-.665-.332-.091.091a2.25 2.25 0 0 1-1.591.659h-.18c-.249 0-.487.1-.662.274a.931.931 0 0 1-1.458-1.137l1.411-2.353a2.25 2.25 0 0 0 .286-.76m11.928 9.869A9 9 0 0 0 8.965 3.525m11.928 9.868A9 9 0 1 1 8.965 3.525"
      />
    </svg>
  );
}

function ClipboardIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M15.666 3.888A2.25 2.25 0 0 0 13.5 2.25h-3c-1.03 0-1.9.693-2.166 1.638m7.332 0c.055.194.084.4.084.612v0a.75.75 0 0 1-.75.75H9.75a.75.75 0 0 1-.75-.75v0c0-.212.03-.418.084-.612m7.332 0c.646.049 1.288.11 1.927.184 1.1.128 1.907 1.077 1.907 2.185V19.5a2.25 2.25 0 0 1-2.25 2.25H6.75A2.25 2.25 0 0 1 4.5 19.5V6.257c0-1.108.806-2.057 1.907-2.185a48.208 48.208 0 0 1 1.927-.184"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
    </svg>
  );
}

function SequenceBlock({ sequence }) {
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef(null);

  useEffect(() => () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
    }
  }, []);

  if (!sequence) {
    return null;
  }

  const copySequence = async () => {
    try {
      await navigator.clipboard.writeText(sequence);
      setCopied(true);
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = setTimeout(() => {
        setCopied(false);
        timeoutRef.current = null;
      }, 1800);
    } catch (error) {
      console.error('Failed to copy sequence', error);
    }
  };

  const blastUrl =
    `https://blast.ncbi.nlm.nih.gov/Blast.cgi?PROGRAM=blastn&PAGE_TYPE=BlastSearch&QUERY=${encodeURIComponent(sequence)}`;

  return (
    <div className="expand-sequence">
      <div className="expand-sequence-header">
        <div className="expand-sequence-title">
          <span>DNA sequence</span>
          <a
            className="sequence-blast-btn"
            href={blastUrl}
            target="_blank"
            rel="noopener noreferrer"
            title="Open sequence in NCBI BLAST"
          >
            BLAST
          </a>
        </div>
        {/* <button
          type="button"
          className={`sequence-action-btn${copied ? ' sequence-action-btn-done' : ''}`}
          onClick={copySequence}
          aria-label={copied ? 'Copied' : 'Copy sequence'}
          title={copied ? 'Copied' : 'Copy to clipboard'}
        >
          {copied ? <CheckIcon /> : <ClipboardIcon />}
          <span className="sequence-copy-label">{copied ? 'Copied' : 'Copy'}</span>
        </button> */}
      </div>
      <pre className="expand-sequence-body">{sequence}</pre>
    </div>
  );
}

function syncUrlQueryParam(value) {
  const params = new URLSearchParams(window.location.search);
  const trimmed = value.trim();
  if (trimmed) {
    params.set('url', trimmed);
  } else {
    params.delete('url');
  }
  const query = params.toString();
  const nextPath = query
    ? `${window.location.pathname}?${query}`
    : window.location.pathname;
  window.history.replaceState(null, '', nextPath);
}

function App() {
  const [files, setFiles] = useState([]);
  const [url, setUrl] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('url') || '';
  });
  const autoStartDone = useRef(false);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [jobStatus, setJobStatus] = useState(null);
  const [annotations, setAnnotations] = useState({});
  const [annotationsLoaded, setAnnotationsLoaded] = useState(false);
  const [expandedRow, setExpandedRow] = useState(null);
  const [mapCache, setMapCache] = useState({});
  const [recordsCache, setRecordsCache] = useState({});
  const [mapLoading, setMapLoading] = useState(null);
  const [mapErrors, setMapErrors] = useState({});

  const ANNOTATIONS_STORAGE_KEY = 'occurrenceAnnotations';

  const getAnnotationKey = (occurrence) => {
    const aphiaid = occurrence.aphiaid ?? 'na';
    const lon = occurrence.decimalLongitude ?? 'na';
    const lat = occurrence.decimalLatitude ?? 'na';
    return `${aphiaid}|${lon}|${lat}`;
  };

  const COLUMN_COUNT = 9;

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
    syncUrlQueryParam('');
    setUploadResult(null);
    setUploadError(null);
    setExpandedRow(null);
  };

  const handleUrlChange = (event) => {
    const nextUrl = event.target.value;
    setUrl(nextUrl);
    syncUrlQueryParam(nextUrl);
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

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const unwrapJobResult = (data) => (
    data.result || {
      files_received: data.files_received,
      files: data.files,
      processing: data.processing,
      cached: data.cached,
      cache_key: data.cache_key,
    }
  );

  const pollJobUntilDone = async (jobId) => {
    while (true) {
      const response = await axios.get(`${API_URL}/api/jobs/${jobId}`);
      const data = response.data;
      setJobStatus(data);

      if (data.status === 'completed') {
        return unwrapJobResult(data);
      }
      if (data.status === 'failed') {
        throw new Error(data.error || 'Analysis failed');
      }

      await sleep(1500);
    }
  };

  const runAnalysis = useCallback(async (urlValue, fileList) => {
    const trimmedUrl = urlValue.trim();
    if (!fileList.length && !trimmedUrl) {
      return;
    }

    setLoading(true);
    setUploadResult(null);
    setUploadError(null);
    setJobStatus(null);
    setExpandedRow(null);

    const formData = new FormData();
    fileList.forEach((file) => {
      formData.append('files', file);
    });
    if (trimmedUrl) {
      formData.append('url', trimmedUrl);
    }

    try {
      const response = await axios.post(`${API_URL}/api/jobs`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const data = response.data;

      if (data.status === 'completed') {
        setUploadResult(unwrapJobResult(data));
        setUploadError(null);
        return;
      }

      setJobStatus(data);
      const result = await pollJobUntilDone(data.job_id);
      setUploadResult(result);
      setUploadError(null);
    } catch (error) {
      console.error('Upload error:', error);
      const errorMessage = error.response?.data?.detail || error.message || 'Upload failed';
      setUploadError(errorMessage);
      setUploadResult(null);
    } finally {
      setLoading(false);
      setJobStatus(null);
    }
  }, []);

  useEffect(() => {
    if (autoStartDone.current) {
      return;
    }
    const params = new URLSearchParams(window.location.search);
    const urlParam = params.get('url')?.trim();
    if (!urlParam) {
      return;
    }
    autoStartDone.current = true;
    runAnalysis(urlParam, []);
  }, [runAnalysis]);

  const handleUpload = async (event) => {
    event.preventDefault();
    await runAnalysis(url, files);
  };

  const loadExample = () => {
    const exampleUrl = 'https://ipt.obis.org/secretariat/archive.do?r=edna-wadden-sea&v=2.0';
    setUrl(exampleUrl);
    syncUrlQueryParam(exampleUrl);
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

    const aphiaid = occurrence.aphiaid;
    if (!aphiaid) {
      return;
    }

    const cachedDensity = mapCache[aphiaid];
    if (cachedDensity && aphiaid in recordsCache) {
      return;
    }

    const fetchRecords = async () => {
      try {
        const params = new URLSearchParams({ v: String(MAP_GEOMETRY_VERSION) });
        const response = await axios.get(
          `${API_URL}/api/density-map/${aphiaid}/records?${params.toString()}`
        );
        setRecordsCache((prev) => (
          aphiaid in prev ? prev : { ...prev, [aphiaid]: response.data }
        ));
      } catch (error) {
        console.error('Failed to load speciesgrids records:', error);
        setRecordsCache((prev) => (
          aphiaid in prev
            ? prev
            : { ...prev, [aphiaid]: { type: 'FeatureCollection', features: [] } }
        ));
      }
    };

    if (cachedDensity) {
      if (!(aphiaid in recordsCache)) {
        void fetchRecords();
      }
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
      const response = await axios.get(
        `${API_URL}/api/density-map/${aphiaid}?${params.toString()}`
      );
      setMapCache((prev) => ({ ...prev, [aphiaid]: response.data }));
      setMapErrors((prev) => {
        const next = { ...prev };
        delete next[rowKey];
        return next;
      });
      void fetchRecords();
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
          Datasets should include coordinates in the <code className="dwca-term">decimalLongitude</code> and <code className="dwca-term">decimalLatitude</code> columns.
          For Event Core archives, coordinates may live on the event table (or parent events via <code className="dwca-term">parentEventID</code>) and are inherited when the occurrence lacks them.
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
            <span>
              {jobStatus?.status === 'queued' && jobStatus.position > 0
                ? `Queued (position ${jobStatus.position + 1})…`
                : jobStatus?.status === 'queued'
                  ? 'Queued…'
                  : jobStatus?.status === 'running'
                    ? 'Processing…'
                    : 'Submitting…'}
            </span>
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
                        const density = occurrence.aphiaid ? mapCache[occurrence.aphiaid] : null;
                        const records = occurrence.aphiaid ? recordsCache[occurrence.aphiaid] : undefined;
                        return (
                          <React.Fragment key={rowKey}>
                            <tr className={isExpanded ? 'row-expanded' : undefined}>
                              <td className="col-toggle">
                                {occurrence.aphiaid ? (
                                  <button
                                    type="button"
                                    className="row-toggle row-toggle-globe"
                                    onClick={() => toggleRowExpand(rowKey, occurrence)}
                                    aria-expanded={isExpanded}
                                    aria-label={isExpanded ? 'Hide density map' : 'Show density map'}
                                  >
                                    <GlobeIcon />
                                  </button>
                                ) : null}
                              </td>
                            <td className="species">
                              {occurrence.aphiaid ? (
                                <button
                                  type="button"
                                  className="species-toggle"
                                  onClick={() => toggleRowExpand(rowKey, occurrence)}
                                  aria-expanded={isExpanded}
                                >
                                  {occurrence.scientificName || occurrence.aphiaid}
                                </button>
                              ) : (
                                occurrence.scientificName || <span className="empty-cell">—</span>
                              )}
                            </td>
                            <td>{occurrence.phylum || <span className="empty-cell">—</span>}</td>
                            <td>{occurrence.class || <span className="empty-cell">—</span>}</td>
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
                                <SequenceBlock sequence={occurrence.DNA_sequence} />
                                {mapLoading === rowKey && (
                                  <div className="density-map-loading">
                                    <div className="spinner" role="status" aria-label="Loading map" />
                                    <span>Loading density map…</span>
                                  </div>
                                )}
                                {mapErrors[rowKey] && (
                                  <div className="density-map-error">{mapErrors[rowKey]}</div>
                                )}
                                {density && mapLoading !== rowKey && !mapErrors[rowKey] && (
                                  <DensityMap
                                    geojson={density}
                                    records={records}
                                    aphiaid={occurrence.aphiaid}
                                    scientificName={occurrence.scientificName}
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

