import React from 'react';

const FileBrowser = ({ 
  files, 
  selectedFiles, 
  setSelectedFiles, 
  expandedFolders, 
  setExpandedFolders 
}) => {
  // Helper function to get folder structure from file paths
  const getFolderStructure = (files) => {
    const structure = {};
    
    files.forEach(file => {
      const parts = file.split('/');
      let current = structure;
      
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        
        if (i === parts.length - 1) {
          // It's a file
          current[part] = file;
        } else {
          // It's a directory
          if (!current[part]) {
            current[part] = {};
          }
          current = current[part];
        }
      }
    });
    
    return structure;
  };
  
  // Count files in a folder structure
  const countFilesInFolder = (obj) => {
    return Object.entries(obj).reduce((count, [_, val]) => {
      if (typeof val === 'string') {
        return count + 1;
      } else {
        return count + countFilesInFolder(val);
      }
    }, 0);
  };
  
  // Get all file paths in a folder structure
  const getFilePaths = (obj, result = []) => {
    Object.entries(obj).forEach(([_, val]) => {
      if (typeof val === 'string') {
        result.push(val);
      } else {
        getFilePaths(val, result);
      }
    });
    return result;
  };

  // Helper function to toggle folder expansion
  const toggleFolder = (folderPath) => {
    setExpandedFolders(prev => ({
      ...prev,
      [folderPath]: !prev[folderPath]
    }));
  };

  // Helper function to render folder structure recursively
  const renderFolderTree = (structure, path = "") => {
    return Object.entries(structure).map(([key, value]) => {
      if (typeof value === 'string') {
        // It's a file
        return (
          <li key={value} 
            className={`file-item ${selectedFiles.has(value) ? 'selected' : ''}`}
            onClick={() => handleFileSelect(value)}>
            <input
              type="checkbox"
              className="file-checkbox"
              id={`file-${value}`}
              checked={selectedFiles.has(value)}
              onChange={(e) => e.stopPropagation()}
            />
            <span className="file-name">{key}</span>
          </li>
        );
      } else {
        // It's a folder
        const currentPath = path ? `${path}/${key}` : key;
        
        // Get expanded state from top-level state
        const isExpanded = expandedFolders[currentPath] === undefined 
          ? true 
          : expandedFolders[currentPath];
        
        // Count files in this folder
        const filesInFolder = countFilesInFolder(value);
        
        // Get all file paths in this folder for select/deselect operations
        const filePaths = [];
        getFilePaths(value, filePaths);
        
        // Handle folder selection (select/deselect all files in folder)
        const handleFolderSelect = () => {
          const newSelectedFiles = new Set(selectedFiles);
          
          // Check if all files in the folder are already selected
          const allSelected = filePaths.every(file => selectedFiles.has(file));
          
          if (allSelected) {
            // Deselect all files in the folder
            filePaths.forEach(file => {
              newSelectedFiles.delete(file);
            });
          } else {
            // Select all files in the folder
            filePaths.forEach(file => {
              newSelectedFiles.add(file);
            });
          }
          
          setSelectedFiles(newSelectedFiles);
        };
        
        return (
          <li key={currentPath} className="folder-item">
            <div 
              className={`folder-header ${isExpanded ? 'expanded' : ''}`}
              onClick={handleFolderSelect}
            >
              <button 
                className={`folder-toggle ${isExpanded ? 'expanded' : ''}`}
                onClick={(e) => {
                  e.stopPropagation();
                  toggleFolder(currentPath);
                }}
                aria-label={isExpanded ? 'Collapse folder' : 'Expand folder'}
                aria-expanded={isExpanded}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                  <path d="M9 18L15 12L9 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </button>
              
              <span className="folder-name">
                {key}
                <span className="folder-count">({filesInFolder})</span>
              </span>
            </div>
            
            {isExpanded && (
              <ul className="nested-folder">
                {renderFolderTree(value, currentPath)}
              </ul>
            )}
          </li>
        );
      }
    });
  };

  // Function to handle file selection
  const handleFileSelect = (filePath) => {
    const newSelectedFiles = new Set(selectedFiles);
    if (newSelectedFiles.has(filePath)) {
      newSelectedFiles.delete(filePath);
    } else {
      newSelectedFiles.add(filePath);
    }
    setSelectedFiles(newSelectedFiles);
  };

  // Create folder structure from the files
  const folderStructure = getFolderStructure(files);

  return (
    <div className="file-browser">
      {files.length > 0 ? (
        <ul className="file-list">
          {Object.keys(folderStructure).length > 0 ? (
            renderFolderTree(folderStructure)
          ) : (
            // Fallback to flat list if structure is empty
            files.map((file, index) => (
              <li key={index} 
                className={`file-item ${selectedFiles.has(file) ? 'selected' : ''}`}
                onClick={() => handleFileSelect(file)}>
                <input
                  type="checkbox"
                  className="file-checkbox"
                  id={`file-${index}`}
                  checked={selectedFiles.has(file)}
                  onChange={(e) => e.stopPropagation()}
                />
                <span className="file-name">{file}</span>
              </li>
            ))
          )}
        </ul>
      ) : (
        <div className="card text-center">
          {files.length === 0 ? 'No files available' : 'No matching files found'}
        </div>
      )}
    </div>
  );
};

export default FileBrowser; 