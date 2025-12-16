import React, { useState, useEffect } from 'react';
import axios from 'axios';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

const ALLOWED_EXTENSIONS = ['.txt', '.csv', '.tsv'];

function App() {
  const [status, setStatus] = useState('loading');
  const [files, setFiles] = useState([]);
  const [uploadResult, setUploadResult] = useState(null);
  const [uploadError, setUploadError] = useState(null);

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
    }
  };

  return (
    <div className="container mt-5">
      <div className="row">
        <div className="col-md-12">
          <h1 className="mb-4">Web Application</h1>
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
              />
            </div>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={!files.length}
            >
              Upload
            </button>
          </form>
          {uploadError && (
            <div className="alert alert-danger mt-3">
              {uploadError}
            </div>
          )}
          {uploadResult && (
            <div className="alert alert-success mt-3">
              <div>Files received: {uploadResult.files_received}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;

