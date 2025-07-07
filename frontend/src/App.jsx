import React, { useState, useEffect, useRef, useContext } from 'react';
import axios from 'axios';
import { ThemeContext } from './ThemeContext';
import ThemeToggle from './components/ThemeToggle';
import './App.css';
import Header from './components/Header';
import WelcomeView from './components/WelcomeView';
import LoadingView from './components/LoadingView';
import FileSelectionView from './components/FileSelectionView';
import ChatView from './components/ChatView';

const webname = "Analyo";

const API_STATUS = {
  IDLE: 'idle',
  FETCHING: 'fetching',
  PROCESSING: 'processing',
  READY: 'ready',
  ERROR: 'error',
};

function App() {
  const { theme } = useContext(ThemeContext);
  const [expandedFolders, setExpandedFolders] = useState({});
  const [githubUrl, setGithubUrl] = useState('');
  const [uiState, setUiState] = useState('welcome'); // welcome, loading, file-selection, chat
  const [statusMessage, setStatusMessage] = useState('');
  const [apiState, setApiState] = useState(API_STATUS.IDLE);
  
  // State for file selection
  const [files, setFiles] = useState([]);
  const [selectedFiles, setSelectedFiles] = useState(new Set());
  const [repoId, setRepoId] = useState(null);

  const [chatQuery, setChatQuery] = useState('');
  const [chatHistory, setChatHistory] = useState([]);
  const [isChatLoading, setIsChatLoading] = useState(false);
  const chatEndRef = useRef(null);
  const [fileSearchTerm, setFileSearchTerm] = useState('');
  const [isHeaderVisible, setIsHeaderVisible] = useState(true);
  const prevScrollPos = useRef(0);
  
  // State for the confirmation dialog
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  const [isClearing, setIsClearing] = useState(false);

  const [processingProgress, setProcessingProgress] = useState({
    total: 0,
    processed: 0,
    percentage: 0,
    currentFile: ''
  });

  const BACKEND_BASE_URL = 'http://localhost:5000/api';
  const RAG_API_URL = 'http://localhost:5001/api/chat';
  const CLEAR_DB_URL = 'http://localhost:5001/api/clear-database';

  // Scroll to bottom whenever chat history changes
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatHistory]);

  // Handle scroll in chat history to hide/show header
  useEffect(() => {
    const handleScroll = (e) => {
      if (!e.target.classList.contains('chat-history')) return;
      
      const currentScrollPos = e.target.scrollTop;
      const isScrollingDown = currentScrollPos > prevScrollPos.current && currentScrollPos > 50;
      
      if (isScrollingDown !== !isHeaderVisible) {
        setIsHeaderVisible(!isScrollingDown);
      }
      
      prevScrollPos.current = currentScrollPos;
    };
    
    const chatHistory = document.querySelector('.chat-history');
    if (chatHistory) {
      chatHistory.addEventListener('scroll', handleScroll);
    }
    
    return () => {
      const chatHistory = document.querySelector('.chat-history');
      if (chatHistory) {
        chatHistory.removeEventListener('scroll', handleScroll);
      }
    };
  }, [isHeaderVisible]);

  // Handle the "Start New" button click
  const handleStartNewClick = () => {
    setShowConfirmDialog(true);
  };

  // Handle confirmation dialog confirmation
  const handleConfirmStartNew = async () => {
    setIsClearing(true);
    try {
      // Make API call to clear database
      await axios.post(CLEAR_DB_URL);
      
      // Reset all state
      setGithubUrl('');
      setUiState('welcome');
      setStatusMessage('');
      setApiState(API_STATUS.IDLE);
      setFiles([]);
      setSelectedFiles(new Set());
      setRepoId(null);
      setChatHistory([]);
      setChatQuery('');
      setProcessingProgress({
        total: 0,
        processed: 0,
        percentage: 0,
        currentFile: ''
      });
      console.log("Application state reset successfully");
    } catch (error) {
      console.error('Error clearing database:', error);
      setChatHistory(prev => [...prev, { 
        sender: 'ai', 
        text: `Error clearing database: ${error.message}. Please try again.` 
      }]);
    } finally {
      setShowConfirmDialog(false);
      setIsClearing(false);
    }
  };

  // Handle confirmation dialog cancellation
  const handleCancelStartNew = () => {
    setShowConfirmDialog(false);
  };

  const handleSubmitRepo = async (e) => {
    e.preventDefault();
    if (!githubUrl.trim()) return;

    setUiState('loading');
    setApiState(API_STATUS.FETCHING);
    setStatusMessage('Fetching repository files...');
    setChatHistory([]);
    setFiles([]);
    setSelectedFiles(new Set());

    try {
      const fetchResponse = await axios.post(`${BACKEND_BASE_URL}/fetch-repo-files`, {
        github_url: githubUrl,
      });

      if (!fetchResponse.data.success) {
        throw new Error(fetchResponse.data.message || 'Failed to fetch files.');
      }
      
      setFiles(fetchResponse.data.files);
      setRepoId(fetchResponse.data.repo_id);
      setStatusMessage('Please select files to process.');
      setApiState(API_STATUS.IDLE);
      setUiState('file-selection');

    } catch (error) {
      console.error('Error fetching repository:', error);
      setStatusMessage(`Error: ${error.message}`);
      setApiState(API_STATUS.ERROR);
      setUiState('welcome'); // Revert to welcome on error
    }
  };

  const handleFileSelect = (filePath) => {
    const newSelectedFiles = new Set(selectedFiles);
    if (newSelectedFiles.has(filePath)) {
      newSelectedFiles.delete(filePath);
    } else {
      newSelectedFiles.add(filePath);
    }
    setSelectedFiles(newSelectedFiles);
  };

  const handleSelectAll = () => {
    if (selectedFiles.size === filteredFiles.length) {
      setSelectedFiles(new Set()); // Deselect all
    } else {
      setSelectedFiles(new Set(filteredFiles)); // Select all filtered files
    }
  };
  
  const handleProcessSelectedFiles = async () => {
    if (selectedFiles.size === 0) {
      setStatusMessage('Please select at least one file.');
      return;
    }

    setUiState('loading');
    setApiState(API_STATUS.PROCESSING);
    setStatusMessage(`Processing ${selectedFiles.size} selected files...`);
    
    // Initialize progress with known values
    setProcessingProgress({
      total: selectedFiles.size,
      processed: 0,
      percentage: 0,
      currentFile: ''
    });

    try {
      const processResponse = await axios.post(`${BACKEND_BASE_URL}/process-files`, {
        selected_files: Array.from(selectedFiles),
        repo_id: repoId,
      });

      if (!processResponse.data.success) {
        throw new Error(processResponse.data.message || 'Failed to process files.');
      }

      // Start polling for status
      pollProcessingStatus(repoId);

    } catch (error) {
      console.error('Error processing files:', error);
      setStatusMessage(`Error: ${error.message}`);
      setApiState(API_STATUS.ERROR);
      setUiState('file-selection'); // Go back to selection on error
    }
  };

  // New function to poll processing status
  const pollProcessingStatus = async (repoId) => {
    if (!repoId) return;
    
    // Use a variable to track if we should continue polling
    let continuePolling = true;
    let pollCount = 0;
    const MAX_POLLS = 120; // Maximum number of polls (10 minutes at 5s intervals)
    
    while (continuePolling && pollCount < MAX_POLLS) {
      try {
        // Poll the status endpoint
        const statusResponse = await axios.get(`${BACKEND_BASE_URL}/processing-status?repo_id=${repoId}`);
        
        if (statusResponse.data.success) {
          // Extract the relevant data from the response
          const { status, message, is_complete, files_processed, total_files, partial_success } = statusResponse.data;
          
          // Update progress state
          const percentage = total_files > 0 
            ? Math.round((files_processed / total_files) * 100) 
            : 0;
            
          setProcessingProgress({
            total: total_files || 0,
            processed: files_processed || 0,
            percentage: percentage,
            currentFile: statusResponse.data.current_file || ''
          });
          
          // Update UI with current status
          setStatusMessage(`${message} (${files_processed || 0}/${total_files || 0})`);
          
          // Check if processing is complete, partial success, or had an error
          if (is_complete === true) {
            // Set progress to 100% when complete
            setProcessingProgress(prev => ({
              ...prev, 
              percentage: 100,
              processed: prev.total
            }));
            
            setApiState(API_STATUS.READY);
            
            // Customize message based on complete vs partial
            if (partial_success) {
              setStatusMessage(`Partial processing complete. ${files_processed}/${total_files} files analyzed.`);
              
              // If there are failed files, add them to the message
              if (statusResponse.data.failed_files && statusResponse.data.failed_files.length > 0) {
                console.log("Failed files:", statusResponse.data.failed_files);
              }
            } else {
              setStatusMessage(`Processing complete! ${total_files} files analyzed.`);
            }
            
            setChatHistory([
              { 
                sender: 'ai', 
                text: partial_success 
                  ? `I've analyzed ${files_processed} out of ${total_files} selected files from your codebase. Some files couldn't be processed, but we can still work with what we have. What would you like to know about the code structure, functions, or relationships?`
                  : `I've analyzed all ${total_files} selected files from your codebase. What would you like to know about the code structure, functions, relationships between components, or any specific implementation details?`
              }
            ]);
            setUiState('chat');
            continuePolling = false;
          } else if (status === 'error') {
            setApiState(API_STATUS.ERROR);
            setStatusMessage(`Error: ${message}`);
            continuePolling = false;
          }
        }
      } catch (error) {
        console.error('Error polling status:', error);
        // Don't stop polling on error, just continue
      }
      
      // Increment poll count
      pollCount++;
      
      // Wait 5 seconds before next poll if we're still polling
      if (continuePolling) {
        await new Promise(resolve => setTimeout(resolve, 5000));
      }
    }
    
    // If we reached max polls without completing
    if (pollCount >= MAX_POLLS && continuePolling) {
      setApiState(API_STATUS.ERROR);
      setStatusMessage('Processing timed out. Please try again or with fewer files.');
      setUiState('file-selection');
    }
  };

  const handleChatQuerySubmit = async (e) => {
    e.preventDefault();
    if (!chatQuery.trim() || isChatLoading) return;

    const userMessage = { sender: 'user', text: chatQuery };
    setChatHistory((prev) => [...prev, userMessage]);
    setChatQuery('');

    setIsChatLoading(true);

    try {
      const response = await axios.post(RAG_API_URL, { query: chatQuery });
      const aiMessage = { sender: 'ai', text: response.data.response };
      setChatHistory((prev) => [...prev, aiMessage]);
    } catch (error) {
      console.error('Error with RAG API:', error);
      const errorMessage = { sender: 'ai', text: `An error occurred: ${error.message}` };
      setChatHistory((prev) => [...prev, errorMessage]);
    } finally {
      setIsChatLoading(false);
    }
  };

  // Filter files based on search term
  const filteredFiles = fileSearchTerm
    ? files.filter(file => file.toLowerCase().includes(fileSearchTerm.toLowerCase()))
    : files;
  
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
  const renderFolderTree = (structure, path = "", indent = 0) => {
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
                {renderFolderTree(value, currentPath, indent + 1)}
              </ul>
            )}
          </li>
        );
      }
    });
  };

  // Main render logic
  const renderContent = () => {
    switch (uiState) {
      case 'welcome':
        return <WelcomeView 
          githubUrl={githubUrl}
          setGithubUrl={setGithubUrl}
          onSubmitRepo={handleSubmitRepo}
          apiState={apiState}
        />;
      case 'loading':
        return <LoadingView 
          apiState={apiState}
          statusMessage={statusMessage}
          processingProgress={processingProgress}
        />;
      case 'file-selection':
        return <FileSelectionView 
          files={files}
          selectedFiles={selectedFiles}
          setSelectedFiles={setSelectedFiles}
          fileSearchTerm={fileSearchTerm}
          setFileSearchTerm={setFileSearchTerm}
          onProcessSelectedFiles={handleProcessSelectedFiles}
          statusMessage={statusMessage}
        />;
      case 'chat':
        return <ChatView 
          chatHistory={chatHistory}
          chatQuery={chatQuery}
          setChatQuery={setChatQuery}
          onSubmitChatQuery={handleChatQuerySubmit}
          isChatLoading={isChatLoading}
          chatEndRef={chatEndRef}
        />;
      default:
        return <WelcomeView 
          githubUrl={githubUrl}
          setGithubUrl={setGithubUrl}
          onSubmitRepo={handleSubmitRepo}
          apiState={apiState}
        />;
    }
  };

  return (
    <div className="app-container">
      <Header 
        uiState={uiState} 
        onStartNew={handleStartNewClick} 
        isClearing={isClearing} 
      />
      
      <main className="main-content">
        {renderContent()}
      </main>
      
      {showConfirmDialog && (
        <div className="confirm-dialog">
          <div className="dialog-content">
            <h2 className="dialog-title">Start New Analysis?</h2>
            <p>
              This will clear all current data and conversation history. Are you sure you want to continue?
            </p>
            <div className="dialog-buttons">
              <button 
                className="btn dialog-cancel"
                onClick={handleCancelStartNew}
                disabled={isClearing}
              >
                Cancel
              </button>
              <button 
                className="btn dialog-confirm"
                onClick={handleConfirmStartNew}
                disabled={isClearing}
              >
                {isClearing ? 'Clearing...' : 'Yes, start new'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;