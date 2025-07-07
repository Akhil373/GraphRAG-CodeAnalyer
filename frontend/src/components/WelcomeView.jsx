import React from 'react';

const WelcomeView = ({ githubUrl, setGithubUrl, onSubmitRepo, apiState }) => (
  <div className="welcome-container">
    <h1 className="welcome-title">Welcome to <span className='logo-text' style={{"fontSize" : "inherit"}}>Analyo</span></h1>
    <p className="welcome-subtitle">
      Analyze, understand, and explore your codebase with AI assistance.
      Enter a GitHub repository URL below to get started.
    </p>
    <form className="repo-form" onSubmit={onSubmitRepo}>
      <div className="repo-input-container">
        <input
          type="text"
          className="repo-input"
          value={githubUrl}
          onChange={(e) => setGithubUrl(e.target.value)}
          placeholder="Enter GitHub repository URL"
          aria-label="GitHub repository URL"
          disabled={apiState !== 'idle'}
        />
      </div>
      <button 
        type="submit" 
        className="btn btn-primary w-full"
        disabled={apiState !== 'idle' || !githubUrl.trim()}
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style={{ marginRight: '0.5rem' }}>
          <path d="M21 12L13 4V9C7 10 4 15 3 20C5.5 16.5 9 14.9 13 14.9V20L21 12Z" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
        </svg>
        Analyze Repository
      </button>
    </form>
  </div>
);

export default WelcomeView; 