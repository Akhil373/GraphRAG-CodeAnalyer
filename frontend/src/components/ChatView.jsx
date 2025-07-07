import React from 'react';
import ChatMessage from './ChatMessage';

const ChatView = ({ 
  chatHistory, 
  chatQuery, 
  setChatQuery, 
  onSubmitChatQuery, 
  isChatLoading,
  chatEndRef
}) => {
  // Function to format code blocks
  const formatMessage = (text) => {
    if (!text) return '';
    
    // Split by code blocks
    const parts = text.split(/```([^`]+)```/);
    if (parts.length === 1) return text; // No code blocks
    
    const result = [];
    for (let i = 0; i < parts.length; i++) {
      if (i % 2 === 0) {
        // Regular text - split by new lines to create proper paragraphs
        if (parts[i].trim()) {
          const paragraphs = parts[i].split('\n').filter(p => p.trim());
          paragraphs.forEach((p, idx) => {
            result.push(<p key={`${i}-${idx}`}>{p}</p>);
          });
        }
      } else {
        // Code block
        result.push(
          <pre key={i}>
            <code>{parts[i]}</code>
          </pre>
        );
      }
    }
    
    return result.length > 0 ? result : text;
  };

  return (
    <div className="chat-container">
      <div className="chat-history">
        {chatHistory.length === 0 ? (
          <div className="card text-center" style={{margin: '2rem auto', maxWidth: '600px', padding: '2rem'}}>
            <svg width="64" height="64" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" style={{margin: '0 auto 1rem'}}>
              <path d="M21 12C21 16.9706 16.9706 21 12 21C7.02944 21 3 16.9706 3 12C3 7.02944 7.02944 3 12 3C16.9706 3 21 7.02944 21 12Z" stroke="currentColor" strokeWidth="2"/>
              <path d="M12 8V12L14.5 14.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              <path d="M9 17C9.85038 17.3756 10.8846 17.5 12 17.5C13.1154 17.5 14.1496 17.3756 15 17" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
            </svg>
            <h3 className="mb-2">Start Your Conversation</h3>
            <p>Ask questions about your code's structure, functions, and relationships to get detailed insights.</p>
          </div>
        ) : (
          chatHistory.map((msg, index) => (
            <ChatMessage key={index} message={msg} />
          ))
        )}
        {isChatLoading && (
          <div className="chat-message ai-message">
            <div className="typing">
              <span></span>
              <span></span>
              <span></span>
              <span></span>
              <span></span>
            </div>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>
      
      <div className="chat-input-container">
        <form onSubmit={onSubmitChatQuery} className="chat-form">
          <input
            type="text"
            className="chat-input"
            value={chatQuery}
            onChange={(e) => setChatQuery(e.target.value)}
            placeholder="Ask about your code structure, functions, relationships..."
            disabled={isChatLoading}
          />
          <button 
            type="submit" 
            className="chat-submit-btn"
            disabled={isChatLoading || !chatQuery.trim()}
            aria-label="Send message"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M22 2L11 13M22 2L15 22L11 13M22 2L2 9L11 13" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
            </svg>
          </button>
        </form>
      </div>
    </div>
  );
};

export default ChatView; 