"""
Lifestyle matcher for vacancy descriptions.
Determines whether a vacancy matches selected work-style preferences.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LifestyleMatchResult:
    fit_status: str = "unknown"  # fit | partial | reject | unknown
    fit_score: int = 0
    fit_confidence: float = 0.35
    matched_reasons: list[str] = field(default_factory=list)
    mismatch_reasons: list[str] = field(default_factory=list)


class LifestyleMatcher:
    """Keyword-based matcher for work-style preferences."""

    REMOTE_POSITIVE = (
        "полная удал",
        "только удал",
        "remote",
        "remote-first",
        "remote first",
        "удаленный формат",
        "удалённый формат",
        "дистанцион",
        "работа из дома",
        "home office",
    )
    REMOTE_NEGATIVE = (
        "гибрид",
        "гибридный",
        "в офисе",
        "офисный формат",
        "посещение офиса",
        "работа из офиса",
        "офлайн формат",
        "на месте работодателя",
    )
    REMOTE_NEGATIVE_SOFT = (
        "офис",
    )
    REMOTE_OFFICE_EXCEPTIONS = (
        "home office",
        "домашний офис",
        "компенсация домашнего офиса",
    )

    NO_CALLS_POSITIVE = (
        "без созвон",
        "без звонков",
        "без обязательных созвон",
        "только переписка",
        "коммуникация в чате",
        "без митингов",
    )
    NO_CALLS_NEGATIVE = (
        "обязательные созвон",
        "ежедневные созвон",
        "регулярные созвон",
        "дейли",
        "daily standup",
        "стендап",
        "митинг",
        "zoom",
        "google meet",
        "видеозвон",
    )
    NO_CALLS_SOFT = (
        "созвоны по необходимости",
        "созвон по необходимости",
        "редкие созвоны",
        "митинги по необходимости",
    )

    ASYNC_POSITIVE = (
        "асинхрон",
        "async",
        "async-first",
        "async first",
        "асинхронная коммуникация",
        "в удобное время",
        "коммуникация в таск-трекере",
        "коммуникация в slack",
    )
    ASYNC_NEGATIVE = (
        "онлайн весь день",
        "постоянно на связи",
        "оперативные созвон",
        "работа в реальном времени",
        "быть на связи в рабочие часы",
    )

    FLEX_POSITIVE = (
        "гибкий график",
        "свободный график",
        "гибкие часы",
        "можно выбирать часы",
        "4-днев",
        "четырехднев",
    )
    FLEX_NEGATIVE = (
        "строгий график",
        "фиксированный график",
        "5/2",
        "с 9:00",
        "с 10:00",
        "с 8:00",
        "по графику смен",
    )

    @staticmethod
    def _to_text(vacancy: Any) -> str:
        title = str(getattr(vacancy, "title", "") or "")
        description = str(getattr(vacancy, "description", "") or "")
        company = str(getattr(vacancy, "company", "") or "")
        return f"{title}\n{company}\n{description}".lower()

    @staticmethod
    def has_active_preferences(preferences: dict | None) -> bool:
        if not preferences:
            return False
        return any(
            bool(preferences.get(key, False))
            for key in (
                "full_remote_only",
                "no_mandatory_calls",
                "async_first",
                "flexible_hours",
            )
        )

    @staticmethod
    def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
        return any(needle in text for needle in needles)

    def _has_remote_negative_signal(self, text: str) -> bool:
        """Detect strong onsite/hybrid signals while avoiding false positives."""
        if self._contains_any(text, self.REMOTE_NEGATIVE):
            return True

        # Bare 'офис' is too noisy; ignore if it appears in remote/home-office contexts
        if self._contains_any(text, self.REMOTE_NEGATIVE_SOFT):
            if self._contains_any(text, self.REMOTE_OFFICE_EXCEPTIONS):
                return False
            # treat as strong negative only when paired with common onsite phrases
            if any(phrase in text for phrase in ("работа в офис", "в офис", "офис 5/2", "гибрид")):
                return True
        return False

    def _has_no_calls_hard_negative(self, text: str) -> bool:
        """Detect mandatory-call signals while allowing optional calls."""
        if self._contains_any(text, self.NO_CALLS_SOFT):
            return False
        return self._contains_any(text, self.NO_CALLS_NEGATIVE)

    def evaluate(self, vacancy: Any, preferences: dict | None) -> LifestyleMatchResult:
        """
        Evaluate vacancy against selected work-style preferences.
        """
        if not self.has_active_preferences(preferences):
            return LifestyleMatchResult(
                fit_status="fit",
                fit_score=100,
                fit_confidence=0.2,
            )

        text = self._to_text(vacancy)
        employment_type = str(getattr(vacancy, "employment_type", "") or "").lower()

        checks_total = 0
        checks_matched = 0
        checks_mismatched = 0
        checks_unknown = 0
        matched_reasons: list[str] = []
        mismatch_reasons: list[str] = []

        # 1) Full remote only
        if preferences.get("full_remote_only"):
            checks_total += 1
            is_remote = employment_type == "remote" or self._contains_any(text, self.REMOTE_POSITIVE)
            has_onsite_signals = self._has_remote_negative_signal(text)

            if is_remote and not has_onsite_signals:
                checks_matched += 1
                matched_reasons.append("Подтверждена полная удаленка.")
            elif has_onsite_signals:
                checks_mismatched += 1
                mismatch_reasons.append("Есть признаки офисного или гибридного формата.")
            else:
                checks_unknown += 1
                mismatch_reasons.append("Не удалось подтвердить полную удаленку.")

        # 2) No mandatory calls
        if preferences.get("no_mandatory_calls"):
            checks_total += 1
            has_no_calls_signal = self._contains_any(text, self.NO_CALLS_POSITIVE)
            has_calls_signal = self._has_no_calls_hard_negative(text)
            has_soft_calls_signal = self._contains_any(text, self.NO_CALLS_SOFT)

            if has_no_calls_signal and not has_calls_signal:
                checks_matched += 1
                matched_reasons.append("В описании нет обязательных созвонов.")
            elif has_soft_calls_signal and not has_calls_signal:
                checks_unknown += 1
                mismatch_reasons.append("Созвоны возможны по необходимости, но не обязательны.")
            elif has_calls_signal:
                checks_mismatched += 1
                mismatch_reasons.append("Упомянуты обязательные созвоны/митинги.")
            else:
                checks_unknown += 1
                mismatch_reasons.append("Неясно, есть ли обязательные созвоны.")

        # 3) Async first
        if preferences.get("async_first"):
            checks_total += 1
            has_async_signal = self._contains_any(text, self.ASYNC_POSITIVE)
            has_sync_signal = self._contains_any(text, self.ASYNC_NEGATIVE)

            if has_async_signal and not has_sync_signal:
                checks_matched += 1
                matched_reasons.append("Асинхронный формат явно обозначен.")
            elif has_sync_signal:
                checks_mismatched += 1
                mismatch_reasons.append("Нужна постоянная синхронная коммуникация.")
            else:
                checks_unknown += 1
                mismatch_reasons.append("Асинхронный формат явно не указан.")

        # 4) Flexible hours
        if preferences.get("flexible_hours"):
            checks_total += 1
            has_flex_signal = self._contains_any(text, self.FLEX_POSITIVE)
            has_fixed_signal = self._contains_any(text, self.FLEX_NEGATIVE)

            if has_flex_signal and not has_fixed_signal:
                checks_matched += 1
                matched_reasons.append("Указан гибкий график.")
            elif has_fixed_signal:
                checks_mismatched += 1
                mismatch_reasons.append("Указан фиксированный график.")
            else:
                checks_unknown += 1
                mismatch_reasons.append("Гибкость графика не подтверждена.")

        # Status
        if checks_mismatched > 0 and checks_matched == 0:
            fit_status = "reject"
        elif checks_mismatched == 0 and checks_unknown == 0:
            fit_status = "fit"
        elif checks_matched > 0:
            fit_status = "partial"
        else:
            fit_status = "unknown"

        # Score (0..100)
        if checks_total == 0:
            fit_score = 100
        else:
            known_checks = checks_total - checks_unknown
            if known_checks <= 0:
                fit_score = 40
            else:
                fit_score = round((checks_matched / known_checks) * 100)
                fit_score -= checks_unknown * 10
            if fit_status == "reject":
                fit_score = min(fit_score, 25)
            fit_score = max(0, min(100, fit_score))

        # Confidence (0..1)
        evidence_points = checks_matched + checks_mismatched
        if evidence_points == 0:
            fit_confidence = 0.35
        else:
            fit_confidence = min(0.95, 0.45 + evidence_points * 0.12 + checks_matched * 0.03)

        return LifestyleMatchResult(
            fit_status=fit_status,
            fit_score=fit_score,
            fit_confidence=round(fit_confidence, 2),
            matched_reasons=matched_reasons[:4],
            mismatch_reasons=mismatch_reasons[:4],
        )


lifestyle_matcher = LifestyleMatcher()
