"""
Session Manager for CrewAI - manages conversation context and memory.
"""
from dataclasses import dataclass, field
from typing import Optional, Any
from datetime import datetime
from models.chat import UserPreferences


@dataclass
class Session:
    """Represents a chat session with memory and preferences."""

    id: str
    user_id: str
    history: list[dict] = field(default_factory=list)
    preferences: UserPreferences = field(default_factory=UserPreferences)
    context: dict = field(default_factory=dict)  # Long-term context
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)

    def add_message(self, role: str, content: str, metadata: Optional[dict] = None):
        """Add a message to the session history."""
        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        if metadata:
            message["metadata"] = metadata
        self.history.append(message)
        self.last_activity = datetime.now()

    def update_preferences(self, new_prefs: dict = None, **kwargs):
        """Update session preferences with new values.

        Can be called either with a dict or keyword arguments:
            session.update_preferences({"city": "Moscow"})
            session.update_preferences(city="Moscow", salary_from=50000)
        """
        prefs_to_update = new_prefs or {}
        prefs_to_update.update(kwargs)

        for key, value in prefs_to_update.items():
            if value is not None and hasattr(self.preferences, key):
                setattr(self.preferences, key, value)

    def get_context_summary(self) -> str:
        """Get a brief summary of the current search context."""
        parts = []
        if self.preferences.query:
            parts.append(f"Ищет: {self.preferences.query}")
        if self.preferences.city:
            parts.append(f"Город: {self.preferences.city}")
        if self.preferences.salary_from:
            parts.append(f"ЗП от: {self.preferences.salary_from:,}₽".replace(",", " "))
        if self.preferences.experience:
            exp_map = {
                "no_experience": "без опыта",
                "1-3": "1-3 года",
                "3-6": "3-6 лет",
                "6+": "более 6 лет"
            }
            parts.append(f"Опыт: {exp_map.get(self.preferences.experience, self.preferences.experience)}")
        if self.preferences.employment_type:
            type_map = {
                "full": "полная занятость",
                "part": "частичная занятость",
                "remote": "удалённая работа"
            }
            parts.append(f"Формат: {type_map.get(self.preferences.employment_type, self.preferences.employment_type)}")
        return ", ".join(parts) if parts else "Новый поиск"

    def get_recent_history(self, max_messages: int = 10, limit: int = None) -> list[dict]:
        """Get recent conversation history for context."""
        count = limit if limit is not None else max_messages
        return self.history[-count:] if self.history else []

    def clear_preferences(self):
        """Clear all search preferences for a new search."""
        self.preferences = UserPreferences()

    def set_context(self, key: str, value: Any):
        """Set a context value."""
        self.context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        """Get a context value."""
        return self.context.get(key, default)


class SessionManager:
    """Manages multiple user sessions."""

    def __init__(self):
        self.sessions: dict[str, Session] = {}
        self._max_session_age_hours = 24  # Sessions expire after 24 hours

    def get_or_create_session(
        self,
        user_id: str,
        chat_id: Optional[str] = None
    ) -> Session:
        """Get an existing session or create a new one."""
        session_key = f"{user_id}_{chat_id or 'default'}"

        if session_key in self.sessions:
            session = self.sessions[session_key]
            # Check if session is still valid
            age = datetime.now() - session.last_activity
            if age.total_seconds() > self._max_session_age_hours * 3600:
                # Session expired, create a new one
                self.sessions[session_key] = Session(
                    id=session_key,
                    user_id=user_id,
                )
        else:
            self.sessions[session_key] = Session(
                id=session_key,
                user_id=user_id,
            )

        return self.sessions[session_key]

    def get_session(self, user_id: str, chat_id: Optional[str] = None) -> Optional[Session]:
        """Get a session if it exists."""
        session_key = f"{user_id}_{chat_id or 'default'}"
        return self.sessions.get(session_key)

    def delete_session(self, user_id: str, chat_id: Optional[str] = None):
        """Delete a session."""
        session_key = f"{user_id}_{chat_id or 'default'}"
        if session_key in self.sessions:
            del self.sessions[session_key]

    def cleanup_expired_sessions(self):
        """Remove expired sessions."""
        now = datetime.now()
        expired_keys = []

        for key, session in self.sessions.items():
            age = now - session.last_activity
            if age.total_seconds() > self._max_session_age_hours * 3600:
                expired_keys.append(key)

        for key in expired_keys:
            del self.sessions[key]

        return len(expired_keys)


# Global session manager instance
session_manager = SessionManager()
