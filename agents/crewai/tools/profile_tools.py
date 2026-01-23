"""
CrewAI Tools for user profile operations.
"""
import asyncio
from typing import Optional
from crewai_tools import BaseTool


class UserProfileTool(BaseTool):
    """
    Tool for getting user profile and resume information.
    """

    name: str = "get_user_profile"
    description: str = """
    Получить профиль и резюме пользователя.

    Возвращает:
    - Имя пользователя
    - Город из профиля
    - Желаемая должность (из резюме)
    - Желаемая зарплата (из резюме)
    - Навыки
    - Опыт работы
    """

    _user_id: Optional[str] = None

    def set_user_id(self, user_id: str):
        """Set the user ID for profile lookup."""
        self._user_id = user_id

    def _run(self, user_id: Optional[str] = None) -> dict:
        """Get user profile data."""
        from services.user_profile import user_profile_service

        target_user_id = user_id or self._user_id
        if not target_user_id:
            return {"error": "No user_id provided"}

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        user_profile_service.get_user_data(target_user_id)
                    )
                    data = future.result(timeout=10)
            else:
                data = asyncio.run(user_profile_service.get_user_data(target_user_id))

            if data:
                return data.model_dump(mode='json')
            return {}

        except Exception as e:
            print(f"[UserProfileTool] Error: {e}")
            return {"error": str(e)}


# Tool instance
user_profile_tool = UserProfileTool()
