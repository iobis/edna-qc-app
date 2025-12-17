import React, { useState, useEffect } from 'react';
import axios from 'axios';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const ALLOWED_EXTENSIONS = ['.txt', '.csv', '.tsv'];

function App() {
  const [status, setStatus] = useState('loading');
  const [files, setFiles] = useState([]);
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
    if (!files.length) {
      return;
    }

    setLoading(true);

    const formData = new FormData();
    files.forEach((file) => {
      formData.append('files', file);
    });

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
          <h1 className="mb-4">Geographic outlier analysis</h1>
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
            <button
              type="submit"
              className="btn btn-primary"
              disabled={!files.length || loading}
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
                          <th>Longitude</th>
                          <th>Latitude</th>
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
                            <td>{occurrence.decimalLongitude !== null && occurrence.decimalLongitude !== undefined ? occurrence.decimalLongitude : '-'}</td>
                            <td>{occurrence.decimalLatitude !== null && occurrence.decimalLatitude !== undefined ? occurrence.decimalLatitude : '-'}</td>
                            <td>
                              {occurrence.density !== null && occurrence.density !== undefined
                                ? Number(occurrence.density).toFixed(4)
                                : '-'}
                            </td>
                            <td>
                              {occurrence.suitability !== null && occurrence.suitability !== undefined
                                ? Number(occurrence.suitability).toFixed(4)
                                : '-'}
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

