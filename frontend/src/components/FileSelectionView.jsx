import React, { useState } from 'react';
import FileBrowser from './FileBrowser';

const FileSelectionView = ({ 
  files, 
  selectedFiles, 
  setSelectedFiles, 
  fileSearchTerm, 
  setFileSearchTerm, 
  onProcessSelectedFiles,
  statusMessage
}) => {
  const [expandedFolders, setExpandedFolders] = useState({});
  
  const handleSelectAll = () => {
    const filteredFiles = fileSearchTerm
      ? files.filter(file => file.toLowerCase().includes(fileSearchTerm.toLowerCase()))
      : files;

    if (selectedFiles.size === filteredFiles.length) {
      setSelectedFiles(new Set());
    } else {
      setSelectedFiles(new Set(filteredFiles));
    }
  };

  const filteredFiles = fileSearchTerm
    ? files.filter(file => file.toLowerCase().includes(fileSearchTerm.toLowerCase()))
    : files;

  return (
    <div className="file-selection-container">
      <div className="selection-header">
        <h2>Select Files to Analyze</h2>
        <div className="file-search">
          <input
            type="text"
            className="repo-input"
            placeholder="Search files..."
            value={fileSearchTerm}
            onChange={(e) => setFileSearchTerm(e.target.value)}
          />
          {fileSearchTerm && (
            <button 
              className="btn" 
              onClick={() => setFileSearchTerm('')}
              aria-label="Clear search"
              style={{ position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', padding: '4px' }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                <path d="M18 6L6 18M6 6L18 18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </button>
          )}
        </div>
      </div>
      
      {files.length === 0 && (
        <div className="card text-center">
          <h3>No files found in repository</h3>
          <p>This could be due to:</p>
          <ul style={{ textAlign: 'left', marginBottom: 'var(--space-4)' }}>
            <li>The repository is empty</li>
            <li>Files may be in nested directories not visible</li>
            <li>There was an error retrieving files</li>
          </ul>
          <button onClick={() => setUiState('welcome')} className="btn btn-primary">
            Go Back
          </button>
        </div>
      )}
      
      {files.length > 0 && (
        <>
          <div className="file-controls">
            <button onClick={handleSelectAll} className="btn btn-secondary">
              {selectedFiles.size === filteredFiles.length && filteredFiles.length > 0 
                ? 'Deselect All' 
                : 'Select All'}
            </button>
            <span className="file-count">
              {selectedFiles.size} file{selectedFiles.size !== 1 ? 's' : ''} selected
            </span>
          </div>
          
          <div className="file-browser">
            <FileBrowser 
              files={filteredFiles}
              selectedFiles={selectedFiles}
              setSelectedFiles={setSelectedFiles}
              expandedFolders={expandedFolders}
              setExpandedFolders={setExpandedFolders}
            />
          </div>
          
          <div className="mt-auto">
            <button 
              onClick={onProcessSelectedFiles} 
              disabled={selectedFiles.size === 0}
              className="btn btn-primary w-full"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ marginRight: '0.5rem' }}>
                <path d="M9 16.2L4.8 12L3.4 13.4L9 19L21 7L19.6 5.6L9 16.2Z" fill="currentColor"/>
              </svg>
              Analyze {selectedFiles.size} Selected File{selectedFiles.size !== 1 ? 's' : ''}
            </button>
          </div>
        </>
      )}
    </div>
  );
};

export default FileSelectionView; 