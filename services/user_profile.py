"""
Сервис для работы с профилем и резюме пользователя
"""

import httpx
from typing import Optional
from pydantic import BaseModel
from config import get_settings


class UserProfile(BaseModel):
    """Профиль пользователя"""
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    patronymic: Optional[str] = None
    phone: Optional[str] = None
    city: Optional[str] = None
    birth_date: Optional[str] = None
    email: Optional[str] = None


class WorkExperience(BaseModel):
    """Опыт работы"""
    company: Optional[str] = None
    position: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    is_current: bool = False
    description: Optional[str] = None


class Education(BaseModel):
    """Образование"""
    institution: Optional[str] = None
    degree: Optional[str] = None
    field: Optional[str] = None
    start_year: Optional[str] = None
    end_year: Optional[str] = None


class UserResume(BaseModel):
    """Резюме пользователя"""
    desired_position: Optional[str] = None
    desired_salary: Optional[str] = None
    skills: Optional[str] = None
    about: Optional[str] = None
    work_experience: list[WorkExperience] = []
    education: list[Education] = []


class UserData(BaseModel):
    """Полные данные пользователя"""
    profile: Optional[UserProfile] = None
    resume: Optional[UserResume] = None


class UserProfileService:
    """Сервис для получения данных пользователя из Supabase"""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.supabase_url
        self.api_key = self.settings.supabase_key

    async def get_user_data(self, user_id: str) -> UserData:
        """Получить профиль и резюме пользователя"""
        profile = await self._get_profile(user_id)
        resume = await self._get_resume(user_id)

        return UserData(profile=profile, resume=resume)

    async def _get_profile(self, user_id: str) -> Optional[UserProfile]:
        """Получить профиль из таблицы profiles"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/profiles",
                    params={"user_id": f"eq.{user_id}", "select": "*"},
                    headers={
                        "apikey": self.api_key,
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data and len(data) > 0:
                        row = data[0]
                        return UserProfile(
                            first_name=row.get("first_name"),
                            last_name=row.get("last_name"),
                            patronymic=row.get("patronymic"),
                            phone=row.get("phone"),
                            city=row.get("city"),
                            birth_date=row.get("birth_date"),
                        )
        except Exception as e:
            print(f"Error fetching profile: {e}")

        return None

    async def _get_resume(self, user_id: str) -> Optional[UserResume]:
        """Получить резюме из таблицы resumes"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/rest/v1/resumes",
                    params={"user_id": f"eq.{user_id}", "select": "*"},
                    headers={
                        "apikey": self.api_key,
                        "Authorization": f"Bearer {self.api_key}",
                    },
                    timeout=10.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    if data and len(data) > 0:
                        row = data[0]

                        # Парсим опыт работы
                        work_exp = []
                        for w in row.get("work_experience") or []:
                            work_exp.append(WorkExperience(
                                company=w.get("company"),
                                position=w.get("position"),
                                start_date=w.get("start_date"),
                                end_date=w.get("end_date"),
                                is_current=w.get("is_current", False),
                                description=w.get("description"),
                            ))

                        # Парсим образование
                        edu = []
                        for e in row.get("education") or []:
                            edu.append(Education(
                                institution=e.get("institution"),
                                degree=e.get("degree"),
                                field=e.get("field"),
                                start_year=e.get("start_year"),
                                end_year=e.get("end_year"),
                            ))

                        return UserResume(
                            desired_position=row.get("desired_position"),
                            desired_salary=row.get("desired_salary"),
                            skills=row.get("skills"),
                            about=row.get("about"),
                            work_experience=work_exp,
                            education=edu,
                        )
        except Exception as e:
            print(f"Error fetching resume: {e}")

        return None


# Singleton
user_profile_service = UserProfileService()
