"""
Session management for multi-user support.

This module provides simple session management without passwords,
suitable for secure network environments.
"""

import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any
import logging
import threading

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages user sessions with SQLite backend."""
    
    def __init__(self, db_path: str = "sessions.db"):
        """Initialize session manager.
        
        Args:
            db_path: Path to SQLite database file (created locally, not tracked in git)
        """
        self.db_path = Path(db_path)
        self.session_data: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._init_db()
    
    def _init_db(self):
        """Initialize the SQLite database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    data_prefix TEXT DEFAULT NULL,
                    UNIQUE(username)
                )
            """)
            conn.commit()
        logger.info(f"Session database initialized at {self.db_path}")
    
    def create_session(self, username: str, data_prefix: str = None) -> str:
        """Create or retrieve a session for a user.
        
        Args:
            username: Unique username
            data_prefix: Optional initial dataset prefix
            
        Returns:
            Session ID
        """
        with self._lock:
            # Check if user already has a session
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT session_id, data_prefix FROM sessions WHERE username = ?",
                    (username,)
                )
                result = cursor.fetchone()
                
                if result:
                    session_id, existing_prefix = result
                    # Update last accessed time
                    conn.execute(
                        "UPDATE sessions SET last_accessed = CURRENT_TIMESTAMP WHERE session_id = ?",
                        (session_id,)
                    )
                    # Use existing data_prefix if not provided
                    if data_prefix is None:
                        data_prefix = existing_prefix
                    logger.info(f"Retrieved existing session {session_id} for user {username}")
                else:
                    # Create new session
                    session_id = str(uuid.uuid4())
                    conn.execute(
                        "INSERT INTO sessions (session_id, username, data_prefix) VALUES (?, ?, ?)",
                        (session_id, username, data_prefix)
                    )
                    logger.info(f"Created new session {session_id} for user {username}")
                
                conn.commit()
            
            # Initialize session data cache
            if session_id not in self.session_data:
                self.session_data[session_id] = {
                    "data_prefix": data_prefix,
                    "df_cache": {
                        "data": None,
                        "last_loaded": None,
                        "target_key": None,
                        "processing_manifest": None,
                        "spot_channels_from_manifest": None,
                        "sankey_data": None
                    }
                }
            
            return session_id
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data.
        
        Args:
            session_id: Session ID
            
        Returns:
            Session data dictionary or None if not found
        """
        with self._lock:
            # Verify session exists in database
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    "SELECT username, data_prefix FROM sessions WHERE session_id = ?",
                    (session_id,)
                )
                result = cursor.fetchone()
                
                if not result:
                    return None
                
                username, data_prefix = result
                
                # Update last accessed
                conn.execute(
                    "UPDATE sessions SET last_accessed = CURRENT_TIMESTAMP WHERE session_id = ?",
                    (session_id,)
                )
                conn.commit()
            
            # Ensure session data exists in memory
            if session_id not in self.session_data:
                self.session_data[session_id] = {
                    "data_prefix": data_prefix,
                    "df_cache": {
                        "data": None,
                        "last_loaded": None,
                        "target_key": None,
                        "processing_manifest": None,
                        "spot_channels_from_manifest": None,
                        "sankey_data": None
                    }
                }
            
            return {
                "session_id": session_id,
                "username": username,
                "data_prefix": data_prefix,
                **self.session_data[session_id]
            }
    
    def update_session_data_prefix(self, session_id: str, data_prefix: str):
        """Update the data prefix for a session.
        
        Args:
            session_id: Session ID
            data_prefix: New data prefix
        """
        with self._lock:
            # Update database
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE sessions SET data_prefix = ?, last_accessed = CURRENT_TIMESTAMP WHERE session_id = ?",
                    (data_prefix, session_id)
                )
                conn.commit()
            
            # Update memory cache
            if session_id in self.session_data:
                self.session_data[session_id]["data_prefix"] = data_prefix
                # Clear cached data when dataset changes
                self.session_data[session_id]["df_cache"] = {
                    "data": None,
                    "last_loaded": None,
                    "target_key": None,
                    "processing_manifest": None,
                    "spot_channels_from_manifest": None,
                    "sankey_data": None
                }
    
    def get_session_cache(self, session_id: str) -> Dict[str, Any]:
        """Get the DataFrame cache for a session.
        
        Args:
            session_id: Session ID
            
        Returns:
            Cache dictionary
        """
        with self._lock:
            if session_id not in self.session_data:
                return {
                    "data": None,
                    "last_loaded": None,
                    "target_key": None,
                    "processing_manifest": None,
                    "spot_channels_from_manifest": None,
                    "sankey_data": None
                }
            return self.session_data[session_id]["df_cache"]
    
    def update_session_cache(self, session_id: str, cache_data: Dict[str, Any]):
        """Update the DataFrame cache for a session.
        
        Args:
            session_id: Session ID
            cache_data: Cache data to update
        """
        with self._lock:
            if session_id not in self.session_data:
                self.session_data[session_id] = {
                    "data_prefix": None,
                    "df_cache": {}
                }
            
            self.session_data[session_id]["df_cache"].update(cache_data)
    
    def cleanup_old_sessions(self, max_age_hours: int = 24):
        """Remove sessions older than max_age_hours.
        
        Args:
            max_age_hours: Maximum age in hours
        """
        with self._lock:
            cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
            
            with sqlite3.connect(self.db_path) as conn:
                # Get sessions to remove
                cursor = conn.execute(
                    "SELECT session_id FROM sessions WHERE last_accessed < ?",
                    (cutoff_time,)
                )
                old_sessions = [row[0] for row in cursor.fetchall()]
                
                # Remove from database
                conn.execute(
                    "DELETE FROM sessions WHERE last_accessed < ?",
                    (cutoff_time,)
                )
                conn.commit()
                
                # Remove from memory
                for session_id in old_sessions:
                    if session_id in self.session_data:
                        del self.session_data[session_id]
                
                logger.info(f"Cleaned up {len(old_sessions)} old sessions")
    
    def list_active_sessions(self) -> list:
        """List all active sessions.
        
        Returns:
            List of session info dictionaries
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT session_id, username, created_at, last_accessed, data_prefix
                FROM sessions
                ORDER BY last_accessed DESC
            """)
            
            sessions = []
            for row in cursor.fetchall():
                sessions.append({
                    "session_id": row[0],
                    "username": row[1],
                    "created_at": row[2],
                    "last_accessed": row[3],
                    "data_prefix": row[4]
                })
            
            return sessions


# Global session manager instance
session_manager = SessionManager()