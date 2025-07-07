import React from 'react';

const ChatMessage = ({ message }) => {
  const formatMessage = (text) => {
    if (!text) return '';
    
    const parts = text.split(/```([^`]+)```/);
    if (parts.length === 1) return text;
    
    return parts.map((part, i) => {
      if (i % 2 === 0) {
        return part.split('\n').filter(p => p.trim()).map((p, idx) => (
          <p key={`${i}-${idx}`}>{p}</p>
        ));
      } else {
        return (
          <pre key={i}>
            <code>{part}</code>
          </pre>
        );
      }
    });
  };

  return (
    <div className={`chat-message ${message.sender === 'user' ? 'user-message' : 'ai-message'}`}>
      {formatMessage(message.text)}
      <span className="message-time">
        {new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </span>
    </div>
  );
};

export default ChatMessage; 