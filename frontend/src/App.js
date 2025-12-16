import React, { useState, useEffect } from 'react';
import axios from 'axios';

const API_URL = process.env.REACT_APP_API_URL || 'http://localhost:8000';

function App() {
  const [status, setStatus] = useState('loading');

  useEffect(() => {
    axios.get(`${API_URL}/api/health`)
      .then(response => {
        setStatus(response.data.status);
      })
      .catch(error => {
        setStatus('error');
        console.error('API connection error:', error);
      });
  }, []);

  return (
    <div className="container mt-5">
      <div className="row">
        <div className="col-md-12">
          <h1 className="mb-4">Web Application</h1>
          <div className="alert alert-info">
            API Status: <strong>{status}</strong>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;

