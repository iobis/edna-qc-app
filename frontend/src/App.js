import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { interpolateSpectral } from 'd3-scale-chromatic';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const ALLOWED_EXTENSIONS = ['.txt', '.csv', '.tsv'];

// Generic badge style function for any column
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
    setUploadResult(null);
    setUploadError(null);
  };

  const handleUpload = async (event) => {
    event.preventDefault();
    if (!files.length && !url.trim()) {
      return;
    }

    setLoading(true);

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
      <div className="row">
        <div className="col-md-12">
          <h1 className="mb-2">Geographic outlier analysis</h1>
        </div>
      </div>
      <div className="row mt-4">
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
                accept=".txt,.csv,.tsv"
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
                onChange={(e) => setUrl(e.target.value)}
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
            <div className="mt-3">
              {uploadResult.processing?.analysis_error && (
                <div className="alert alert-warning">
                  {uploadResult.processing.analysis_error}
                </div>
              )}
              {uploadResult.processing?.analyzed_occurrences && (
                <div className="mt-4">
                  <h5>Analysis Results</h5>
                  <div className="table-responsive">
                    <table className="table table-striped">
                      <thead className="">
                        <tr>
                          <th>Species</th>
                          <th>AphaID</th>
                          <th>Coordinates</th>
                          <th>Density</th>
                          <th>Suitability</th>
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
                          .map((occurrence, index) => (
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
                          </tr>
                        ))}
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

