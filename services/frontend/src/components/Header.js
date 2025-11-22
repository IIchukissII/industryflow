import React from 'react';
import './Header.css';

function Header({ user, connected }) {
  const handleLogout = () => {
    localStorage.removeItem('access_token');
    localStorage.removeItem('user');
    window.location.href = '/login';
  };

  const linkStyle = {
    color: '#787b86',
    textDecoration: 'none',
    fontSize: '14px',
    padding: '8px 16px',
    borderRadius: '4px',
    border: '1px solid #2a2e39',
    transition: 'all 0.2s'
  };

  return (
    <header className="App-header">
      <div style={{ display: 'flex', alignItems: 'center', gap: '30px' }}>
        <h1>IndustryFlow</h1>
        <a href="/" style={linkStyle}
          onMouseOver={(e) => {
            e.target.style.color = '#2962ff';
            e.target.style.borderColor = '#2962ff';
          }}
          onMouseOut={(e) => {
            e.target.style.color = '#787b86';
            e.target.style.borderColor = '#2a2e39';
          }}>
          Dashboard
        </a>
        <a href="/alerts" style={linkStyle}
          onMouseOver={(e) => {
            e.target.style.color = '#2962ff';
            e.target.style.borderColor = '#2962ff';
          }}
          onMouseOut={(e) => {
            e.target.style.color = '#787b86';
            e.target.style.borderColor = '#2a2e39';
          }}>
          Alerts
        </a>
        <a href="/alert-rules" style={linkStyle}
          onMouseOver={(e) => {
            e.target.style.color = '#2962ff';
            e.target.style.borderColor = '#2962ff';
          }}
          onMouseOut={(e) => {
            e.target.style.color = '#787b86';
            e.target.style.borderColor = '#2a2e39';
          }}>
          Alert Rules
        </a>
        <a href="/equipment" style={linkStyle}
          onMouseOver={(e) => {
            e.target.style.color = '#2962ff';
            e.target.style.borderColor = '#2962ff';
          }}
          onMouseOut={(e) => {
            e.target.style.color = '#787b86';
            e.target.style.borderColor = '#2a2e39';
          }}>
          Equipment
        </a>
        <a href="/ml-models" style={linkStyle}
          onMouseOver={(e) => {
            e.target.style.color = '#2962ff';
            e.target.style.borderColor = '#2962ff';
          }}
          onMouseOut={(e) => {
            e.target.style.color = '#787b86';
            e.target.style.borderColor = '#2a2e39';
          }}>
          ML Models
        </a>
        {user && user.is_superuser && (
          <a href="/admin" style={linkStyle}
            onMouseOver={(e) => {
              e.target.style.color = '#2962ff';
              e.target.style.borderColor = '#2962ff';
            }}
            onMouseOut={(e) => {
              e.target.style.color = '#787b86';
              e.target.style.borderColor = '#2a2e39';
            }}>
            ⚙️ Admin
          </a>
        )}
      </div>
      <div className="status">
        <span style={{ marginRight: '20px', color: '#d1d4dc' }}>
          👤 {user ? user.email : 'Guest'}
        </span>
        <a href="/settings" style={{
          ...linkStyle,
          marginRight: '15px',
          padding: '6px 12px'
        }}
          onMouseOver={(e) => {
            e.target.style.color = '#2962ff';
            e.target.style.borderColor = '#2962ff';
          }}
          onMouseOut={(e) => {
            e.target.style.color = '#787b86';
            e.target.style.borderColor = '#2a2e39';
          }}>
          ⚙️ Settings
        </a>
        API: <span className={connected ? 'connected' : 'disconnected'}>
          {connected ? '● Connected' : '● Disconnected'}
        </span>
        <button
          onClick={handleLogout}
          style={{
            marginLeft: '20px',
            background: 'transparent',
            border: '1px solid #2a2e39',
            color: '#787b86',
            padding: '6px 12px',
            borderRadius: '4px',
            cursor: 'pointer',
            fontSize: '13px',
            transition: 'all 0.2s'
          }}
          onMouseOver={(e) => {
            e.target.style.borderColor = '#ef5350';
            e.target.style.color = '#ef5350';
          }}
          onMouseOut={(e) => {
            e.target.style.borderColor = '#2a2e39';
            e.target.style.color = '#787b86';
          }}
        >
          Logout
        </button>
      </div>
    </header>
  );
}

export default Header;
