import React from 'react';

const LoadingView = ({ apiState, statusMessage, processingProgress }) => (
  <div className="loading-container">
    <div className="spinner">
      <div className="spinner-dot"></div>
      <div className="spinner-dot"></div>
      <div className="spinner-dot"></div>
      <div className="spinner-dot"></div>
      <div className="spinner-dot"></div>
      <div className="spinner-dot"></div>
      <div className="spinner-dot"></div>
      <div className="spinner-dot"></div>
    </div>
    <h2>
      {apiState === 'fetching' 
        ? 'Fetching repository files...' 
        : 'Processing selected files...'}
    </h2>
    
    {apiState === 'processing' && processingProgress.total > 0 && (
      <div className="progress-container">
        <div className="progress-bar">
          <div 
            className="progress-fill" 
            style={{ width: `${processingProgress.percentage}%` }}
          ></div>
        </div>
        <p className="progress-text">
          Processing file {processingProgress.processed} of {processingProgress.total}
          {processingProgress.currentFile && `: ${processingProgress.currentFile}`}
        </p>
      </div>
    )}
    
    <p>{statusMessage}</p>
  </div>
);

export default LoadingView; 