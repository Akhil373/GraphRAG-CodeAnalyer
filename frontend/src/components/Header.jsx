import React from 'react';
import ThemeToggle from './ThemeToggle';

const Header = ({ uiState, onStartNew, isClearing }) => (
  <header className={`app-header`}>
    <div className="logo-container">
      <h1 className="logo-text">Analyo</h1>
    </div>
    <div className="header-actions">
      {uiState === 'chat' && (
        <button 
          className="btn btn-secondary" 
          onClick={onStartNew}
          disabled={isClearing}
        >
          Start New
        </button>
      )}
      <ThemeToggle />
    </div>
  </header>
);

export default Header; 