"""Answer parsers for incident agent phases."""

import re

from lyoko.domain.models.incident import Diagnosis

_ROOT_CAUSE_RE = re.compile(r"^ROOT_CAUSE:\s*(.*?)(?=^ACTIONABLE:|\Z)", re.S | re.M | re.I)
_ACTIONABLE_RE = re.compile(r"^ACTIONABLE:\s*(yes|no)\b", re.M | re.I)
_PLAN_RE = re.compile(r"^PLAN:\s*(.*)\Z", re.S | re.M | re.I)
_RESULT_RE = re.compile(r"^RESULT:\s*(.*)\Z", re.S | re.M | re.I)
_VERDICT_RE = re.compile(r"\W*(RESOLVED|UNRESOLVED)\b[\s:.*\-]*(.*)", re.S | re.I)


class IncidentOutputParser:
    """Parses output from incident investigation, remediation, and verification runs."""

    @staticmethod
    def parse_diagnosis(text: str) -> Diagnosis:
        """Parse the diagnose answer. Anything unparseable is treated as not actionable."""
        root_cause_match = _ROOT_CAUSE_RE.search(text)
        actionable_match = _ACTIONABLE_RE.search(text)
        plan_match = _PLAN_RE.search(text)

        root_cause = (
            root_cause_match.group(1).strip() if root_cause_match else ""
        ) or text.strip()[:500]
        actionable = bool(actionable_match and actionable_match.group(1).lower() == "yes")
        plan = plan_match.group(1).strip() if plan_match else ""
        return Diagnosis(root_cause=root_cause, actionable=actionable and bool(plan), plan=plan)

    @staticmethod
    def parse_result(text: str) -> str:
        """Return the ``RESULT:`` summary of a remediation, or the whole answer if it is missing."""
        match = _RESULT_RE.search(text)
        return (match.group(1) if match else text).strip()

    @staticmethod
    def parse_verdict(text: str) -> tuple[bool, str]:
        """Parse the verify answer into ``(resolved, evidence)``. Unclear answers are unresolved."""
        stripped = text.strip()
        match = _VERDICT_RE.match(stripped)
        if match is None:
            return False, stripped
        return match.group(1).upper() == "RESOLVED", match.group(2).strip()
