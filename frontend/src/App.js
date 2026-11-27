import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import axios from 'axios';
import { interpolateSpectral } from 'd3-scale-chromatic';
import DensityMap from './DensityMap';
import CoordinatePopover from './CoordinatePopover';
import ScoreDotPlot from './ScoreDotPlot';
import './App.css';

// Use relative URL if REACT_APP_API_URL is empty, otherwise use the provided URL
// Empty string means use relative URLs (works with nginx proxy)
const API_URL = process.env.REACT_APP_API_URL || '';
const MAP_GEOMETRY_VERSION = 8;

const ALLOWED_EXTENSIONS = ['.txt', '.csv', '.tsv', '.zip', '.xlsx'];

// Accept/reject and comments stay in the codebase, but are not shown or saved.
const ANNOTATIONS_ENABLED = false;

const genesForOccurrence = (occurrence) => {
  if (!occurrence?.target_gene) {
    return [];
  }
  return occurrence.target_gene
    .split(',')
    .map((gene) => gene.trim())
    .filter(Boolean);
};

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

function SequenceBlock({ sequence, gene }) {
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

  const title = gene ? `DNA sequence (${gene})` : 'DNA sequence';

  return (
    <div className="expand-sequence">
      <div className="expand-sequence-header">
        <div className="expand-sequence-title">
          <span>{title}</span>
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

function SequenceBlocks({ occurrence }) {
  const markers = Array.isArray(occurrence?.dna_markers)
    ? occurrence.dna_markers.filter((marker) => marker?.DNA_sequence)
    : [];

  if (markers.length > 0) {
    return markers.map((marker, index) => (
      <SequenceBlock
        key={`${marker.target_gene || 'sequence'}-${index}`}
        sequence={marker.DNA_sequence}
        gene={marker.target_gene || undefined}
      />
    ));
  }

  return <SequenceBlock sequence={occurrence?.DNA_sequence} />;
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
  const [statusMessage, setStatusMessage] = useState(null);
  const [annotations, setAnnotations] = useState({});
  const [annotationsLoaded, setAnnotationsLoaded] = useState(false);
  const [expandedRow, setExpandedRow] = useState(null);
  const [mapCache, setMapCache] = useState({});
  const [recordsCache, setRecordsCache] = useState({});
  const [mapLoading, setMapLoading] = useState(null);
  const [mapErrors, setMapErrors] = useState({});
  const [plotReturn, setPlotReturn] = useState(null);
  const plotScrollRef = useRef(null);
  const [phylumFilter, setPhylumFilter] = useState('');
  const [classFilter, setClassFilter] = useState('');
  const [geneFilter, setGeneFilter] = useState('');

  const ANNOTATIONS_STORAGE_KEY = 'occurrenceAnnotations';

  const getAnnotationKey = (occurrence) => {
    const aphiaid = occurrence.aphiaid ?? 'na';
    const lon = occurrence.decimalLongitude ?? 'na';
    const lat = occurrence.decimalLatitude ?? 'na';
    return `${aphiaid}|${lon}|${lat}`;
  };

  const COLUMN_COUNT = ANNOTATIONS_ENABLED ? 11 : 9;

  const analyzedOccurrences = uploadResult?.processing?.analyzed_occurrences || [];

  const phylumOptions = useMemo(() => {
    const values = new Set();
    analyzedOccurrences.forEach((occurrence) => {
      if (occurrence.phylum) {
        values.add(occurrence.phylum);
      }
    });
    return Array.from(values).sort((a, b) => a.localeCompare(b));
  }, [analyzedOccurrences]);

  const classOptions = useMemo(() => {
    const values = new Set();
    analyzedOccurrences.forEach((occurrence) => {
      if (phylumFilter && occurrence.phylum !== phylumFilter) {
        return;
      }
      if (occurrence.class) {
        values.add(occurrence.class);
      }
    });
    return Array.from(values).sort((a, b) => a.localeCompare(b));
  }, [analyzedOccurrences, phylumFilter]);

  const geneOptions = useMemo(() => {
    const values = new Set();
    analyzedOccurrences.forEach((occurrence) => {
      if (phylumFilter && occurrence.phylum !== phylumFilter) {
        return;
      }
      if (classFilter && occurrence.class !== classFilter) {
        return;
      }
      genesForOccurrence(occurrence).forEach((gene) => values.add(gene));
    });
    return Array.from(values).sort((a, b) => a.localeCompare(b));
  }, [analyzedOccurrences, phylumFilter, classFilter]);

  const filteredOccurrences = useMemo(() => {
    return analyzedOccurrences
      .filter((occurrence) => {
        if (phylumFilter && occurrence.phylum !== phylumFilter) {
          return false;
        }
        if (classFilter && occurrence.class !== classFilter) {
          return false;
        }
        if (geneFilter && !genesForOccurrence(occurrence).includes(geneFilter)) {
          return false;
        }
        return true;
      })
      .slice()
      .sort((a, b) => {
        const da = a.density ?? Infinity;
        const db = b.density ?? Infinity;
        return da - db;
      });
  }, [analyzedOccurrences, phylumFilter, classFilter, geneFilter]);

  const geneStillValid = (gene, phylum, className) => {
    if (!gene) {
      return true;
    }
    return analyzedOccurrences.some((occurrence) => {
      if (phylum && occurrence.phylum !== phylum) {
        return false;
      }
      if (className && occurrence.class !== className) {
        return false;
      }
      return genesForOccurrence(occurrence).includes(gene);
    });
  };

  const handlePhylumFilter = (value) => {
    const nextClass = !value || !classFilter || analyzedOccurrences.some(
      (occurrence) => occurrence.phylum === value && occurrence.class === classFilter
    )
      ? classFilter
      : '';
    setPhylumFilter(value);
    setClassFilter(nextClass);
    setGeneFilter((current) => (geneStillValid(current, value, nextClass) ? current : ''));
  };

  const handleClassFilter = (value) => {
    setClassFilter(value);
    setGeneFilter((current) => (geneStillValid(current, phylumFilter, value) ? current : ''));
  };

  useEffect(() => {
    setPhylumFilter('');
    setClassFilter('');
    setGeneFilter('');
  }, [uploadResult]);

  useEffect(() => {
    if (!ANNOTATIONS_ENABLED) {
      return;
    }
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
    if (!ANNOTATIONS_ENABLED || !annotationsLoaded) {
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
            order: occurrence?.order || null,
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

  const formatErrorDetail = (error) => {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) {
      return detail;
    }
    if (Array.isArray(detail)) {
      return detail
        .map((item) => (typeof item === 'string' ? item : item?.msg || JSON.stringify(item)))
        .join('; ');
    }
    if (detail && typeof detail === 'object') {
      return detail.msg || JSON.stringify(detail);
    }
    return error.message || 'Upload failed';
  };

  const fallbackJobMessage = (data) => {
    if (!data) {
      return 'Starting…';
    }
    if (data.status === 'queued') {
      const ahead = typeof data.position === 'number' ? data.position : null;
      const queueSize = typeof data.queue_size === 'number' ? data.queue_size : null;
      if (ahead != null && queueSize != null) {
        if (ahead <= 0) {
          return queueSize <= 1
            ? 'Waiting in queue…'
            : `Next in queue (${queueSize} in queue)…`;
        }
        const aheadLabel = ahead === 1 ? '1 job' : `${ahead} jobs`;
        return `Waiting in queue (${aheadLabel} ahead, ${queueSize} in queue)…`;
      }
      if (data.message) {
        return data.message;
      }
      return 'Waiting in queue…';
    }
    if (data.message) {
      return data.message;
    }
    if (data.status === 'running') {
      if (data.files?.some((file) => file.pending_download)) {
        return 'Downloading…';
      }
      if (data.files?.some((file) => file.pending_extract)) {
        return 'Extracting archive…';
      }
      return 'Running analysis…';
    }
    return 'Working…';
  };

  const pollJobUntilDone = async (jobId) => {
    while (true) {
      const response = await axios.get(`${API_URL}/api/jobs/${jobId}`);
      const data = response.data;
      setJobStatus(data);
      setStatusMessage(fallbackJobMessage(data));

      if (data.status === 'completed') {
        return unwrapJobResult(data);
      }
      if (data.status === 'failed') {
        throw new Error(data.error || 'Analysis failed');
      }

      await sleep(1000);
    }
  };

  const runAnalysis = useCallback(async (urlValue, fileList) => {
    const trimmedUrl = urlValue.trim();
    if (!fileList.length && !trimmedUrl) {
      return;
    }

    const hasFiles = fileList.length > 0;
    setLoading(true);
    setUploadResult(null);
    setUploadError(null);
    setJobStatus(null);
    setStatusMessage(hasFiles ? 'Preparing upload…' : 'Creating job…');
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
        onUploadProgress: (event) => {
          if (!hasFiles) {
            return;
          }
          if (!event.total) {
            setStatusMessage('Uploading file…');
            return;
          }
          const pct = Math.min(100, Math.round((event.loaded / event.total) * 100));
          if (pct >= 100) {
            setStatusMessage('Creating job…');
          } else {
            setStatusMessage(`Uploading file… ${pct}%`);
          }
        },
      });
      const data = response.data;

      if (data.status === 'completed') {
        setJobStatus(data);
        setStatusMessage(data.cached ? 'Loading cached results…' : 'Loading results…');
        setUploadResult(unwrapJobResult(data));
        setUploadError(null);
        return;
      }

      if (!data.job_id) {
        throw new Error('Server did not return a job id');
      }

      setJobStatus(data);
      setStatusMessage(fallbackJobMessage(data));
      const result = await pollJobUntilDone(data.job_id);
      setStatusMessage('Loading results…');
      setUploadResult(result);
      setUploadError(null);
    } catch (error) {
      console.error('Upload error:', error);
      setUploadError(formatErrorDetail(error));
      setUploadResult(null);
    } finally {
      setLoading(false);
      setJobStatus(null);
      setStatusMessage(null);
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

  const openOccurrenceRow = async (rowKey, occurrence) => {
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

  const toggleRowExpand = (rowKey, occurrence) => {
    if (expandedRow === rowKey) {
      setExpandedRow(null);
      return;
    }
    void openOccurrenceRow(rowKey, occurrence);
  };

  useEffect(() => {
    if (!plotReturn || expandedRow === plotReturn.rowKey) {
      return;
    }
    plotScrollRef.current = null;
    setPlotReturn(null);
  }, [expandedRow, plotReturn]);

  const revealOccurrenceFromPlot = (occurrence) => {
    const rowKey = getAnnotationKey(occurrence);
    if (plotScrollRef.current == null) {
      plotScrollRef.current = window.scrollY;
    }
    setPlotReturn({ scrollY: plotScrollRef.current, rowKey });
    if (expandedRow !== rowKey) {
      void openOccurrenceRow(rowKey, occurrence);
    }
    window.requestAnimationFrame(() => {
      const row = document.querySelector(
        `[data-row-key="${window.CSS.escape(rowKey)}"]`
      );
      row?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  };

  const returnToPlot = () => {
    const top = plotScrollRef.current ?? plotReturn?.scrollY;
    plotScrollRef.current = null;
    setPlotReturn(null);
    if (top == null) {
      return;
    }
    window.scrollTo({ top, behavior: 'smooth' });
  };

  return (
    <div className="app">
      <header className="header">
        <h1>
          <a href="/" className="header-home">
            Geographic outlier detection
          </a>
        </h1>
        <div className="intro">
          <div className="intro-copy">
            <p>
              This app scores species occurrences for spatial and environmental outliers.
              Upload Darwin Core text files or a Darwin Core Archive, or point to a hosted archive with a URL.
              A typical dataset finishes within a minute.
            </p>
            <p>
              Some alternative file formats such as Wilderlab's result spreadsheet are supported as well.
            </p>
            <p>
              Click the{' '}
              <button type="button" onClick={loadExample} className="example-link">
                example
              </button>{' '}
              link to load an example dataset.
            </p>
          </div>
          <aside className="intro-fields">
            <h2>Required fields</h2>
            <ul>
              <li>
                <code className="dwca-term">scientificName</code>
              </li>
              <li>
                <code className="dwca-term">decimalLongitude</code>
                <code className="dwca-term">decimalLatitude</code>
                <span>
                  Taken from the occurrence, or inherited from the event and its parent events.
                </span>
              </li>
            </ul>
            <h2>Optional</h2>
            <ul>
              <li>
                <code className="dwca-term">scientificNameID</code>
                <span>A WoRMS LSID. Supplying it skips name matching and speeds up processing.</span>
              </li>
              <li>
                <code className="dwca-term">taxonRank</code>
                <span>Only species-level rows are scored.</span>
              </li>
            </ul>
          </aside>
        </div>
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
              accept=".txt,.csv,.tsv,.zip,.xlsx"
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
          <div className="loading" aria-live="polite">
            <div className="spinner" role="status" aria-label="Loading" />
            <div className="loading-copy">
              <span className="loading-message">
                {statusMessage || fallbackJobMessage(jobStatus)}
              </span>
              {(jobStatus?.status === 'queued' || jobStatus?.status === 'running') && (
                <span className="loading-hint">
                  {jobStatus.status === 'queued'
                    ? (typeof jobStatus.queue_size === 'number'
                      ? `${jobStatus.queue_size} in queue`
                      : 'Job queued')
                    : 'Analysis in progress'}
                </span>
              )}
            </div>
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
          {!uploadResult.processing?.analyzed_occurrences?.length && (
            <div className="alert alert-warning">
              {uploadResult.processing?.occurrence_file_found === false
                ? 'No occurrence table was found in the uploaded files.'
                : 'No species occurrences were available to analyze.'}
            </div>
          )}
          {uploadResult.processing?.analyzed_occurrences?.length > 0 && (
            <section className="results">
              <div className="results-header">
                <h2>Analysis results</h2>
                {ANNOTATIONS_ENABLED && (
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
                )}
              </div>
              <ScoreDotPlot
                occurrences={filteredOccurrences}
                onPointClick={revealOccurrenceFromPlot}
              />
              <div className="table-filters">
                <label className="table-filter">
                  <span>Phylum</span>
                  <select
                    className="form-select"
                    value={phylumFilter}
                    onChange={(event) => handlePhylumFilter(event.target.value)}
                  >
                    <option value="">All</option>
                    {phylumOptions.map((name) => (
                      <option key={name} value={name}>{name}</option>
                    ))}
                  </select>
                </label>
                <label className="table-filter">
                  <span>Class</span>
                  <select
                    className="form-select"
                    value={classFilter}
                    onChange={(event) => handleClassFilter(event.target.value)}
                  >
                    <option value="">All</option>
                    {classOptions.map((name) => (
                      <option key={name} value={name}>{name}</option>
                    ))}
                  </select>
                </label>
                <label className="table-filter table-filter-wide">
                  <span>Target gene</span>
                  <select
                    className="form-select"
                    value={geneFilter}
                    onChange={(event) => setGeneFilter(event.target.value)}
                  >
                    <option value="">All</option>
                    {geneOptions.map((name) => (
                      <option key={name} value={name}>{name}</option>
                    ))}
                  </select>
                </label>
                <span className="table-filter-count">
                  {filteredOccurrences.length.toLocaleString()} of {analyzedOccurrences.length.toLocaleString()}
                </span>
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th className="col-toggle" aria-label="Expand" />
                      <th>Species</th>
                      <th>Phylum</th>
                      <th>Class</th>
                      <th>Order</th>
                      <th>Target gene</th>
                      <th>Coordinates</th>
                      <th>Density</th>
                      <th>Suitability</th>
                      {ANNOTATIONS_ENABLED && (
                        <>
                          <th>Annotation</th>
                          <th>Comments</th>
                        </>
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredOccurrences.length === 0 && (
                      <tr>
                        <td className="table-filter-empty" colSpan={COLUMN_COUNT}>
                          No occurrences match these filters.
                        </td>
                      </tr>
                    )}
                    {filteredOccurrences.map((occurrence) => {
                        const rowKey = getAnnotationKey(occurrence);
                        const annotationData = annotations[rowKey] || { annotation: '', comments: '' };
                        const annotationValue = annotationData.annotation || '';
                        const commentsValue = annotationData.comments || '';
                        const isExpanded = expandedRow === rowKey;
                        const density = occurrence.aphiaid ? mapCache[occurrence.aphiaid] : null;
                        const records = occurrence.aphiaid ? recordsCache[occurrence.aphiaid] : undefined;
                        return (
                          <React.Fragment key={rowKey}>
                            <tr
                              data-row-key={rowKey}
                              className={isExpanded ? 'row-expanded' : undefined}
                            >
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
                            <td>{occurrence.order || <span className="empty-cell">—</span>}</td>
                            <td>{occurrence.target_gene || <span className="empty-cell">—</span>}</td>
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
                            {ANNOTATIONS_ENABLED && (
                              <>
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
                              </>
                            )}
                          </tr>
                          {isExpanded && (
                            <tr className="expand-row">
                              <td colSpan={COLUMN_COUNT}>
                                <SequenceBlocks occurrence={occurrence} />
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
      {plotReturn && (
        <button type="button" className="back-to-plot" onClick={returnToPlot}>
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <path
              d="M8 3.5 3.5 8.2M8 3.5l4.5 4.7M8 3.5V13"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
          Back to plot
        </button>
      )}
    </div>
  );
}

export default App;

